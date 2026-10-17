"""显式复用已结束批次的核验字节；新批次/版本/绑定，不复活旧意图。"""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import upload_row, upload_view
from packages.domain.runtime import (
    DomainError,
    audit,
    connection,
    enqueue,
    etag,
    fetch,
    match,
    now,
    uid,
    validate,
)
from packages.domain.work_access import begin
from packages.lifecycle.service import closure_schema_available

router = APIRouter(prefix="/api/v1", tags=["retained verified originals"])


def source(c, bid, identity):
    row = upload_row(c, identity)
    if row["bid_id"] != bid:
        raise DomainError("UPLOAD_TARGET_MISMATCH", "原件不属于当前项目", 409)
    if row["state"] != "verified" or row["committed_binding_id"] or row["aborted_at"]:
        raise DomainError("UPLOAD_NOT_REUSABLE", "请选择已核验且未入库的保留原件", 409)
    if not row["verified_retain_until"] or row["verified_retain_until"] <= now():
        raise DomainError("VERIFIED_BYTES_EXPIRED", "保留原件已过期，请重新上传", 410)
    blob = fetch(
        c,
        "SELECT b.digest,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s AND v.bid_id=%s",
        (row["verified_version_id"], bid),
    )
    if blob["digest"] != row["declared_digest"] or blob["byte_size"] != row["declared_size"]:
        raise DomainError("VERIFIED_BYTES_CORRUPTED", "保留原件身份或声明摘要不一致，不能复用", 409)
    if not closure_schema_available(c):
        raise DomainError("BATCH_CLOSURE_NOT_CONFIGURED", "批次结束协议尚未配置", 503)
    fetch(
        c,
        "SELECT id FROM upload_batch_closures WHERE batch_id=%s AND bid_id=%s",
        (row["batch_id"], bid),
    )
    return row


@router.post("/bids/{bid_id}/upload-batches/{batch_id}/retained-uploads", status_code=201)
def reuse(bid_id: UUID, batch_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if set(body) != {"source_upload_id", "target", "reason"}:
        raise DomainError("INVALID_REQUEST", "请选择保留原件、新文件目标及理由", 422)
    try:
        original_id = UUID(body["source_upload_id"])
    except (ValueError, TypeError, AttributeError):
        raise DomainError("INVALID_REQUEST", "保留原件身份不合法", 422) from None
    validate("UploadBindingRequest", {"target": body["target"], "reason": body["reason"]})

    def command(c):
        return begin(
            c,
            actor["member_id"],
            "reuse_retained_upload",
            batch_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
            bid=bid_id,
            batch=batch_id,
        )

    def response(stored, replay=False):
        return JSONResponse(
            stored["body"],
            status_code=stored["status"],
            headers=stored["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
        )

    with connection() as c:
        cmd = command(c)
        if cmd.replay:
            return response(cmd.replay, True)
        original = source(c, bid_id, original_id)
        match(r.headers.get("if-match"), "upload", original_id, original["revision"])
        c.rollback()
    # 原件核验不持bid或命令锁；远端无新PUT，不改旧对象及来源关系。
    from apps.api.main import verify_retained_object

    with connection() as c:
        verify_retained_object(c, original)
    with connection() as c:
        cmd = command(c)
        if cmd.replay:
            return response(cmd.replay, True)
        original = source(c, bid_id, original_id)
        match(r.headers.get("if-match"), "upload", original_id, original["revision"])
        batch = fetch(
            c,
            "SELECT state FROM upload_batches WHERE id=%s AND bid_id=%s FOR UPDATE",
            (batch_id, bid_id),
        )
        if batch["state"] != "open" or original["batch_id"] == batch_id:
            raise DomainError("BATCH_CLOSED", "请选择新的开放批次", 409)
        from packages.domain.word_materials import require_configured
        from packages.word_intake.inspection import DOCX, filename_type
        from packages.word_intake.repository import assert_file_format, declare_pdf_source

        media = filename_type(original["filename"])
        declared = c.execute(
            "SELECT source_document_version_id FROM document_relations WHERE bid_id=%s AND document_version_id=%s AND kind='user_declared_same_draft'",
            (bid_id, original["verified_version_id"]),
        ).fetchone()
        reverify = media == DOCX or bool(declared)
        if reverify:
            require_configured(c)
        immutable_blob = fetch(
            c,
            "SELECT b.object_key FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s AND v.bid_id=%s",
            (original["verified_version_id"], bid_id),
        )
        target, file_id, expected = body["target"], uid(), 1
        if target["kind"] == "new_version":
            file = fetch(
                c, "SELECT * FROM document_files WHERE id=%s FOR UPDATE", (target["file_id"],)
            )
            if file["bid_id"] != bid_id or file["role"] != original["role"]:
                raise DomainError("UPLOAD_TARGET_MISMATCH", "目标文件项目或资料角色不一致", 409)
            expected, file_id = target["expected_file_revision"], file["id"]
            assert_file_format(c, file_id, media)
            if expected != file["revision"]:
                raise DomainError("FILE_REVISION_CONFLICT", "目标文件已更新，请重新选择", 412)
        else:
            c.execute(
                "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
                (file_id, bid_id, original["role"]),
            )
        upload, version, binding = uid(), uid(), uid()
        if not reverify:
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) SELECT %s,bid_id,blob_id,original_name,obtained_at,artifact_kind,%s FROM document_versions WHERE bid_id=%s AND id=%s",
                (version, actor["member_id"], bid_id, original["verified_version_id"]),
            )
        else:
            version = None
        c.execute(
            "INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at,verified_version_id,verified_retain_until) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                upload,
                bid_id,
                batch_id,
                original["filename"],
                original["role"],
                original["declared_digest"],
                original["declared_size"],
                immutable_blob["object_key"] if reverify else f"staging/{bid_id}/{upload}/retained",
                "verifying" if reverify else "verified",
                now() + timedelta(hours=24),
                version,
                original["verified_retain_until"],
            ),
        )
        c.execute(
            "INSERT INTO upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,reason,command_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                binding,
                bid_id,
                upload,
                file_id,
                expected,
                body["reason"],
                cmd.id,
                actor["member_id"],
            ),
        )
        c.execute(
            "INSERT INTO upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,part_size,part_count,revision,current_binding_id) VALUES(%s,%s,%s,%s,'single',%s,1,1,%s)",
            (upload, bid_id, file_id, expected, original["declared_size"], binding),
        )
        verify_operation = None
        if reverify:
            if declared:
                declare_pdf_source(
                    c,
                    bid_id,
                    upload,
                    declared["source_document_version_id"],
                    actor["member_id"],
                    cmd.id,
                )
            verify_operation = enqueue(
                c,
                bid_id,
                cmd.id,
                "verify-upload",
                {"id": upload, "digest": original["declared_digest"]},
            )
        audit(
            c,
            actor["member_id"],
            bid_id,
            "upload.retained_bytes_reselected",
            {
                "source_upload_id": original_id,
                "source_document_version_id": original["verified_version_id"],
                "upload_id": upload,
                "document_version_id": version,
                "batch_id": batch_id,
                "reason": body["reason"],
                "sha256": original["declared_digest"],
                "controlled_render": False,
                "verification_operation_id": verify_operation["id"] if verify_operation else None,
            },
            cmd.id,
        )
        value = upload_view(c, upload)
        return response(cmd.finish(value, 201, {"ETag": etag("upload", upload, 1)}))
