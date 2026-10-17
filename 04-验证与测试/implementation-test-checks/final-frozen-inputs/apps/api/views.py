"""接口投影不推断模型结论。"""

from psycopg import sql

from packages.domain.runtime import clean, etag, fetch, sha


def bid_view(c, bid):
    row = fetch(
        c,
        "SELECT b.*,p.phase,p.archived_at,p.current_body_id,p.current_review_id,s.id AS stream_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id JOIN stream_heads s ON s.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    return {
        k: clean(row[k])
        for k in [
            "id",
            "title",
            "revision",
            "material_revision",
            "presentation_revision",
            "phase",
            "archived_at",
            "current_body_id",
            "current_review_id",
            "stream_id",
        ]
    } | {
        "next_action": {
            "action": "upload_materials",
            "reason": "上传招标资料与PDF正文；评审能力尚未配置时不会给出通过结论",
            "target_url": f"/projects/{bid}/materials",
            "blocked": False,
        }
    }


def upload_row(c, upload, lock=False):
    return fetch(
        c,
        sql.SQL(
            "SELECT u.*,t.file_id AS original_file_id,t.revision,t.mode,t.part_size,t.part_count,t.provider_upload_id,t.aborted_at,t.current_binding_id,t.committed_binding_id,b.file_id,b.expected_file_revision FROM uploads u JOIN upload_transfers t ON t.upload_id=u.id JOIN upload_bindings b ON b.id=t.current_binding_id WHERE u.id=%s"
        )
        + sql.SQL(" FOR UPDATE OF u,t" if lock else ""),
        (upload,),
    )


def upload_view(c, upload):
    r = upload_row(c, upload)
    return clean(
        {
            "id": r["id"],
            "bid_id": r["bid_id"],
            "batch_id": r["batch_id"],
            "filename": r["filename"],
            "state": "aborted" if r["aborted_at"] else r["state"],
            "document_version_id": r["verified_version_id"],
            "error_code": r["error_code"],
            "file_id": r["file_id"],
            "revision": r["revision"],
            "session_expires_at": r["expires_at"],
            "binding_id": r["current_binding_id"],
            "verified_retain_until": r["verified_retain_until"],
        }
    )


def batch_view(c, bid, batch):
    r = fetch(c, "SELECT * FROM upload_batches WHERE bid_id=%s AND id=%s", (bid, batch))
    return clean(
        {
            "id": r["id"],
            "bid_id": bid,
            "revision": r["revision"],
            "state": r["state"],
            "uploads": [
                upload_view(c, x["id"])
                for x in c.execute(
                    "SELECT id FROM uploads WHERE bid_id=%s AND batch_id=%s ORDER BY id",
                    (bid, batch),
                ).fetchall()
            ],
        }
    )


def operation_view(c, operation):
    r = fetch(c, "SELECT * FROM operations WHERE id=%s", (operation,))
    effect = c.execute(
        "SELECT result FROM effect_receipts WHERE operation_id=%s ORDER BY created_at DESC LIMIT 1",
        (operation,),
    ).fetchone()
    return clean(
        {k: r[k] for k in ["id", "bid_id", "kind", "state", "input_digest", "handler_version"]}
        | {"result_ref": effect["result"].get("result_ref") if effect else None}
    )


def operation_etag(c, operation, view=None):
    """完整对外内容与稳定执行身份；心跳和lease不制造条件冲突。"""
    body = operation_view(c, operation) if view is None else view
    stages = c.execute(
        "SELECT p.step_key,p.input_digest,p.dispatch_generation,s.fence FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s ORDER BY p.step_key,p.input_digest",
        (operation,),
    ).fetchall()
    return etag("operation", operation, sha({"operation": body, "stages": clean(stages)}))


def document_view(c, row):
    r = fetch(
        c,
        "SELECT v.*,b.digest,b.byte_size,b.media_type,fv.file_id,fv.revision AS file_revision,fv.previous_version_id,f.revision AS file_head_revision,f.head_version_id,f.role FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN document_file_versions fv ON fv.version_id=v.id JOIN document_files f ON f.id=fv.file_id WHERE v.id=%s",
        (row["document_version_id"],),
    )
    parsed = c.execute(
        "SELECT coverage FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC LIMIT 1",
        (r["id"],),
    ).fetchone()
    return clean(
        {
            "id": r["id"],
            "bid_id": r["bid_id"],
            "filename": r["original_name"],
            "role": r["role"],
            "artifact_kind": r["artifact_kind"],
            "sha256": r["digest"],
            "byte_size": r["byte_size"],
            "media_type": r["media_type"],
            "obtained_at": r["obtained_at"],
            "parse_coverage": parsed["coverage"] if parsed else "pending",
            "file_id": r["file_id"],
            "file_revision": r["file_revision"],
            "previous_version_id": r["previous_version_id"],
            "file_head_revision": r["file_head_revision"],
            "file_head_version_id": r["head_version_id"],
            "source_relation": None,
        }
    )


def evidence_view(c, eid):
    r = fetch(
        c,
        "SELECT e.*,p.width,p.height,p.rotation,p.printed_label,r.document_version_id FROM evidence_blocks e JOIN pages p ON p.parse_id=e.parse_id AND p.physical_page=e.physical_page JOIN parse_runs r ON r.id=e.parse_id WHERE e.id=%s",
        (eid,),
    )
    return {
        "id": str(r["id"]),
        "document_version_id": str(r["document_version_id"]),
        "parse_run_id": str(r["parse_id"]),
        "physical_page": r["physical_page"],
        "printed_label": r["printed_label"],
        "kind": r["kind"],
        "quote": r["text_content"],
        "bbox": r["bbox"],
        "page_width": float(r["width"]),
        "page_height": float(r["height"]),
        "rotation": r["rotation"],
        "source_status": "published",
    }
