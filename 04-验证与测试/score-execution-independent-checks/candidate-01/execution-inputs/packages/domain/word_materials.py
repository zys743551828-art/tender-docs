"""可信Word材料选择与API采用端口；转换Worker不获得材料或当前指针写权限。"""

import os
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.runtime import (
    Command,
    DomainError,
    audit,
    clean,
    fetch,
    require_read_committed,
    sha,
    uid,
)
from packages.lifecycle.service import assert_work_writable
from packages.word_intake.inspection import DOCX
from packages.word_intake.repository import completed_pdf

SELECTION = "word.material_selected"
ADOPTION = "word.material_adoption_recorded"


def configured(c):
    return c.execute(
        "SELECT to_regclass('design.word_intake_receipts') IS NOT NULL AND to_regclass('design.word_pdf_declarations') IS NOT NULL AND to_regclass('design.word_render_runs') IS NOT NULL AS ready"
    ).fetchone()["ready"]


def require_enabled():
    if os.environ.get("WORD_INTAKE_ENABLED") != "1":
        raise DomainError("WORD_INTAKE_DISABLED", "Word接收暂未开放，请上传PDF", 503)


def require_configured(c):
    require_enabled()
    if not configured(c):
        raise DomainError("WORD_INTAKE_NOT_CONFIGURED", "Word原件接收协议尚未配置，请使用PDF", 503)


def pending_body(c, bid):
    """The current material role is authoritative; no parallel Word inventory/head."""
    return c.execute(
        "SELECT v.id AS document_version_id,v.original_name,f.file_id,b.digest,b.byte_size FROM bid_profiles p JOIN material_assignments a ON a.bid_id=p.bid_id AND a.snapshot_id=p.current_material_id AND a.role='body' JOIN document_versions v ON v.bid_id=a.bid_id AND v.id=a.document_version_id JOIN blobs b ON b.id=v.blob_id JOIN document_file_versions f ON f.bid_id=v.bid_id AND f.version_id=v.id JOIN document_files h ON h.id=f.file_id WHERE p.bid_id=%s AND p.current_body_id IS NULL AND b.media_type=%s AND h.head_version_id=v.id",
        (bid, DOCX),
    ).fetchone()


def context(c, bid, source):
    row = fetch(
        c,
        "SELECT b.material_revision,b.settings_revision,p.current_material_id,p.current_body_id,p.current_rule_set_id,coalesce(h.revision,0) AS fact_revision,h.fact_set_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id LEFT JOIN fact_heads h ON h.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    original = fetch(
        c,
        "SELECT v.id,v.blob_id,b.digest,b.media_type,f.file_id,d.head_version_id,u.role FROM word_intake_receipts w JOIN document_versions v ON v.id=w.document_version_id AND v.bid_id=w.bid_id JOIN blobs b ON b.id=v.blob_id JOIN document_file_versions f ON f.version_id=v.id AND f.bid_id=v.bid_id JOIN document_files d ON d.id=f.file_id JOIN uploads u ON u.id=w.upload_id WHERE w.bid_id=%s AND w.document_version_id=%s",
        (bid, source),
    )
    selected = c.execute(
        "SELECT 1 FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND document_version_id=%s AND role=%s",
        (bid, row["current_material_id"], source, original["role"]),
    ).fetchone()
    if (
        original["media_type"] != DOCX
        or original["head_version_id"] != UUID(str(source))
        or not selected
    ):
        raise DomainError(
            "WORD_SELECTION_NOT_CURRENT", "Word已不是当前文件或所选材料，转换产物仅保留历史", 409
        )
    if original["role"] == "body" and row["current_body_id"] is not None:
        raise DomainError(
            "WORD_SELECTION_NOT_CURRENT", "当前已选择其他PDF正文，不能用迟到转换结果覆盖", 409
        )
    return clean(
        dict(row)
        | {
            "source_document_version_id": source,
            "source_sha256": original["digest"],
            "file_id": original["file_id"],
            "role": original["role"],
        }
    )


def record_selection(c, bid, source, actor, command):
    require_read_committed(c)
    assert_work_writable(c, bid)
    value = context(c, bid, source)
    audit(
        c,
        actor,
        bid,
        SELECTION,
        {"source_document_version_id": source, "context": value, "context_digest": sha(value)},
        command,
    )
    return value


def candidate(c, bid, source):
    if not configured(c):
        return None
    pdf = completed_pdf(c, bid, source)
    if pdf is None:
        return None
    run = fetch(
        c,
        "SELECT r.operation_id,r.profile_digest,r.outcome,o.state FROM word_render_runs r JOIN operations o ON o.id=r.operation_id WHERE r.bid_id=%s AND r.source_document_version_id=%s ORDER BY r.created_at DESC,r.operation_id DESC LIMIT 1",
        (bid, source),
    )
    if run["state"] != "succeeded":
        return None
    blob = fetch(
        c,
        "SELECT b.object_key,b.digest,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=%s",
        (bid, pdf["document_version_id"]),
    )
    return clean(
        dict(pdf)
        | dict(blob)
        | {
            "source_document_version_id": source,
            "operation_id": run["operation_id"],
            "profile_digest": run["profile_digest"],
        }
    )


def verify_candidate(value):
    """Call with no business lock or DB connection; HEAD/ETag is not byte SHA."""
    raw = storage.get(value["object_key"])
    if (
        len(raw) != value["byte_size"]
        or sha(raw) != value["digest"]
        or not raw.startswith(b"%PDF-")
    ):
        raise DomainError(
            "WORD_RENDERED_PDF_CORRUPTED", "转换PDF实际字节核验失败，Word原件保留", 409
        )
    return value


def selection(c, bid, source):
    return c.execute(
        "SELECT * FROM audit_entries WHERE bid_id=%s AND event_type=%s AND payload->>'source_document_version_id'=%s ORDER BY (payload->'context'->>'material_revision')::bigint DESC,created_at DESC,id DESC LIMIT 1",
        (bid, SELECTION, str(source)),
    ).fetchone()


def _record(c, bid, source, value, selected, state, reason, **extra):
    result = clean(
        {
            "state": state,
            "reason": reason,
            "source_document_version_id": source,
            "conversion_operation_id": value["operation_id"],
            "selection_id": selected["id"] if selected else None,
            **extra,
        }
    )
    audit(
        c,
        selected["actor_id"] if selected else None,
        bid,
        ADOPTION,
        result,
        selected["command_id"] if selected else None,
    )
    return result


def adopt(c, bid, source, verified, parser, *, render_profile):
    """API transaction, bid lock, exact selection CAS; never called in ordinary Worker."""
    require_read_committed(c)
    assert_work_writable(c, bid)
    selected = selection(c, bid, source)
    current = candidate(c, bid, source)
    if not selected or not current or sha(current) != sha(verified):
        raise DomainError("WORD_CONVERSION_CHANGED", "转换请求或输出已经变化，请重新核对", 409)
    old = c.execute(
        "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type=%s AND payload->>'selection_id'=%s AND payload->>'conversion_operation_id'=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (bid, ADOPTION, str(selected["id"]), current["operation_id"]),
    ).fetchone()
    if old:
        return old["payload"]
    if render_profile["profile_digest"] != current["profile_digest"]:
        return _record(
            c, bid, source, current, selected, "needs_selection", "WORD_RENDER_PROFILE_CHANGED"
        )
    try:
        actual = context(c, bid, source)
    except DomainError as error:
        return _record(c, bid, source, current, selected, "needs_selection", error.code)
    if sha(actual) != selected["payload"]["context_digest"]:
        return _record(
            c, bid, source, current, selected, "needs_selection", "WORD_SELECTION_CONTEXT_CHANGED"
        )
    from packages.domain.word_lineage import ensure_controlled_ancestry

    try:
        with c.transaction():
            ensure_controlled_ancestry(c, bid, current["document_version_id"])
    except DomainError as error:
        return _record(c, bid, source, current, selected, "needs_selection", error.code)
    command = Command(
        c,
        selected["actor_id"],
        "adopt_word_render",
        UUID(str(source)),
        f"word-adopt:{current['operation_id']}:{selected['id']}",
        {
            "selection_id": selected["id"],
            "context_digest": selected["payload"]["context_digest"],
            "conversion": current,
        },
    )
    if command.replay:
        return command.replay["body"]
    entries = c.execute(
        "SELECT a.document_version_id,a.role,f.file_id FROM material_assignments a JOIN document_file_versions f ON f.bid_id=a.bid_id AND f.version_id=a.document_version_id WHERE a.bid_id=%s AND a.snapshot_id=%s ORDER BY a.role,a.ordinal,a.document_version_id",
        (bid, actual["current_material_id"]),
    ).fetchall()
    remaining = []
    for entry in entries:
        other = entry["document_version_id"]
        if str(other) != str(source):
            previous = selection(c, bid, other)
            if previous:
                try:
                    if sha(context(c, bid, other)) == previous["payload"]["context_digest"]:
                        remaining.append((other, previous))
                except DomainError:
                    pass
    for entry in entries:
        if str(entry["document_version_id"]) == str(source) and entry["role"] == actual["role"]:
            entry.update(
                document_version_id=current["document_version_id"], file_id=current["file_id"]
            )
    snapshot, revision = uid(), actual["material_revision"] + 1
    manifest = clean(entries)
    c.execute(
        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,%s,%s)",
        (snapshot, bid, revision, Jsonb({"documents": manifest}), sha(manifest)),
    )
    ordinals: dict[str, int] = {}
    for entry in entries:
        ordinal = ordinals.get(entry["role"], 0)
        ordinals[entry["role"]] = ordinal + 1
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,%s)",
            (snapshot, bid, entry["document_version_id"], entry["role"], ordinal),
        )
    c.execute(
        "UPDATE bids SET revision=revision+1,material_revision=%s WHERE id=%s", (revision, bid)
    )
    c.execute(
        "UPDATE bid_profiles SET current_material_id=%s,current_body_id=CASE WHEN %s='body' THEN %s::uuid ELSE current_body_id END,current_review_id=NULL WHERE bid_id=%s",
        (snapshot, actual["role"], current["document_version_id"], bid),
    )
    for other, previous in remaining:
        record_selection(c, bid, other, previous["actor_id"], command.id)
    from packages.domain.parsing_profile import profile as parse_profile
    from packages.domain.reviews import on_material_commit
    from packages.domain.runtime import enqueue, enqueue_plan

    payload = {"id": snapshot, "digest": sha(manifest)}
    operation = (
        enqueue(c, bid, command.id, "parse-materials", payload)
        if parser is None
        else enqueue_plan(
            c,
            bid,
            command.id,
            "parse-materials",
            payload,
            stages=[{"key": "parse-materials", "depends_on": []}],
            handler_version="document/2",
            topic="tender.task.document.v1",
            input_digest=sha(payload | {"parse_profile": parse_profile(parser)}),
        )
    )
    request = on_material_commit(c, bid, command.id)
    audit(
        c,
        selected["actor_id"],
        bid,
        "materials.committed",
        {
            "snapshot_id": snapshot,
            "source_word_document_version_id": source,
            "review_request_id": request["resource_id"] if request else None,
        },
        command.id,
    )
    from packages.tasks.coordinator import material_committed

    event = fetch(
        c,
        "SELECT id FROM audit_entries WHERE bid_id=%s AND command_id=%s AND event_type='materials.committed'",
        (bid, command.id),
    )
    material_committed(c, bid=bid, event_id=event["id"])
    result = _record(
        c,
        bid,
        source,
        current,
        selected | {"command_id": command.id},
        "adopted",
        None,
        material_snapshot_id=snapshot,
        material_revision=revision,
        document_version_id=current["document_version_id"],
        parse_operation_id=operation["id"],
        review_request_id=request["resource_id"] if request else None,
    )
    command.finish(result)
    return result


def material_view(c, bid, source):
    """只读投影严格区分历史生成PDF、当前材料和真正采用收据。"""
    require = fetch(
        c,
        "SELECT w.document_version_id,coalesce(fv.file_id,x.file_id) AS file_id,u.role FROM word_intake_receipts w JOIN uploads u ON u.id=w.upload_id JOIN upload_transfers t ON t.upload_id=u.id JOIN upload_bindings x ON x.id=t.current_binding_id LEFT JOIN document_file_versions fv ON fv.version_id=w.document_version_id AND fv.bid_id=w.bid_id WHERE w.bid_id=%s AND w.document_version_id=%s",
        (bid, source),
    )
    head = fetch(
        c,
        "SELECT head_version_id FROM document_files WHERE id=%s AND bid_id=%s",
        (require["file_id"], bid),
    )
    profile = fetch(
        c,
        "SELECT p.current_material_id,p.current_body_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
        (bid,),
    )
    selected = selection(c, bid, source)
    receipt = c.execute(
        "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type=%s AND payload->>'source_document_version_id'=%s AND payload->>'selection_id'=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (bid, ADOPTION, str(source), str(selected["id"]) if selected else ""),
    ).fetchone()
    receipt = receipt["payload"] if receipt else {}
    selected_material = bool(
        head["head_version_id"] == UUID(str(source))
        and c.execute(
            "SELECT 1 FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND document_version_id=%s AND role=%s",
            (bid, profile["current_material_id"], source, require["role"]),
        ).fetchone()
    )
    adopted_pdf = receipt.get("document_version_id")
    is_current = bool(
        adopted_pdf
        and c.execute(
            "SELECT 1 FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND document_version_id=%s AND role=%s",
            (bid, profile["current_material_id"], adopted_pdf, require["role"]),
        ).fetchone()
    )
    return clean(
        {
            "bid_id": bid,
            "source_document_version_id": source,
            "file_id": require["file_id"],
            "source_is_file_head": head["head_version_id"] == UUID(str(source)),
            "selected_as_current_material": selected_material,
            "adoption_state": receipt.get(
                "state", "pending" if selected_material else "not_selected"
            ),
            "adoption_reason": receipt.get("reason"),
            "adopted_pdf_document_version_id": adopted_pdf,
            "is_current_converted_pdf": is_current,
            "current_body_version_id": profile["current_body_id"],
            "material_revision": profile["material_revision"],
        }
    )
