"""T0与PDF资料入库API；未配置能力明确拒绝。"""

import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID

import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb

from apps.api.assistant_routes import router as assistant_router
from apps.api.auth import COOKIE, auth, auth_view, csrf, origin, password_ok
from apps.api.authoring import router as authoring_router
from apps.api.comparison_routes import router as comparison_router
from apps.api.composition import mount as mount_composition
from apps.api.event_routes import router as event_router
from apps.api.export_openapi import implemented_openapi
from apps.api.history_routes import router as history_router
from apps.api.human_routes import router as human_router
from apps.api.identity_routes import router as identity_router
from apps.api.knowledge_routes import router as knowledge_router
from apps.api.metrics_routes import router as metrics_router
from apps.api.multipart_routes import router as multipart_router
from apps.api.object_transfer import make_download_url, make_upload_url
from apps.api.object_transfer import router as transfer_router
from apps.api.pricing_routes import router as pricing_router
from apps.api.rag_routes import router as rag_router
from apps.api.report_routes import router as report_router
from apps.api.research_routes import router as research_router
from apps.api.retained_upload_routes import router as retained_upload_router
from apps.api.review_routes import router as review_router
from apps.api.settings_routes import router as settings_router
from apps.api.source_effectivity_routes import router as effectivity_router
from apps.api.submission_routes import router as submission_router
from apps.api.task_routes import router as task_router
from apps.api.upload_layout_routes import router as upload_layout_router
from apps.api.views import (
    batch_view,
    bid_etag,
    bid_view,
    document_view,
    evidence_view,
    match_bid,
    operation_etag,
    operation_view,
    upload_row,
    upload_view,
)
from apps.api.word_intake_routes import router as word_router
from apps.api.word_material_routes import router as word_material_router
from packages.domain import reviews as review_domain
from packages.domain import storage, word_materials
from packages.domain.parsing_profile import configured_parser
from packages.domain.parsing_profile import profile as parser_profile
from packages.domain.reviews import on_material_commit
from packages.domain.runtime import (
    Command,
    DomainError,
    accepted,
    assert_role,
    audit,
    clean,
    connection,
    decode_cursor,
    encode_cursor,
    enqueue,
    enqueue_plan,
    etag,
    fetch,
    match,
    now,
    required,
    secret_hash,
    sha,
    uid,
    validate,
)
from packages.domain.work_access import begin as work_command
from packages.identity.repository import create_association, record_binding
from packages.tasks.inputs import review_guard
from packages.uploads import service as multipart
from packages.word_intake.inspection import DOCX, filename_type
from packages.word_intake.repository import assert_file_format, declare_pdf_source

review_domain.TASK_INPUT_GUARD = review_guard


@asynccontextmanager
async def lifespan(app):
    for name in ["APP_ORIGIN", "SESSION_SECRET", "CSRF_SECRET"]:
        required(name)
    assert_role()
    yield


class WorkbenchAPI(FastAPI):
    def openapi(self):
        return implemented_openapi(self)


app = WorkbenchAPI(title="AI投标工作台·PDF资料切片", lifespan=lifespan)
app.include_router(transfer_router)
app.include_router(rag_router)
app.include_router(review_router)
app.include_router(settings_router)
app.include_router(assistant_router)
app.include_router(comparison_router)
app.include_router(authoring_router)
app.include_router(research_router)
app.include_router(event_router)
app.include_router(history_router)
app.include_router(human_router)
app.include_router(knowledge_router)
app.include_router(effectivity_router)
app.include_router(multipart_router)
app.include_router(upload_layout_router)
app.include_router(identity_router)
app.include_router(pricing_router)
app.include_router(submission_router)
app.include_router(report_router)
app.include_router(metrics_router)
app.include_router(retained_upload_router)
app.include_router(task_router)


def word_capability(request: Request):
    auth(request, request.method != "GET")
    if request.method != "GET":
        word_materials.require_enabled()
    with connection() as c:
        if not word_materials.configured(c):
            raise DomainError("WORD_INTAKE_NOT_CONFIGURED", "Word接收协议尚未配置，请使用PDF", 503)


app.include_router(word_router, dependencies=[Depends(word_capability)])
app.include_router(word_material_router)

mount_composition(app)


@app.middleware("http")
async def request_context(request, call_next):
    request.state.request_id = str(uid())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "private,no-store"
    return response


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "request_id": request.state.request_id,
            "retryable": exc.retryable,
            "details": clean(exc.details),
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return await domain_error(
        request, DomainError("INVALID_REQUEST", "请求字段或对象ID格式不正确", 422)
    )


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    return await domain_error(
        request,
        DomainError(
            "DATABASE_TRANSACTION_FAILED", "资料事务未完成，请保留输入并重试", 503, retryable=True
        ),
    )


@app.exception_handler(ClientError)
@app.exception_handler(BotoCoreError)
async def storage_error(request, exc):
    return await domain_error(
        request,
        DomainError(
            "OBJECT_STORE_UNAVAILABLE", "对象存储暂不可用，请保留输入重试", 503, retryable=True
        ),
    )


def result(value, replay=False):
    headers = value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {})
    return JSONResponse(value["body"], status_code=value["status"], headers=headers)


def command(c, r, a, kind, target, body, conditional=False):
    if kind != "create_bid":
        batch = None
        if kind in ("create_batch", "create_upload"):
            bid = target
            if kind == "create_upload":
                batch = body["batch_id"]
        elif kind == "commit_batch":
            bid = fetch(c, "SELECT bid_id FROM upload_batches WHERE id=%s", (target,))["bid_id"]
            batch = target
        elif kind == "cancel_operation":
            bid = fetch(c, "SELECT bid_id FROM operations WHERE id=%s", (target,))["bid_id"]
        elif kind in ("complete_upload", "rebind_upload", "refresh_upload", "abort_upload"):
            upload = upload_row(c, target)
            bid, batch = upload["bid_id"], upload["batch_id"]
        else:
            raise DomainError("WORK_COMMAND_UNREGISTERED", "该写入入口尚未登记工作守卫", 503)
        return work_command(
            c,
            a["member_id"],
            kind,
            target,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match") if conditional else None,
            bid=bid,
            batch=batch,
            work=kind not in ("cancel_operation", "abort_upload"),
        )
    return Command(
        c,
        a["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match") if conditional else None,
    )


def verify_retained_object(c, upload):
    info = fetch(
        c,
        "SELECT b.object_key,b.digest,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
        (upload["verified_version_id"],),
    )
    try:
        metadata = storage.client().head_object(Bucket=storage.bucket(), Key=info["object_key"])
    except ClientError as error:
        if error.response["Error"]["Code"] in ["404", "NoSuchKey", "NotFound"]:
            raise DomainError(
                "VERIFIED_BYTES_EXPIRED", "已核验对象不可用，请重新上传", 410
            ) from None
        raise
    if metadata["ContentLength"] != info["byte_size"]:
        raise DomainError("VERIFIED_BYTES_CORRUPTED", "已核验对象大小不符，不能继续提交", 409)
    stored = metadata.get("Metadata", {}).get("sha256")
    if stored != info["digest"]:
        # 未提供可信checksum时回读核验，不用ETag冒充SHA。
        if sha(storage.get(info["object_key"])) != info["digest"]:
            raise DomainError("VERIFIED_BYTES_CORRUPTED", "已核验对象摘要不符，不能继续提交", 409)


def lock_bid(c, bid):
    return fetch(c, "SELECT * FROM bids WHERE id=%s FOR UPDATE", (bid,))


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    assert_role()
    return {
        "database": "ready",
        "model": "configured" if os.environ.get("DEEPSEEK_API_KEY") else "not_configured",
        "slice": "pdf-and-core-review",
    }


@app.post("/api/v1/auth/login")
def login(r: Request, body: dict = Body(...)):
    origin(r)
    validate("LoginRequest", body)
    with connection() as c:
        attempt_key = secret_hash(
            body["login_name"] + ":" + (r.client.host if r.client else "unknown")
        )
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (attempt_key,))
        failures = c.execute(
            "SELECT count(*) AS n FROM audit_entries WHERE event_type='auth.login_failed' AND payload->>'key'=%s AND created_at>now()-interval '15 minutes'",
            (attempt_key,),
        ).fetchone()["n"]
        if failures >= 10:
            raise DomainError("LOGIN_RATE_LIMITED", "登录尝试过多，请稍后重试", 429, retryable=True)
        row = c.execute(
            "SELECT c.*,m.display_name,m.active FROM credentials c JOIN members m ON m.id=c.member_id WHERE login_name=%s",
            (body["login_name"],),
        ).fetchone()
        if (
            not row
            or not row["active"]
            or row["disabled_at"]
            or not password_ok(body["password"], row["password_hash"])
        ):
            audit(c, None, None, "auth.login_failed", {"key": attempt_key})
            # 拒绝记录独立提交，不能被随后返回错误的事务回滚。
            c.commit()
            raise DomainError("INVALID_CREDENTIALS", "账号或密码不正确", 401)
        token = secrets.token_urlsafe(48)
        expires = now() + timedelta(hours=8)
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uid(),
                row["member_id"],
                secret_hash(token),
                hashlib.sha256(csrf(token).encode()).hexdigest(),
                expires,
            ),
        )
        row["expires_at"] = expires
        out = JSONResponse(auth_view(row, token))
        out.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=required("APP_ORIGIN").startswith("https://"),
            samesite="lax",
            max_age=28800,
            path="/",
        )
        return out


@app.get("/api/v1/auth/session")
def session(r: Request):
    return auth_view(auth(r), r.cookies[COOKIE])


@app.post("/api/v1/auth/logout")
def logout(r: Request):
    a = auth(r, True)
    if not r.headers.get("idempotency-key"):
        raise DomainError("IDEMPOTENCY_REQUIRED", "缺少操作幂等键", 428)
    with connection() as c:
        c.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (a["id"],))
    out = Response(status_code=204)
    out.delete_cookie(COOKIE, path="/")
    return out


@app.post("/api/v1/bids")
def create_bid(r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("CreateBid", body)
    with connection() as c:
        cmd = command(c, r, a, "create_bid", UUID(int=0), body)
        if cmd.replay:
            return result(cmd.replay, True)
        bid, stream = uid(), uid()
        identities = create_association(c, body)
        settings = {
            "auto_review": True,
            "consistency": True,
            "scope_residue": True,
            "show_prediction": True,
            "pricing": True,
            "result_reminder": True,
            "accept_rules_as_written": False,
        }
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,%s,%s)",
            (bid, body["title"], Jsonb(settings), a["member_id"]),
        )
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase,deadline) VALUES(%s,%s,%s,'preparation',%s)",
            (bid, identities["round_id"], identities["bidder_id"], body.get("deadline")),
        )
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (stream, bid))
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
        record_binding(
            c,
            bid,
            a["member_id"],
            cmd.id,
            reason="创建项目时明确选择投标主体" if identities["confirmed"] else "投标主体待确认",
            confirmed=identities["confirmed"],
        )
        audit(c, a["member_id"], bid, "bid.created", {"bid_id": bid}, cmd.id)
        return result(
            cmd.finish(
                bid_view(c, bid),
                201,
                {"ETag": bid_etag(c, bid), "Location": f"/api/v1/bids/{bid}"},
            )
        )


@app.get("/api/v1/bids")
def list_bids(r: Request, cursor: str | None = None, limit: int = Query(default=20, ge=1, le=100)):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if cursor:
            at, identity = decode_cursor(cursor)
            rows = c.execute(
                "SELECT b.id,b.created_at FROM bids b JOIN bid_profiles p ON p.bid_id=b.id JOIN stream_heads s ON s.bid_id=b.id JOIN review_slots r ON r.bid_id=b.id WHERE (b.created_at,b.id)<(%s,%s) ORDER BY b.created_at DESC,b.id DESC LIMIT %s",
                (at, identity, limit + 1),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT b.id,b.created_at FROM bids b JOIN bid_profiles p ON p.bid_id=b.id JOIN stream_heads s ON s.bid_id=b.id JOIN review_slots r ON r.bid_id=b.id ORDER BY b.created_at DESC,b.id DESC LIMIT %s",
                (limit + 1,),
            ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [bid_view(c, x["id"]) for x in items],
            "next_cursor": encode_cursor(items[-1]) if more else None,
            "has_more": more,
        }


@app.get("/api/v1/bids/{bid_id}")
def get_bid(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = bid_view(c, bid_id)
        return JSONResponse(body, headers={"ETag": bid_etag(c, bid_id, body)})


@app.post("/api/v1/bids/{bid_id}/upload-batches")
def create_batch(bid_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("UploadBatch", body)
    with connection() as c:
        cmd = command(c, r, a, "create_batch", bid_id, body)
        if cmd.replay:
            return result(cmd.replay, True)
        lock_bid(c, bid_id)
        batch = uid()
        c.execute(
            "INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,'open',%s)",
            (batch, bid_id, a["member_id"]),
        )
        return result(
            cmd.finish(
                batch_view(c, bid_id, batch),
                201,
                {"Location": f"/api/v1/bids/{bid_id}/upload-batches/{batch}"},
            )
        )


@app.get("/api/v1/bids/{bid_id}/upload-batches/{batch_id}")
def get_batch(bid_id: UUID, batch_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        return batch_view(c, bid_id, batch_id)


@app.post("/api/v1/bids/{bid_id}/uploads")
def create_upload(bid_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("UploadRequest", body)
    if multipart.wants_multipart(body):
        return result(multipart.create_upload(bid_id, r, a, body))
    media_type = filename_type(body["filename"])
    if media_type == DOCX or body.get("declared_source_document_version_id"):
        word_materials.require_enabled()
    if body["byte_size"] > int(os.environ.get("MAX_UPLOAD_BYTES", "104857600")):
        raise DomainError("UPLOAD_TOO_LARGE", "文件超出当前上传大小上限", 413)
    storage.client()
    with connection() as c:
        cmd = command(c, r, a, "create_upload", bid_id, body)
        if cmd.replay:
            return result(cmd.replay, True)
        lock_bid(c, bid_id)
        batch = fetch(
            c,
            "SELECT * FROM upload_batches WHERE id=%s AND bid_id=%s FOR UPDATE",
            (body["batch_id"], bid_id),
        )
        if batch["state"] != "open":
            raise DomainError("BATCH_CLOSED", "该批次已结束", 409)
        if media_type == DOCX or body.get("declared_source_document_version_id"):
            word_materials.require_configured(c)
        target = body["target"]
        file_id = uid()
        if target["kind"] == "new_version":
            file_id = UUID(target["file_id"])
            f = fetch(
                c,
                "SELECT * FROM document_files WHERE id=%s FOR UPDATE",
                (file_id,),
            )
            if f["bid_id"] != bid_id or f["role"] != body["role"]:
                raise DomainError("UPLOAD_TARGET_MISMATCH", "新版本资料角色与目标文件不一致", 409)
            assert_file_format(c, file_id, media_type)
            expected = target["expected_file_revision"]
            if f["revision"] != expected:
                raise DomainError("FILE_REVISION_CONFLICT", "文件版本已更新，请重新选择", 412)
        else:
            expected = 1
            c.execute(
                "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
                (file_id, bid_id, body["role"]),
            )
        upload, binding = uid(), uid()
        expires = now() + timedelta(hours=24)
        key = f"staging/{bid_id}/{upload}"
        c.execute(
            "INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'authorized',%s)",
            (
                upload,
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
                upload,
                file_id,
                expected,
                "首次明确上传目标",
                cmd.id,
                a["member_id"],
            ),
        )
        c.execute(
            "INSERT INTO upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,part_size,part_count,revision,current_binding_id) VALUES(%s,%s,%s,%s,'single',%s,1,1,%s)",
            (upload, bid_id, file_id, expected, body["byte_size"], binding),
        )
        if body.get("declared_source_document_version_id"):
            declare_pdf_source(
                c,
                bid_id,
                upload,
                body["declared_source_document_version_id"],
                a["member_id"],
                cmd.id,
            )
        grant = {
            "upload_id": str(upload),
            "object_ref": str(upload),
            "file_id": str(file_id),
            "mode": "single",
            "upload_url": make_upload_url(
                r,
                upload,
                session=a,
                bid_id=bid_id,
                staging_key=key,
                byte_size=body["byte_size"],
                revision=1,
                expires_at=min(now() + timedelta(minutes=15), expires),
            )
            if os.environ.get("OBJECT_STORE_BACKEND") == "oss"
            else storage.sign(key, "put_object"),
            "part_size": body["byte_size"],
            "part_count": 1,
            "parts": [],
            "expires_at": (now() + timedelta(minutes=15)).isoformat(),
            "session_expires_at": expires.isoformat(),
            "revision": 1,
        }
        return result(cmd.finish(grant, 200, {"ETag": etag("upload", upload, 1)}))


@app.get("/api/v1/uploads/{upload_id}")
def get_upload(upload_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        b = upload_view(c, upload_id)
        return JSONResponse(b, headers={"ETag": etag("upload", upload_id, b["revision"])})


@app.post("/api/v1/uploads/{upload_id}/complete")
def complete(upload_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("UploadComplete", body)
    if multipart.is_multipart(upload_id):
        return result(multipart.complete(upload_id, r, a, body))
    with connection() as c:
        cmd = command(c, r, a, "complete_upload", upload_id, body)
        if cmd.replay:
            return result(cmd.replay, True)
        original = upload_row(c, upload_id)
        lock_bid(c, original["bid_id"])
        u = upload_row(c, upload_id, True)
        if u["state"] != "authorized" or u["aborted_at"] or u["expires_at"] <= now():
            raise DomainError("UPLOAD_NOT_COMPLETABLE", "上传不可完成或已过期", 409)
        if body["parts"] or body["declared_sha256"] != u["declared_digest"]:
            raise DomainError("UPLOAD_RECEIPT_INVALID", "单文件完成声明不匹配", 422)
        c.execute("UPDATE uploads SET state='verifying' WHERE id=%s", (upload_id,))
        c.execute(
            "UPDATE upload_transfers SET revision=revision+1 WHERE upload_id=%s", (upload_id,)
        )
        op = enqueue(
            c,
            u["bid_id"],
            cmd.id,
            "verify-upload",
            {"id": upload_id, "digest": u["declared_digest"]},
        )
        return result(cmd.finish(accepted(op, upload_id, "upload"), 202))


@app.post("/api/v1/uploads/{upload_id}/bindings")
def rebind(upload_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("UploadBindingRequest", body)
    with connection() as c:
        cmd = command(c, r, a, "rebind_upload", upload_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        c.rollback()  # 字节复核不持bid/Command锁；发布时重新检查同一命令。
        c.execute("SET LOCAL search_path=design,public")
        old = upload_row(c, upload_id)
        if old["state"] == "verified":
            verify_retained_object(c, old)
        cmd = command(c, r, a, "rebind_upload", upload_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        lock_bid(c, old["bid_id"])
        u = upload_row(c, upload_id, True)
        match(r.headers.get("if-match"), "upload", upload_id, u["revision"])
        if u["state"] != "verified" or u["committed_binding_id"]:
            raise DomainError(
                "UPLOAD_NOT_REBINDABLE", "只有未入库的已核验上传可以重新选择目标", 409
            )
        if u["verified_retain_until"] <= now():
            raise DomainError("VERIFIED_BYTES_EXPIRED", "已核验字节保留期已过，请重新上传", 410)
        target = body["target"]
        file_id = uid()
        if target["kind"] == "new_version":
            f = fetch(
                c,
                "SELECT * FROM document_files WHERE id=%s FOR UPDATE",
                (target["file_id"],),
            )
            if f["bid_id"] != u["bid_id"] or f["role"] != u["role"]:
                raise DomainError("UPLOAD_TARGET_MISMATCH", "文件资料角色不一致", 409)
            expected = target["expected_file_revision"]
            file_id = f["id"]
            assert_file_format(c, file_id, filename_type(u["filename"]))
            if expected != f["revision"]:
                raise DomainError("FILE_REVISION_CONFLICT", "文件版本已更新", 412)
        else:
            expected = 1
            c.execute(
                "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
                (file_id, u["bid_id"], u["role"]),
            )
        binding = uid()
        revision = u["revision"] + 1
        c.execute(
            "INSERT INTO upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,previous_binding_id,reason,command_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                binding,
                u["bid_id"],
                upload_id,
                file_id,
                expected,
                u["current_binding_id"],
                body["reason"],
                cmd.id,
                a["member_id"],
            ),
        )
        c.execute(
            "UPDATE upload_transfers SET current_binding_id=%s,revision=%s WHERE upload_id=%s",
            (binding, revision, upload_id),
        )
        audit(
            c,
            a["member_id"],
            u["bid_id"],
            "upload.target_reconfirmed",
            {
                "old_binding": u["current_binding_id"],
                "new_binding": binding,
                "reason": body["reason"],
            },
            cmd.id,
        )
        response = clean(
            {
                "id": binding,
                "upload_id": upload_id,
                "bid_id": u["bid_id"],
                "file_id": file_id,
                "expected_file_revision": expected,
                "previous_binding_id": u["current_binding_id"],
                "upload_revision": revision,
                "verified_retain_until": u["verified_retain_until"],
            }
        )
        return result(cmd.finish(response, 200, {"ETag": etag("upload", upload_id, revision)}))


@app.post("/api/v1/bids/{bid_id}/upload-batches/{batch_id}/commit")
def commit_batch(bid_id: UUID, batch_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("BatchCommit", body)
    with connection() as c:
        cmd = command(c, r, a, "commit_batch", batch_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        c.rollback()
        c.execute("SET LOCAL search_path=design,public")
        ready_word_pdfs = {}
        for upload_id in body["accepted_upload_ids"]:
            retained = upload_row(c, UUID(upload_id))
            if retained["bid_id"] != bid_id or str(retained["batch_id"]) != str(batch_id):
                raise DomainError("UPLOAD_TARGET_MISMATCH", "上传不属于本项目和批次", 409)
            if retained["state"] == "verified":
                verify_retained_object(c, retained)
                if filename_type(retained["filename"]) == DOCX:
                    possible = [retained["verified_version_id"]]
                    head = c.execute(
                        "SELECT head_version_id FROM document_files WHERE id=%s",
                        (retained["file_id"],),
                    ).fetchone()
                    if head and head["head_version_id"]:
                        possible.append(head["head_version_id"])
                    for source in possible:
                        pdf = word_materials.candidate(c, bid_id, source)
                        if pdf:
                            c.rollback()
                            ready_word_pdfs[str(source)] = word_materials.verify_candidate(pdf)
                            c.execute("SET LOCAL search_path=design,public")
        c.rollback()
        parser = configured_parser()  # HTTP identity在bid锁外读取，未配置保持保守字层解析。
        render_profile = None
        if ready_word_pdfs:
            from packages.word_intake.renderer import configured_profile

            try:
                render_profile = configured_profile()
            except DomainError:
                ready_word_pdfs.clear()
        cmd = command(c, r, a, "commit_batch", batch_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        bid = lock_bid(c, bid_id)
        match_bid(c, r.headers.get("if-match"), bid_id)
        batch = fetch(
            c,
            "SELECT * FROM upload_batches WHERE id=%s AND bid_id=%s FOR UPDATE",
            (batch_id, bid_id),
        )
        if batch["state"] != "open":
            raise DomainError("BATCH_CLOSED", "批次已提交或不可继续", 409)
        uploads = [upload_row(c, UUID(x), True) for x in body["accepted_upload_ids"]]
        if any(x["bid_id"] != bid_id or x["batch_id"] != batch_id for x in uploads):
            raise DomainError("UPLOAD_TARGET_MISMATCH", "上传不属于本项目和批次", 409)
        if set(map(str, [x["current_binding_id"] for x in uploads])) != set(
            body["accepted_binding_ids"]
        ):
            raise DomainError("UPLOAD_BINDING_CHANGED", "文件目标已变化，请重新确认", 412)
        all_count = c.execute(
            "SELECT count(*) AS n FROM uploads WHERE batch_id=%s", (batch_id,)
        ).fetchone()["n"]
        if len(uploads) != all_count and not body["explicit_partial_acceptance"]:
            raise DomainError("PARTIAL_ACCEPTANCE_REQUIRED", "请明确选择接收本批成功文件", 409)
        if any(x["state"] != "verified" or x["committed_binding_id"] for x in uploads):
            raise DomainError("UPLOAD_NOT_VERIFIED", "所选文件尚未核验或已提交", 409)
        if any(x["verified_retain_until"] <= now() for x in uploads):
            raise DomainError("VERIFIED_BYTES_EXPIRED", "已核验字节保留期已过", 410)
        if len({x["file_id"] for x in uploads}) != len(uploads):
            raise DomainError(
                "DUPLICATE_FILE_TARGET", "同一批次不能同时提交同一文件的两个新版本", 409
            )
        if sum(x["role"] == "body" for x in uploads) > 1:
            raise DomainError("BODY_SELECTION_REQUIRED", "请明确选择一份本轮PDF正文", 409)
        profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s FOR UPDATE", (bid_id,))
        selected = []
        for u in sorted(uploads, key=lambda x: str(x["file_id"])):
            f = fetch(
                c,
                "SELECT * FROM document_files WHERE bid_id=%s AND id=%s FOR UPDATE",
                (bid_id, u["file_id"]),
            )
            if f["revision"] != u["expected_file_revision"]:
                raise DomainError(
                    "FILE_REVISION_CONFLICT",
                    "其他人已提交此文件新版本，请重新选择目标",
                    412,
                    {"file_id": str(f["id"]), "current_revision": f["revision"]},
                )
            version = u["verified_version_id"]
            assert_file_format(c, f["id"], filename_type(u["filename"]))
            old = (
                c.execute(
                    "SELECT blob_id FROM document_versions WHERE id=%s", (f["head_version_id"],)
                ).fetchone()
                if f["head_version_id"]
                else None
            )
            new = fetch(c, "SELECT blob_id FROM document_versions WHERE id=%s", (version,))
            if old and old["blob_id"] == new["blob_id"]:
                version = f["head_version_id"]
            else:
                c.execute(
                    "INSERT INTO document_file_versions(bid_id,file_id,version_id,revision,previous_version_id) VALUES(%s,%s,%s,%s,%s)",
                    (bid_id, f["id"], version, f["revision"], f["head_version_id"]),
                )
                c.execute(
                    "UPDATE document_files SET head_version_id=%s,revision=revision+1 WHERE id=%s",
                    (version, f["id"]),
                )
            selected.append({"document_version_id": version, "role": u["role"], "file_id": f["id"]})
            c.execute(
                "UPDATE upload_transfers SET committed_binding_id=current_binding_id,revision=revision+1 WHERE upload_id=%s",
                (u["id"],),
            )
        prior = (
            c.execute(
                "SELECT a.document_version_id,a.role,f.file_id FROM material_assignments a JOIN document_file_versions f ON f.version_id=a.document_version_id WHERE a.snapshot_id=%s ORDER BY a.role,a.ordinal",
                (profile["current_material_id"],),
            ).fetchall()
            if profile["current_material_id"]
            else []
        )
        replace_files = {x["file_id"] for x in selected}
        new_body = next((x["document_version_id"] for x in selected if x["role"] == "body"), None)
        entries = [
            x
            for x in prior
            if x["file_id"] not in replace_files and not (new_body and x["role"] == "body")
        ] + selected
        snapshot = uid()
        revision = bid["material_revision"] + 1
        manifest = clean(entries)
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,%s,%s)",
            (snapshot, bid_id, revision, Jsonb({"documents": manifest}), sha(manifest)),
        )
        counters: dict[str, int] = {}
        for entry in entries:
            role = entry["role"]
            ordinal = counters.get(role, 0)
            counters[role] = ordinal + 1
            c.execute(
                "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,%s)",
                (snapshot, bid_id, entry["document_version_id"], role, ordinal),
            )
        c.execute(
            "UPDATE bids SET revision=revision+1,material_revision=%s WHERE id=%s",
            (revision, bid_id),
        )
        body_is_word = bool(
            new_body
            and fetch(
                c,
                "SELECT b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
                (new_body,),
            )["media_type"]
            == DOCX
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s,current_body_id=CASE WHEN %s THEN NULL WHEN %s::uuid IS NOT NULL THEN %s::uuid ELSE current_body_id END,current_review_id=NULL WHERE bid_id=%s",
            (snapshot, body_is_word, new_body, new_body, bid_id),
        )
        for u in uploads:
            if filename_type(u["filename"]) == DOCX:
                selected_word = next(
                    x["document_version_id"] for x in selected if x["file_id"] == u["file_id"]
                )
                word_materials.record_selection(c, bid_id, selected_word, a["member_id"], cmd.id)
        c.execute(
            "UPDATE upload_batches SET state='committed',revision=revision+1 WHERE id=%s",
            (batch_id,),
        )
        payload = {"id": snapshot, "digest": sha(manifest)}
        if parser is None:
            op = enqueue(c, bid_id, cmd.id, "parse-materials", payload)
        else:
            op = enqueue_plan(
                c,
                bid_id,
                cmd.id,
                "parse-materials",
                payload,
                stages=[{"key": "parse-materials", "depends_on": []}],
                handler_version="document/2",
                topic="tender.task.document.v1",
                input_digest=sha(payload | {"parse_profile": parser_profile(parser)}),
            )
        request_intent = on_material_commit(c, bid_id, cmd.id)
        audit(
            c,
            a["member_id"],
            bid_id,
            "materials.committed",
            {
                "snapshot_id": snapshot,
                "review_request_id": request_intent["resource_id"] if request_intent else None,
            },
            cmd.id,
        )
        from packages.tasks.coordinator import material_committed

        event = fetch(
            c,
            "SELECT id FROM audit_entries WHERE bid_id=%s AND command_id=%s AND event_type='materials.committed'",
            (bid_id, cmd.id),
        )
        material_committed(c, bid=bid_id, event_id=event["id"])
        for entry in selected:
            verified = ready_word_pdfs.get(str(entry["document_version_id"]))
            if verified:
                adopted = word_materials.adopt(
                    c,
                    bid_id,
                    entry["document_version_id"],
                    verified,
                    parser,
                    render_profile=render_profile,
                )
                if adopted["state"] == "adopted":
                    snapshot = adopted["material_snapshot_id"]
                    op = operation_view(c, adopted["parse_operation_id"])
        return result(
            cmd.finish(
                accepted(op, snapshot, "ingestion"),
                202,
                {"ETag": bid_etag(c, bid_id)},
            )
        )


@app.get("/api/v1/bids/{bid_id}/materials")
def materials(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        b = fetch(
            c,
            "SELECT b.material_revision,p.current_material_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s",
            (bid_id,),
        )
        rows = c.execute(
            "SELECT document_version_id FROM material_assignments WHERE snapshot_id=%s ORDER BY role,ordinal",
            (b["current_material_id"],),
        ).fetchall()
        return clean(
            {
                "bid_id": bid_id,
                "material_revision": b["material_revision"],
                "snapshot_id": b["current_material_id"],
                "documents": [document_view(c, row) for row in rows],
                "conflicts": [],
            }
        )


@app.get("/api/v1/operations/{operation_id}")
def operation(operation_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = operation_view(c, operation_id)
        return JSONResponse(body, headers={"ETag": operation_etag(c, operation_id, body)})


@app.post("/api/v1/operations/{operation_id}/cancel")
def cancel(operation_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("Cancellation", body)
    with connection() as c:
        cmd = command(c, r, a, "cancel_operation", operation_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        op = fetch(c, "SELECT * FROM operations WHERE id=%s", (operation_id,))
        lock_bid(c, op["bid_id"])
        op = fetch(c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (operation_id,))
        c.execute(
            "SELECT step_key FROM operation_steps WHERE operation_id=%s ORDER BY step_key,input_digest FOR UPDATE",
            (operation_id,),
        ).fetchall()
        c.execute(
            "SELECT step_key FROM step_receipts WHERE operation_id=%s ORDER BY step_key,input_digest FOR UPDATE",
            (operation_id,),
        ).fetchall()
        current_tag = operation_etag(c, operation_id)
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请读取最新操作状态后取消", 428)
        if r.headers["if-match"] != current_tag:
            raise DomainError(
                "REVISION_CONFLICT",
                "操作状态或执行代次已变化，请核对后重试",
                412,
                {"current_etag": current_tag},
            )
        if op["state"] not in ["succeeded", "failed", "cancelled"]:
            c.execute(
                "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                (operation_id,),
            )
            c.execute(
                "UPDATE step_receipts SET lease_until=now() WHERE operation_id=%s AND state='running'",
                (operation_id,),
            )
            audit(
                c,
                a["member_id"],
                op["bid_id"],
                "operation.cancel_requested",
                {"operation_id": operation_id},
                cmd.id,
            )
        body = operation_view(c, operation_id)
        return result(cmd.finish(body, headers={"ETag": operation_etag(c, operation_id, body)}))


@app.get("/api/v1/documents/{document_version_id}")
def document_metadata(document_version_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        body = document_view(c, {"document_version_id": document_version_id})
        return body


@app.get("/api/v1/documents/{document_version_id}/evidence")
def document_evidence(document_version_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        fetch(c, "SELECT id FROM document_versions WHERE id=%s", (document_version_id,))
        parsed = c.execute(
            "SELECT * FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (document_version_id,),
        ).fetchone()
        ids = (
            c.execute(
                "SELECT id FROM evidence_blocks WHERE parse_id=%s ORDER BY physical_page,ordinal",
                (parsed["id"],),
            ).fetchall()
            if parsed
            else []
        )
        return clean(
            {
                "document_version_id": document_version_id,
                "parse_run_id": parsed["id"] if parsed else None,
                "coverage": parsed["coverage"] if parsed else "pending",
                "gaps": parsed["gaps"] if parsed else [],
                "items": [evidence_view(c, x["id"]) for x in ids],
            }
        )


@app.get("/api/v1/artifacts/{artifact_id}/download")
def download(artifact_id: UUID, r: Request):
    a = auth(r)
    with connection() as c:
        doc = fetch(
            c,
            "SELECT v.*,b.object_key,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
            (artifact_id,),
        )
        return {
            "artifact_id": str(artifact_id),
            "download_url": make_download_url(
                r,
                artifact_id,
                session=a,
                object_key=doc["object_key"],
                filename=doc["original_name"],
                byte_size=doc["byte_size"],
            )
            if os.environ.get("OBJECT_STORE_BACKEND") == "oss"
            else storage.sign(doc["object_key"]),
            "expires_at": (now() + timedelta(minutes=15)).isoformat(),
            "filename": doc["original_name"],
            "artifact_kind": doc["artifact_kind"],
            "presentation_revision": None,
        }


@app.get("/api/v1/uploads/{upload_id}/parts")
def upload_parts(upload_id: UUID, r: Request):
    a = auth(r)
    if multipart.is_multipart(upload_id):
        return multipart.get_parts(upload_id, a)
    with connection() as c:
        u = upload_row(c, upload_id)
    parts = []
    try:
        head = storage.client().head_object(Bucket=storage.bucket(), Key=u["staging_key"])
        if head["ContentLength"] == u["declared_size"]:
            parts = [{"part_number": 1, "etag": head["ETag"], "byte_size": head["ContentLength"]}]
    except ClientError as error:
        if error.response["Error"]["Code"] not in ["404", "NoSuchKey", "NotFound"]:
            raise
    return clean(
        {
            "upload_id": upload_id,
            "revision": u["revision"],
            "mode": "single",
            "completed_parts": parts,
            "session_expires_at": u["expires_at"],
        }
    )


@app.post("/api/v1/uploads/{upload_id}/authorizations")
def refresh_upload(upload_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("RefreshUploadRequest", body)
    if multipart.is_multipart(upload_id):
        return result(multipart.refresh(upload_id, r, a, body))
    if body["part_numbers"] != [1]:
        raise DomainError("INVALID_PART", "单传只支持片号1", 422)
    with connection() as c:
        cmd = command(c, r, a, "refresh_upload", upload_id, body, True)
        if cmd.replay:
            return result(cmd.replay, True)
        old = upload_row(c, upload_id)
        lock_bid(c, old["bid_id"])
        u = upload_row(c, upload_id, True)
        match(r.headers.get("if-match"), "upload", upload_id, u["revision"])
        if u["expires_at"] <= now():
            raise DomainError("UPLOAD_EXPIRED", "上传会话已过期，请重新创建", 410)
        if u["state"] != "authorized" or u["aborted_at"]:
            raise DomainError("UPLOAD_AUTHORIZATION_CLOSED", "已完成或终止的上传不可重新授权", 409)
        revision = u["revision"] + 1
        c.execute(
            "UPDATE upload_transfers SET revision=%s WHERE upload_id=%s", (revision, upload_id)
        )
        expires = min(now() + timedelta(minutes=15), u["expires_at"])
        grant = {
            "upload_id": str(upload_id),
            "object_ref": str(upload_id),
            "file_id": str(u["file_id"]),
            "mode": "single",
            "upload_url": make_upload_url(
                r,
                upload_id,
                session=a,
                bid_id=u["bid_id"],
                staging_key=u["staging_key"],
                byte_size=u["part_size"],
                revision=revision,
                expires_at=expires,
            )
            if os.environ.get("OBJECT_STORE_BACKEND") == "oss"
            else storage.sign(
                u["staging_key"],
                "put_object",
                seconds=max(1, int((expires - now()).total_seconds())),
            ),
            "part_size": u["part_size"],
            "part_count": 1,
            "parts": [],
            "expires_at": expires.isoformat(),
            "session_expires_at": u["expires_at"].isoformat(),
            "revision": revision,
        }
        return result(cmd.finish(grant, 200, {"ETag": etag("upload", upload_id, revision)}))


@app.post("/api/v1/uploads/{upload_id}/abort")
def abort_upload(upload_id: UUID, r: Request):
    a = auth(r, True)
    if multipart.is_multipart(upload_id):
        return result(multipart.abort(upload_id, r, a))
    with connection() as c:
        cmd = command(c, r, a, "abort_upload", upload_id, {}, True)
        if cmd.replay:
            return result(cmd.replay, True)
        old = upload_row(c, upload_id)
        lock_bid(c, old["bid_id"])
        u = upload_row(c, upload_id, True)
        match(r.headers.get("if-match"), "upload", upload_id, u["revision"])
        if u["state"] in ["verifying", "verified"]:
            raise DomainError("UPLOAD_AUTHORIZATION_CLOSED", "核验中的原件不能终止删除", 409)
        c.execute(
            "UPDATE upload_transfers SET aborted_at=now(),revision=revision+1 WHERE upload_id=%s",
            (upload_id,),
        )
        audit(c, a["member_id"], u["bid_id"], "upload.aborted", {"upload_id": upload_id}, cmd.id)
        return result(
            cmd.finish(
                upload_view(c, upload_id),
                200,
                {"ETag": etag("upload", upload_id, u["revision"] + 1)},
            )
        )
