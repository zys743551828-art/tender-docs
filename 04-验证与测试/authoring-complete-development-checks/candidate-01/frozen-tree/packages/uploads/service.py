"""分片状态机：远端I/O在事务外，短事务只负责CAS与现有verify任务入队。"""

from __future__ import annotations

import os
from datetime import timedelta
from uuid import UUID, uuid4

from apps.api.object_transfer import _configured_origin, _identity, _ticket, _verify
from apps.api.views import upload_row, upload_view
from packages.domain.runtime import (
    DomainError,
    accepted,
    audit,
    clean,
    connection,
    enqueue,
    etag,
    fetch,
    match,
    now,
    uid,
    validate,
)
from packages.uploads.journal import initialization_candidate
from packages.uploads.native import NativeMultipart
from packages.uploads.protocol import (
    MAX_GRANTS,
    PART_SIZE,
    check_parts,
    layout,
    pack,
    part_size,
    unpack,
)

BASE = "/api/v1/object-transfers/multipart"
PENDING = "X-Workbench-Multipart-Pending"


def wants_multipart(body):
    return os.environ.get("OBJECT_STORE_BACKEND") == "oss" and body["byte_size"] > PART_SIZE


def is_multipart(upload_id):
    with connection() as c:
        return upload_row(c, upload_id)["mode"] == "multipart"


def _command(c, request, session, kind, target, body, conditional=True):
    from packages.domain.work_access import begin

    if kind == "create_upload":
        bid, batch = target, body["batch_id"]
    else:
        row = upload_row(c, target)
        bid, batch = row["bid_id"], row["batch_id"]
    return begin(
        c,
        session["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match") if conditional else None,
        bid=bid,
        batch=batch,
        work=kind != "abort_upload",
    )


def _locked(c, upload_id):
    old = upload_row(c, upload_id)
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (old["bid_id"],))
    return upload_row(c, upload_id, True)


def _work(c, row):
    from packages.domain.runtime import require_read_committed
    from packages.lifecycle.service import assert_batch_open, assert_work_writable

    require_read_committed(c)
    assert_work_writable(c, row["bid_id"])
    assert_batch_open(c, row["bid_id"], row["batch_id"])


def _open(c, row, *, phase="ready"):
    _work(c, row)
    if row["mode"] != "multipart":
        raise DomainError("UPLOAD_MODE_MISMATCH", "上传模式不匹配", 409)
    if row["expires_at"] <= now():
        raise DomainError("UPLOAD_EXPIRED", "上传会话已过期，请重新创建", 410)
    batch = fetch(c, "SELECT state FROM upload_batches WHERE id=%s", (row["batch_id"],))
    if row["state"] != "authorized" or row["aborted_at"] or batch["state"] != "open":
        raise DomainError("UPLOAD_AUTHORIZATION_CLOSED", "上传授权已结束，请刷新状态", 409)
    state = unpack(row["provider_upload_id"], row["staging_key"])
    if state["phase"] != phase:
        raise DomainError(
            "MULTIPART_INIT_UNCONFIRMED",
            "分片初始化未确认，请保留上传编号并重新查询；不要反复创建远端会话",
            503,
            {
                "upload_id": str(row["id"]),
                "create_new_transfer_required": state["phase"] == "init_uncertain",
            },
        )
    return state


def claims(row, session, number):
    return {
        **_identity(session),
        "kind": "multipart_part",
        "method": "PUT",
        "upload": str(row["id"]),
        "bid": str(row["bid_id"]),
        "key": row["staging_key"],
        "part": number,
        "size": part_size(row, number),
        "revision": row["revision"],
        "digest": row["declared_digest"],
    }


def grant(row, session, numbers):
    if not 1 <= len(numbers) <= MAX_GRANTS or len(set(numbers)) != len(numbers):
        raise DomainError("INVALID_PART", "每次只可授权1至128个不重复片号", 422)
    expires = min(now() + timedelta(minutes=15), row["expires_at"], session["expires_at"])
    parts = []
    for number in numbers:
        size = part_size(row, number)
        token = _ticket(claims(row, session, number), session, expires)
        parts.append(
            {
                "part_number": number,
                "byte_offset": (number - 1) * row["part_size"],
                "byte_size": size,
                "url": f"{_configured_origin()}{BASE}/{row['id']}/parts/{number}?ticket={token}",
                "expires_at": expires.isoformat(),
            }
        )
    return {
        "upload_id": str(row["id"]),
        "object_ref": str(row["id"]),
        "file_id": str(row["file_id"]),
        "mode": "multipart",
        "upload_url": None,
        "part_size": row["part_size"],
        "part_count": row["part_count"],
        "parts": parts,
        "expires_at": expires.isoformat(),
        "session_expires_at": row["expires_at"].isoformat(),
        "revision": row["revision"],
    }


def create_upload(bid_id, request, session, body):
    validate("UploadRequest", body)
    if not body["filename"].lower().endswith(".pdf") or body.get(
        "declared_source_document_version_id"
    ):
        raise DomainError("PDF_REQUIRED", "分片上传目前接收PDF原件", 415)
    if body["byte_size"] > int(os.environ.get("MAX_UPLOAD_BYTES", "104857600")):
        raise DomainError("UPLOAD_TOO_LARGE", "文件超出当前上传大小上限", 413)
    size, count = layout(body["byte_size"])
    own_nonce = None
    with connection() as c:
        cmd = _command(c, request, session, "create_upload", bid_id, body, False)
        if cmd.replay:
            response = cmd.replay
            upload_id = UUID(response["body"]["upload_id"])
        else:
            fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
            batch = fetch(
                c,
                "SELECT * FROM upload_batches WHERE id=%s AND bid_id=%s FOR UPDATE",
                (body["batch_id"], bid_id),
            )
            if batch["state"] != "open":
                raise DomainError("BATCH_CLOSED", "该批次已结束", 409)
            target, file_id = body["target"], uid()
            if target["kind"] == "new_version":
                file_id = UUID(target["file_id"])
                f = fetch(c, "SELECT * FROM document_files WHERE id=%s FOR UPDATE", (file_id,))
                if f["bid_id"] != bid_id or f["role"] != body["role"]:
                    raise DomainError("UPLOAD_TARGET_MISMATCH", "目标文件与资料角色不匹配", 409)
                expected = target["expected_file_revision"]
                if f["revision"] != expected:
                    raise DomainError("FILE_REVISION_CONFLICT", "目标文件已更新", 412)
            else:
                expected = 1
                c.execute(
                    "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
                    (file_id, bid_id, body["role"]),
                )
            upload_id, binding, own_nonce = uid(), uid(), str(uuid4())
            key, expires = f"staging/{bid_id}/{upload_id}", now() + timedelta(hours=24)
            c.execute(
                "INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at,error_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'authorized',%s,'MULTIPART_INIT_PENDING')",
                (
                    upload_id,
                    bid_id,
                    body["batch_id"],
                    body["filename"],
                    body["role"],
                    body["declared_sha256"],
                    body["byte_size"],
                    key,
                    expires,
                ),
            )
            c.execute(
                "INSERT INTO upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,reason,command_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    binding,
                    bid_id,
                    upload_id,
                    file_id,
                    expected,
                    "首次明确分片上传目标",
                    cmd.id,
                    session["member_id"],
                ),
            )
            state = pack("init_reserved", key=key, nonce=own_nonce)
            c.execute(
                "INSERT INTO upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,provider_upload_id,part_size,part_count,revision,current_binding_id) VALUES(%s,%s,%s,%s,'multipart',%s,%s,%s,1,%s)",
                (upload_id, bid_id, file_id, expected, state, size, count, binding),
            )
            row = upload_row(c, upload_id)
            response = cmd.finish(
                grant(row, session, list(range(1, min(count, MAX_GRANTS) + 1))),
                200,
                {"ETag": etag("upload", upload_id, 1)},
            )
    # The reserved command and session identity are durable before the first remote request.
    if own_nonce:
        initialize_once(upload_id, own_nonce)
    with connection() as c:
        row = upload_row(c, upload_id)
        state = _open(c, row)
    if own_nonce is None:
        # A previous commit acknowledgement could have been lost before exact abort cleanup.
        # Never issue a replay grant for a native session whose existence is unconfirmed.
        NativeMultipart().parts(row["staging_key"], state["provider_id"])
    return response


def initialize_once(upload_id, own_nonce):
    with connection() as c:
        row = upload_row(c, upload_id)
        _work(c, row)
        state = unpack(row["provider_upload_id"], row["staging_key"])
        if state["phase"] != "init_reserved" or state["nonce"] != own_nonce:
            raise DomainError("MULTIPART_INIT_UNCONFIRMED", "分片初始化未确认", 503)
    native = NativeMultipart()
    initialization_candidate(row, "before_single_init_attempt")
    try:
        provider_id = native.initialize(row["staging_key"])
    except Exception:
        initialization_candidate(row, "init_response_uncertain_no_automatic_reinit")
        with connection() as c:
            current = _locked(c, upload_id)
            if current["provider_upload_id"] == row["provider_upload_id"]:
                c.execute(
                    "UPDATE upload_transfers SET provider_upload_id=%s WHERE upload_id=%s",
                    (pack("init_uncertain", key=row["staging_key"], nonce=own_nonce), upload_id),
                )
                c.execute(
                    "UPDATE uploads SET error_code='MULTIPART_INIT_UNCERTAIN' WHERE id=%s",
                    (upload_id,),
                )
        raise DomainError(
            "MULTIPART_INIT_UNCONFIRMED",
            "初始化响应未确认；本会话不会自动重建，请查询后新建传输",
            503,
            {"upload_id": str(upload_id), "create_new_transfer_required": True},
        ) from None
    accepted_id = False
    for attempt in range(2):
        try:
            with connection() as c:
                current = _locked(c, upload_id)
                _work(c, current)
                current_state = unpack(current["provider_upload_id"], current["staging_key"])
                if (
                    current_state["phase"] == "ready"
                    and current_state["provider_id"] == provider_id
                    and current_state["nonce"] == own_nonce
                ):
                    accepted_id = not current["aborted_at"] and current["state"] == "authorized"
                elif (
                    current["provider_upload_id"] == row["provider_upload_id"]
                    and not current["aborted_at"]
                    and current["state"] == "authorized"
                ):
                    c.execute(
                        "UPDATE upload_transfers SET provider_upload_id=%s WHERE upload_id=%s",
                        (
                            pack(
                                "ready",
                                key=row["staging_key"],
                                nonce=own_nonce,
                                provider_id=provider_id,
                            ),
                            upload_id,
                        ),
                    )
                    c.execute("UPDATE uploads SET error_code=NULL WHERE id=%s", (upload_id,))
                    accepted_id = True
                elif (
                    current["provider_upload_id"] == row["provider_upload_id"]
                    and current["aborted_at"]
                ):
                    c.execute(
                        "UPDATE upload_transfers SET provider_upload_id=%s WHERE upload_id=%s",
                        (
                            pack(
                                "aborted",
                                key=row["staging_key"],
                                nonce=own_nonce,
                                provider_id=provider_id,
                            ),
                            upload_id,
                        ),
                    )
            break
        except Exception:
            if attempt == 1:
                initialization_candidate(row, "known_init_id_database_unconfirmed_abort_requested")
                native.abort(row["staging_key"], provider_id)
                raise DomainError(
                    "MULTIPART_INIT_UNCONFIRMED",
                    "分片已请求取消；初始化记录未确认，请重新查询",
                    503,
                ) from None
    if not accepted_id:
        native.abort(row["staging_key"], provider_id)
        raise DomainError(
            "UPLOAD_AUTHORIZATION_CLOSED", "初始化期间上传已结束，已请求取消远端分片", 409
        )


def snapshot(upload_id, session, number=None, ticket=None):
    with connection() as c:
        row = upload_row(c, upload_id)
        _open(c, row)
        if ticket is not None:
            _verify(ticket, claims(row, session, number))
        return row


def publish_part(upload_id, session, number, ticket, original, remote_etag):
    with connection() as c:
        row = _locked(c, upload_id)
        _open(c, row)
        _verify(ticket, claims(row, session, number))
        if (
            row["revision"] != original["revision"]
            or row["provider_upload_id"] != original["provider_upload_id"]
        ):
            raise DomainError("REVISION_CONFLICT", "分片授权已刷新，请查询存储状态后补传", 412)
        _observe(
            c,
            upload_id,
            [{"part_number": number, "byte_size": part_size(row, number), "etag": remote_etag}],
        )


def _observe(c, upload_id, parts):
    for part in parts:
        c.execute(
            "INSERT INTO upload_parts(upload_id,part_number,byte_size,etag,observed_at) VALUES(%s,%s,%s,%s,now()) ON CONFLICT(upload_id,part_number) DO UPDATE SET byte_size=EXCLUDED.byte_size,etag=EXCLUDED.etag,observed_at=now()",
            (upload_id, part["part_number"], part["byte_size"], part["etag"]),
        )


def get_parts(upload_id, session):
    row = snapshot(upload_id, session)
    state = unpack(row["provider_upload_id"], row["staging_key"])
    parts = check_parts(row, NativeMultipart().parts(row["staging_key"], state["provider_id"]))
    with connection() as c:
        current = _locked(c, upload_id)
        _open(c, current)
        if (
            current["revision"] != row["revision"]
            or current["provider_upload_id"] != row["provider_upload_id"]
        ):
            raise DomainError("REVISION_CONFLICT", "上传状态已变化，请刷新", 412)
        _observe(c, upload_id, parts)
    return clean(
        {
            "upload_id": upload_id,
            "revision": row["revision"],
            "mode": "multipart",
            "completed_parts": parts,
            "session_expires_at": row["expires_at"],
        }
    )


def refresh(upload_id, request, session, body):
    validate("RefreshUploadRequest", body)
    with connection() as c:
        cmd = _command(c, request, session, "refresh_upload", upload_id, body)
        if cmd.replay:
            return cmd.replay
        row = _locked(c, upload_id)
        _open(c, row)
        match(request.headers.get("if-match"), "upload", upload_id, row["revision"])
        for number in body["part_numbers"]:
            part_size(row, number)
        c.execute(
            "UPDATE upload_transfers SET revision=revision+1 WHERE upload_id=%s", (upload_id,)
        )
        row["revision"] += 1
        return cmd.finish(
            grant(row, session, body["part_numbers"]),
            200,
            {"ETag": etag("upload", upload_id, row["revision"])},
        )


def complete(upload_id, request, session, body):
    validate("UploadComplete", body)
    with connection() as c:
        cmd = _command(c, request, session, "complete_upload", upload_id, body)
        if cmd.replay and PENDING not in cmd.replay["headers"]:
            return cmd.replay
        row = _locked(c, upload_id)
        _work(c, row)  # interim ACK is not permission for new remote completion after close.
        if row["expires_at"] <= now():
            raise DomainError("UPLOAD_EXPIRED", "上传会话已过期，请重新创建", 410)
        state = unpack(row["provider_upload_id"], row["staging_key"])
        if not cmd.replay:
            _open(c, row)
            match(request.headers.get("if-match"), "upload", upload_id, row["revision"])
            if body["declared_sha256"] != row["declared_digest"]:
                raise DomainError("UPLOAD_RECEIPT_INVALID", "完成声明摘要不匹配", 422)
            check_parts(row, body["parts"], complete=True)
            state = {**state, "phase": "completing", "nonce": str(cmd.id)}
            encoded = pack(**state)
            c.execute("UPDATE uploads SET state='uploaded' WHERE id=%s", (upload_id,))
            c.execute(
                "UPDATE upload_transfers SET provider_upload_id=%s,revision=revision+1 WHERE upload_id=%s",
                (encoded, upload_id),
            )
            row["revision"] += 1
            row["provider_upload_id"] = encoded
            cmd.finish({"upload_id": str(upload_id)}, 202, {PENDING: "true"})
        elif (
            row["state"] != "uploaded"
            or state["phase"] != "completing"
            or state["nonce"] != str(cmd.id)
        ):
            raise DomainError("UPLOAD_AUTHORIZATION_CLOSED", "上传完成状态已变化", 409)
        command_id, revision = cmd.id, row["revision"]
    native = NativeMultipart()
    try:
        try:
            native.verify_complete(row)
        except DomainError as error:
            if error.code != "MULTIPART_COMPLETE_UNCONFIRMED":
                raise
            observed = check_parts(
                row, native.parts(row["staging_key"], state["provider_id"]), complete=True
            )
            requested = [{**item, "etag": item["etag"].strip('"')} for item in body["parts"]]
            if observed != sorted(requested, key=lambda item: item["part_number"]):
                raise DomainError(
                    "UPLOAD_RECEIPT_INVALID", "完成收据与实际存储分片不符", 422
                ) from None
            try:
                native.complete(row["staging_key"], state["provider_id"], observed)
            except DomainError:
                # A lost success response is reconciled by exact HEAD and full SHA readback.
                native.verify_complete(row)
            native.verify_complete(row)
    except DomainError as error:
        reversible = {
            "MULTIPART_PARTS_INVALID",
            "MULTIPART_INCOMPLETE",
            "UPLOAD_RECEIPT_INVALID",
            "INVALID_PART",
        }
        corrupt = {"UPLOAD_DIGEST_MISMATCH", "UPLOAD_SIZE_MISMATCH"}
        if error.code in reversible | corrupt:
            return _completion_failure(
                upload_id,
                request,
                session,
                body,
                row,
                state,
                error,
                quarantine=error.code in corrupt,
            )
        raise
    with connection() as c:
        cmd = _command(c, request, session, "complete_upload", upload_id, body)
        if cmd.replay and PENDING not in cmd.replay["headers"]:
            return cmd.replay
        current = _locked(c, upload_id)
        _work(c, current)
        if (
            current["revision"] != revision
            or current["state"] != "uploaded"
            or current["provider_upload_id"] != row["provider_upload_id"]
            or current["aborted_at"]
        ):
            raise DomainError("REVISION_CONFLICT", "完成期间上传状态已变化", 412)
        if current["expires_at"] <= now():
            raise DomainError("UPLOAD_EXPIRED", "上传会话已过期", 410)
        c.execute("UPDATE uploads SET state='verifying',error_code=NULL WHERE id=%s", (upload_id,))
        c.execute(
            "UPDATE upload_transfers SET provider_upload_id=%s,revision=revision+1 WHERE upload_id=%s",
            (
                pack(
                    "completed",
                    key=row["staging_key"],
                    nonce=state["nonce"],
                    provider_id=state["provider_id"],
                ),
                upload_id,
            ),
        )
        _observe(c, upload_id, body["parts"])
        op = enqueue(
            c,
            row["bid_id"],
            command_id,
            "verify-upload",
            {"id": upload_id, "digest": row["declared_digest"]},
        )
        audit(
            c,
            session["member_id"],
            row["bid_id"],
            "upload.multipart_completed",
            {"upload_id": upload_id},
            command_id,
        )
        return cmd.finish(accepted(op, upload_id, "upload"), 202)


def _completion_failure(upload_id, request, session, body, original, state, error, *, quarantine):
    with connection() as c:
        cmd = _command(c, request, session, "complete_upload", upload_id, body)
        if cmd.replay and PENDING not in cmd.replay["headers"]:
            return cmd.replay
        row = _locked(c, upload_id)
        if (
            row["revision"] != original["revision"]
            or row["provider_upload_id"] != original["provider_upload_id"]
        ):
            raise DomainError("REVISION_CONFLICT", "完成期间上传状态已变化", 412)
        c.execute(
            "UPDATE uploads SET state=%s,error_code=%s WHERE id=%s",
            ("quarantined" if quarantine else "authorized", error.code, upload_id),
        )
        c.execute(
            "UPDATE upload_transfers SET provider_upload_id=%s,revision=revision+1 WHERE upload_id=%s",
            (
                pack(
                    "completed" if quarantine else "ready",
                    key=row["staging_key"],
                    nonce=state["nonce"],
                    provider_id=state["provider_id"],
                ),
                upload_id,
            ),
        )
        return cmd.finish(
            {
                "code": error.code,
                "message": error.message,
                "request_id": getattr(request.state, "request_id", str(uid())),
                "retryable": False,
                "details": {
                    "upload_id": str(upload_id),
                    "new_authorization_required": not quarantine,
                },
            },
            error.status,
            {"ETag": etag("upload", upload_id, row["revision"] + 1)},
        )


def abort(upload_id, request, session):
    with connection() as c:
        cmd = _command(c, request, session, "abort_upload", upload_id, {})
        if cmd.replay:
            response = cmd.replay
            row = upload_row(c, upload_id)
        else:
            row = _locked(c, upload_id)
            match(request.headers.get("if-match"), "upload", upload_id, row["revision"])
            if row["state"] in {"uploaded", "verifying", "verified"}:
                raise DomainError(
                    "UPLOAD_AUTHORIZATION_CLOSED", "正在完成或核验的原件不可终止删除", 409
                )
            c.execute(
                "UPDATE upload_transfers SET aborted_at=now(),revision=revision+1 WHERE upload_id=%s",
                (upload_id,),
            )
            audit(
                c,
                session["member_id"],
                row["bid_id"],
                "upload.aborted",
                {"upload_id": upload_id},
                cmd.id,
            )
            response = cmd.finish(
                upload_view(c, upload_id),
                200,
                {"ETag": etag("upload", upload_id, row["revision"] + 1)},
            )
    state = unpack(row["provider_upload_id"], row["staging_key"])
    if state["provider_id"]:
        NativeMultipart().abort(row["staging_key"], state["provider_id"])
    return response
