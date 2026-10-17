"""Receipts reuse original document identities; only Worker writes render outcomes."""

from __future__ import annotations

import re
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, clean, enqueue_plan, fetch, sha
from packages.word_intake.inspection import DOCX, PDF, filename_type, public_report

REQUIRED_ISOLATION = {
    "local_image_pinned",
    "network_none",
    "ipc_none",
    "pid_private",
    "uts_private",
    "nonroot",
    "read_only",
    "not_privileged",
    "capabilities_dropped",
    "no_new_privileges",
    "memory_limited",
    "cpu_limited",
    "pids_limited",
    "no_daemon_document_logs",
    "clean_environment",
    "all_bind_mounts_readonly",
    "exact_allowlisted_bind_mounts",
    "no_anonymous_persistent_volumes",
    "image_data_volume_replaced_by_bounded_inaccessible_tmpfs",
    "no_docker_socket_mount",
    "bounded_tmpfs_output",
    "own_job_cleanup_confirmed",
}


def assert_file_format(c, file_id, media_type):
    current = c.execute(
        "SELECT b.media_type FROM document_files f JOIN document_versions v ON v.id=f.head_version_id JOIN blobs b ON b.id=v.blob_id WHERE f.id=%s",
        (file_id,),
    ).fetchone()
    if current and current["media_type"] != media_type:
        raise DomainError(
            "FILE_FORMAT_MISMATCH", "DOCX与PDF须使用不同逻辑文件，请选择新建文件", 409
        )
    pending = c.execute(
        "SELECT u.filename FROM uploads u JOIN upload_transfers t ON t.upload_id=u.id JOIN upload_bindings x ON x.id=t.current_binding_id WHERE x.file_id=%s AND t.aborted_at IS NULL AND u.state IN ('authorized','uploaded','verifying','verified')",
        (file_id,),
    ).fetchall()
    if any(filename_type(row["filename"]) != media_type for row in pending):
        raise DomainError(
            "FILE_FORMAT_MISMATCH", "该逻辑文件已有不同格式的原件或在途上传，请新建PDF文件", 409
        )


def declare_pdf_source(c, bid, upload, source, actor, command):
    if source is None:
        return
    original = fetch(c, "SELECT * FROM uploads WHERE bid_id=%s AND id=%s", (bid, upload))
    if filename_type(original["filename"]) != PDF:
        raise DomainError("WORD_PDF_DECLARATION_INVALID", "修改稿来源声明只适用于补充PDF", 422)
    word = fetch(
        c,
        "SELECT u.role FROM word_intake_receipts w JOIN uploads u ON u.id=w.upload_id WHERE w.bid_id=%s AND w.document_version_id=%s",
        (bid, source),
    )
    if word["role"] != original["role"]:
        raise DomainError("WORD_PDF_ROLE_MISMATCH", "补充PDF须保留原Word的项目与资料角色", 409)
    c.execute(
        "INSERT INTO word_pdf_declarations(upload_id,bid_id,source_document_version_id,command_id,created_by) VALUES(%s,%s,%s,%s,%s)",
        (upload, bid, source, command, actor),
    )


def queue_render(c, bid, version, command, profile):
    original = fetch(
        c,
        "SELECT b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN word_intake_receipts w ON w.document_version_id=v.id WHERE v.bid_id=%s AND v.id=%s",
        (bid, version),
    )
    payload = {
        "id": version,
        "digest": original["digest"],
        "render_profile_digest": profile["profile_digest"],
    }
    operation = enqueue_plan(
        c,
        bid,
        command,
        "word-render",
        payload,
        stages=[{"key": "word-render", "depends_on": []}],
        handler_version="word-render/1",
        topic="tender.task.document.v1",
        input_digest=sha(payload),
    )
    c.execute(
        "INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile) VALUES(%s,%s,%s,%s,%s)",
        (operation["id"], bid, version, profile["profile_digest"], Jsonb(profile)),
    )
    return operation


def publish_verified(c, message, upload, version_id, checked, *, render_profile=None):
    """Call after the existing verifier creates blob/version and sets upload=verified."""
    if checked["media_type"] == DOCX:
        if render_profile is None:
            raise DomainError(
                "WORD_RENDER_PROFILE_REQUIRED", "Word原件必须登记明确转换配置或不可用状态", 409
            )
        report = public_report(checked["inspection"])
        c.execute(
            "INSERT INTO word_intake_receipts(document_version_id,bid_id,upload_id,verify_operation_id,inspection_digest,inspection_report) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                version_id,
                upload["bid_id"],
                upload["id"],
                message["operation_id"],
                sha(report),
                Jsonb(report),
            ),
        )
        command = fetch(
            c,
            "SELECT command_id FROM operations WHERE id=%s AND bid_id=%s",
            (message["operation_id"], upload["bid_id"]),
        )["command_id"]
        operation = queue_render(c, upload["bid_id"], version_id, command, render_profile)
        return {
            "word_original_saved": True,
            "word_intake_url": f"/api/v1/bids/{upload['bid_id']}/word-originals/{version_id}",
            "conversion_operation_id": operation["id"],
        }
    declaration = c.execute(
        "SELECT source_document_version_id FROM word_pdf_declarations WHERE upload_id=%s AND bid_id=%s",
        (upload["id"], upload["bid_id"]),
    ).fetchone()
    if declaration:
        c.execute(
            "INSERT INTO document_relations(bid_id,document_version_id,source_document_version_id,kind,verification,evidence_ids) VALUES(%s,%s,%s,'user_declared_same_draft','unverified','{}'::uuid[])",
            (upload["bid_id"], version_id, declaration["source_document_version_id"]),
        )
        return {
            "word_source_document_version_id": str(declaration["source_document_version_id"]),
            "relation_kind": "user_declared_same_draft",
            "relation_verification": "unverified",
        }
    return {}


def controlled_qualified(result):
    required = {
        "source_docx_digest",
        "pdf_digest",
        "profile_digest",
        "renderer_digest",
        "fontset_digest",
        "options_digest",
    }
    return bool(
        result.get("state") == "succeeded"
        and result.get("coverage") == "complete"
        and result.get("conversion_attempted") is True
        and result.get("gaps") == []
        and isinstance(result.get("isolation"), dict)
        and result["isolation"]
        and REQUIRED_ISOLATION <= result["isolation"].keys()
        and all(value is True for value in result["isolation"].values())
        and required <= result.get("chain", {}).keys()
        and all(
            isinstance(result["chain"][key], str)
            and re.fullmatch(r"[0-9a-f]{64}", result["chain"][key])
            for key in required
        )
    )


def word_view(c, bid, version, *, current_profile=None):
    word = fetch(
        c,
        "SELECT w.*,v.original_name,b.digest,b.byte_size,u.role,coalesce(fv.file_id,x.file_id) AS file_id FROM word_intake_receipts w JOIN document_versions v ON v.id=w.document_version_id JOIN blobs b ON b.id=v.blob_id JOIN uploads u ON u.id=w.upload_id JOIN upload_transfers t ON t.upload_id=u.id JOIN upload_bindings x ON x.id=t.current_binding_id LEFT JOIN document_file_versions fv ON fv.bid_id=w.bid_id AND fv.version_id=w.document_version_id WHERE w.bid_id=%s AND w.document_version_id=%s",
        (bid, version),
    )
    run = c.execute(
        "SELECT r.*,o.state AS operation_state FROM word_render_runs r JOIN operations o ON o.id=r.operation_id WHERE r.bid_id=%s AND r.source_document_version_id=%s ORDER BY r.created_at DESC,r.operation_id DESC LIMIT 1",
        (bid, version),
    ).fetchone()
    outcome = run["outcome"] if run else None
    state = outcome["state"] if outcome else (run["operation_state"] if run else "failed")
    state = {"cancel_requested": "cancelling", "queued": "pending"}.get(state, state)
    pdf = str(run["pdf_document_version_id"]) if run and run["pdf_document_version_id"] else None
    qualified = bool(outcome and controlled_qualified(outcome))
    gaps = outcome["gaps"] if outcome else ([] if run else ["WORD_CONVERSION_NOT_REGISTERED"])
    if outcome is None and run and state in {"failed", "cancelled", "succeeded"}:
        gaps = [
            "WORD_CONVERSION_CANCELLED"
            if state == "cancelled"
            else "WORD_CONVERSION_RESULT_MISSING"
        ]
        if state == "succeeded":
            state = "failed"
    recoverable_configuration = bool(
        outcome
        and outcome.get("error_code")
        in {"WORD_RENDER_SANDBOX_UNAVAILABLE", "WORD_RENDER_PROFILE_CHANGED"}
        and current_profile
        and current_profile["identity"].get("enabled") is not False
        and word["inspection_report"]["conversion_eligible"]
    )
    return clean(
        {
            "id": version,
            "bid_id": bid,
            "file_id": word["file_id"],
            "original_name": word["original_name"],
            "role": word["role"],
            "original_saved": True,
            "source_sha256": word["digest"],
            "byte_size": word["byte_size"],
            "inspection": word["inspection_report"],
            "conversion": {
                "state": state,
                "operation_id": run["operation_id"] if run else None,
                "conversion_attempted": outcome["conversion_attempted"] if outcome else False,
                "coverage": outcome["coverage"] if outcome else "failed" if not run else "pending",
                "error_code": outcome.get("error_code") if outcome else None,
                "retryable": bool(
                    outcome.get("retryable") or recoverable_configuration
                    if outcome
                    else run and state in {"failed", "cancelled"}
                ),
                "gaps": gaps,
                "pdf_document_version_id": pdf,
                "controlled_render_verified": qualified,
            },
            "message": "Word原件已保存／对应PDF已生成，检查结果以该PDF为准"
            if qualified
            else "Word原件已保存／本次对应检查未完成",
            "delivery_check_message": "Word交付未获对应检查资格",
            "review_qualification": "not_established_by_conversion",
            "primary_action": {
                "action": "upload_modified_pdf",
                "label": "上传此修改稿的PDF",
                "context": {
                    "bid_id": bid,
                    "role": word["role"],
                    "declared_source_document_version_id": version,
                    "target": {"kind": "new_file"},
                },
            },
            "converted_pdf_action": {"action": "open_pdf", "document_version_id": pdf}
            if pdf
            else None,
        }
    )


def word_etag(c, bid, version, *, body=None, current_profile=None):
    from packages.domain.runtime import etag

    return etag(
        "word-original",
        version,
        sha(
            body
            if body is not None
            else word_view(c, bid, version, current_profile=current_profile)
        ),
    )


def current_word_adoption(c, bid, source):
    """Read-only hint; backend applies canonical material commit under its bid lock."""
    row = c.execute(
        "SELECT p.current_body_id,p.current_material_id,f.id AS file_id,f.head_version_id,b.material_revision,u.role FROM word_intake_receipts w JOIN uploads u ON u.id=w.upload_id JOIN document_file_versions fv ON fv.version_id=w.document_version_id AND fv.bid_id=w.bid_id JOIN document_files f ON f.id=fv.file_id JOIN bid_profiles p ON p.bid_id=w.bid_id JOIN bids b ON b.id=w.bid_id WHERE w.bid_id=%s AND w.document_version_id=%s",
        (bid, source),
    ).fetchone()
    selected = bool(
        row
        and row["head_version_id"] == UUID(str(source))
        and row["current_material_id"]
        and c.execute(
            "SELECT 1 FROM material_assignments WHERE snapshot_id=%s AND bid_id=%s AND document_version_id=%s AND role=%s",
            (row["current_material_id"], bid, source, row["role"]),
        ).fetchone()
    )
    return {
        "source_still_current_material": selected,
        "source_still_current_word_body": bool(
            selected and row["role"] == "body" and row["current_body_id"] is None
        ),
        "role": row["role"] if row else None,
        "current_material_revision": row["material_revision"] if row else None,
        "current_material_id": str(row["current_material_id"])
        if row and row["current_material_id"]
        else None,
    }


def completed_pdf(c, bid, source):
    """Latest requested conversion only; never fall back to an older green attempt."""
    run = c.execute(
        "SELECT * FROM word_render_runs WHERE bid_id=%s AND source_document_version_id=%s ORDER BY created_at DESC,operation_id DESC LIMIT 1",
        (bid, source),
    ).fetchone()
    if not run or not run["outcome"] or not controlled_qualified(run["outcome"]):
        return None
    return c.execute(
        "SELECT v.id AS document_version_id,f.file_id,b.digest,r.source_document_version_id FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN document_file_versions f ON f.version_id=v.id AND f.bid_id=v.bid_id JOIN document_relations r ON r.document_version_id=v.id AND r.bid_id=v.bid_id WHERE v.id=%s AND v.bid_id=%s AND b.media_type=%s AND b.digest=%s AND r.source_document_version_id=%s AND r.kind='controlled_render' AND r.verification='verified'",
        (run["pdf_document_version_id"], bid, PDF, run["outcome"]["chain"]["pdf_digest"], source),
    ).fetchone()
