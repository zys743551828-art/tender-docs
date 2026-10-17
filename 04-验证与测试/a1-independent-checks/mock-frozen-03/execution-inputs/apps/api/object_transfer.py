"""同源私有原件传输；短票据与Cookie共同授权，不向浏览器暴露云签名。"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from apps.api.auth import auth
from apps.api.views import upload_row
from packages.domain import storage
from packages.domain.runtime import DomainError, connection, fetch, now, secret_hash

router = APIRouter(tags=["Private object transfers"])
_BASE = "/api/v1/object-transfers"
_CHUNK = 64 * 1024


def _limits():
    try:
        maximum = int(os.environ.get("OBJECT_TRANSFER_MAX_BYTES", "104857600"))
        deadline = int(os.environ.get("OBJECT_TRANSFER_DEADLINE_SECONDS", "300"))
        upload_max = int(os.environ.get("MAX_UPLOAD_BYTES", "104857600"))
        if not 1 <= maximum <= 268435456 or not 1 <= deadline <= 900 or upload_max < 1:
            raise ValueError
        return min(maximum, upload_max), deadline
    except ValueError:
        raise DomainError("OBJECT_TRANSFER_CONFIG_INVALID", "原件传输限制配置不合法", 503) from None


def _identity(session):
    return {"session": str(session["id"]), "member": str(session["member_id"])}


def _configured_origin():
    """仅使用部署时受信Origin，不读取Host或代理转发头。"""
    value = os.environ.get("APP_ORIGIN", "")
    try:
        if not value or not value.isascii() or any(c.isspace() for c in value):
            raise ValueError
        if any(c in value for c in "\\%?#") or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.path
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError
        host = parts.hostname
        try:
            address = ipaddress.ip_address(host)
            serialized_host = f"[{address.compressed}]" if address.version == 6 else str(address)
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host) or len(host) > 253:
                raise ValueError from None
            if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.rsplit(".", 1)[-1]):
                raise ValueError from None
            if not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                raise ValueError from None
            serialized_host = host
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if port in ({"http": 80, "https": 443}[parts.scheme], None):
            authority = serialized_host
        else:
            authority = f"{serialized_host}:{port}"
        # Cookie/Origin校验使用配置原文；要求与浏览器序列化的Origin一致。
        if value != f"{parts.scheme}://{authority}":
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise DomainError(
            "OBJECT_TRANSFER_CONFIG_INVALID", "APP_ORIGIN须为合法且不含路径的规范HTTP(S)源", 503
        ) from None


def _upload_claims(upload_id, session, bid_id, key, byte_size, revision):
    return {
        **_identity(session),
        "kind": "upload",
        "id": str(upload_id),
        "bid": str(bid_id),
        "key": key,
        "size": int(byte_size),
        "revision": int(revision),
        "media": "application/pdf",
    }


def _download_claims(version_id, session, key, filename, byte_size):
    return {
        **_identity(session),
        "kind": "download",
        "id": str(version_id),
        "key": key,
        "name": filename,
        "size": int(byte_size),
    }


def _signature(claims, expiry, nonce):
    return secret_hash(
        "object-transfer/v1:"
        + json.dumps([claims, expiry, nonce], sort_keys=True, separators=(",", ":"))
    )


def _ticket(claims, session, expires_at=None):
    expiry = min(now() + timedelta(minutes=15), session["expires_at"])
    if expires_at is not None:
        expiry = min(expiry, expires_at)
    stamp = int(expiry.timestamp())
    if stamp <= int(now().timestamp()):
        raise DomainError("TRANSFER_TICKET_EXPIRED", "传输授权已过期，请刷新授权", 410)
    nonce = secrets.token_hex(16)
    return f"{stamp}.{nonce}.{_signature(claims, stamp, nonce)}"


def _verify(ticket, claims):
    try:
        if len(ticket) > 160:
            raise ValueError
        stamp, nonce, signature = ticket.split(".")
        expiry = int(stamp)
        if len(nonce) != 32 or len(signature) != 64:
            raise ValueError
        if not hmac.compare_digest(_signature(claims, expiry, nonce), signature):
            raise ValueError
        if expiry <= int(now().timestamp()):
            raise DomainError("TRANSFER_TICKET_EXPIRED", "传输授权已过期，请刷新授权", 410)
    except (ValueError, TypeError):
        raise DomainError(
            "TRANSFER_TICKET_INVALID", "传输授权已失效，请刷新上传或下载状态", 403
        ) from None


def make_upload_url(
    request: Request,
    upload_id,
    *,
    session,
    bid_id,
    staging_key,
    byte_size,
    revision,
    expires_at,
):
    """在创建/刷新授权的既有事务中调用，不另开事务读取尚未提交的数据。"""
    maximum, _ = _limits()
    if not 0 < byte_size <= maximum:
        raise DomainError("UPLOAD_TOO_LARGE", "文件超出当前上传大小上限", 413)
    claims = _upload_claims(upload_id, session, bid_id, staging_key, byte_size, revision)
    return f"{_configured_origin()}{_BASE}/uploads/{upload_id}?ticket={_ticket(claims, session, expires_at)}"


def make_download_url(
    request: Request,
    version_id,
    *,
    session,
    object_key,
    filename,
    byte_size,
    expires_at: datetime | None = None,
):
    claims = _download_claims(version_id, session, object_key, filename, byte_size)
    return f"{_configured_origin()}{_BASE}/versions/{version_id}?ticket={_ticket(claims, session, expires_at)}"


def _check_upload(c, upload_id, session, ticket, *, lock=False):
    row = upload_row(c, upload_id)
    if lock:
        # 与complete/refresh/abort保持bid -> upload/transfers锁顺序。
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (row["bid_id"],))
        row = upload_row(c, upload_id, True)
    batch = fetch(
        c,
        "SELECT state FROM upload_batches WHERE id=%s AND bid_id=%s",
        (row["batch_id"], row["bid_id"]),
    )
    if row["expires_at"] <= now():
        raise DomainError("UPLOAD_EXPIRED", "上传会话已过期，请重新创建", 410)
    if row["state"] != "authorized" or row["aborted_at"] or batch["state"] != "open":
        raise DomainError("UPLOAD_AUTHORIZATION_CLOSED", "上传授权已结束，请刷新上传状态", 409)
    base = f"staging/{row['bid_id']}/{upload_id}"
    key = row["staging_key"]
    if key != base:
        try:
            if not key.startswith(base + "/transfers/"):
                raise ValueError
            UUID(key.removeprefix(base + "/transfers/"))
        except ValueError:
            raise DomainError("UPLOAD_LOCATOR_INVALID", "上传暂存位置不合法", 409) from None
    _verify(
        ticket,
        _upload_claims(
            upload_id, session, row["bid_id"], key, row["declared_size"], row["revision"]
        ),
    )
    maximum, _ = _limits()
    if not 0 < row["declared_size"] <= maximum:
        raise DomainError("UPLOAD_TOO_LARGE", "文件超出当前上传大小上限", 413)
    return row


def _upload_snapshot(upload_id, request, ticket):
    session = auth(request, True)
    with connection() as c:
        return _check_upload(c, upload_id, session, ticket)


def _publish_upload(upload_id, request, ticket, key):
    session = auth(request, True)
    with connection() as c:
        row = _check_upload(c, upload_id, session, ticket, lock=True)
        # 只有状态仍然authorized且原ticket与当前revision/key一致时，才公布本次独立key。
        c.execute(
            "UPDATE uploads SET staging_key=%s WHERE id=%s AND staging_key=%s AND state='authorized'",
            (key, upload_id, row["staging_key"]),
        )


def _store_file(file, key):
    file.seek(0)
    return storage.put(key, file.read(), "application/pdf")


def _register_temporary_key(key):
    """登记本系统待核对候选；GC必须确认不被uploads/blobs引用，不能直接按表删除。"""
    directory = Path(__file__).resolve().parents[2] / ".local"
    directory.mkdir(mode=0o700, exist_ok=True)
    record = (
        json.dumps(
            {
                "backend": os.environ.get("OBJECT_STORE_BACKEND", ""),
                "bucket": os.environ.get("OBJECT_STORE_BUCKET", ""),
                "key": key,
                "prefix": os.environ.get("OBJECT_STORE_PREFIX", ""),
                "created_at": now().isoformat(),
                "state": "requires_database_reference_check",
            },
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    descriptor = os.open(
        directory / "object-transfer-temporary-keys.jsonl",
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, record)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@router.put(
    _BASE + "/uploads/{upload_id}",
    operation_id="putPrivateUploadBytes",
    status_code=200,
    response_class=Response,
    openapi_extra={
        "security": [{"SessionCookie": []}],
        "requestBody": {
            "required": True,
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        "responses": {
            "200": {"description": "已暂存；仍需complete与Worker实际SHA核验"},
            "default": {"$ref": "#/components/responses/DomainError"},
        },
    },
)
async def upload_bytes(upload_id: UUID, request: Request, ticket: str = Query(..., max_length=160)):
    row = await run_in_threadpool(_upload_snapshot, upload_id, request, ticket)
    if request.headers.get("content-type", "").lower() != "application/pdf":
        raise DomainError("PDF_REQUIRED", "原件传输仅接收application/pdf", 415)
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise DomainError("CONTENT_ENCODING_UNSUPPORTED", "原件传输不支持压缩编码", 415)
    try:
        length = int(request.headers["content-length"])
    except (KeyError, ValueError):
        raise DomainError("CONTENT_LENGTH_REQUIRED", "请提供准确的原件字节数", 411) from None
    if length != row["declared_size"]:
        raise DomainError("UPLOAD_SIZE_MISMATCH", "原件字节数与上传声明不符", 422)
    maximum, deadline = _limits()
    if length > maximum:
        raise DomainError("UPLOAD_TOO_LARGE", "文件超出当前上传大小上限", 413)
    count = 0
    # 每次请求写独立key。即使并发complete/abort/refresh，云网络期间也不覆盖旧暂存或正式blob。
    key = f"staging/{row['bid_id']}/{upload_id}/transfers/{uuid4()}"
    try:
        async with asyncio.timeout(deadline):
            with tempfile.TemporaryFile(mode="w+b") as file:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > maximum or count > length:
                        raise DomainError("UPLOAD_TOO_LARGE", "实际原件字节数超出上传限制", 413)
                    await run_in_threadpool(file.write, chunk)
                if count != length:
                    raise DomainError("UPLOAD_SIZE_MISMATCH", "原件传输未完成，请重试", 422)
                file.seek(0)
                if file.read(5) != b"%PDF-":
                    raise DomainError("PDF_REQUIRED", "实际原件不是PDF文件", 415)
                await run_in_threadpool(_upload_snapshot, upload_id, request, ticket)
                await run_in_threadpool(_register_temporary_key, key)
                stored = await run_in_threadpool(_store_file, file, key)
                await run_in_threadpool(_publish_upload, upload_id, request, ticket, key)
    except (TimeoutError, ClientDisconnect):
        raise DomainError(
            "UPLOAD_INTERRUPTED", "原件传输中断或超时，请刷新状态后重试", 408, retryable=True
        ) from None
    return Response(
        status_code=200,
        headers={"ETag": str(stored["ETag"]), "Cache-Control": "private,no-store"},
    )


def _open_download(version_id, request, ticket):
    session = auth(request)
    with connection() as c:
        row = fetch(
            c,
            "SELECT v.original_name,b.object_key,b.byte_size,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN bids bid ON bid.id=v.bid_id WHERE v.id=%s",
            (version_id,),
        )
    _verify(
        ticket,
        _download_claims(
            version_id, session, row["object_key"], row["original_name"], row["byte_size"]
        ),
    )
    maximum, _ = _limits()
    if row["byte_size"] > maximum:
        raise DomainError("DOWNLOAD_TOO_LARGE", "原件超出当前传输大小上限", 413)
    result = storage.client().get_object(Bucket=storage.bucket(), Key=row["object_key"])
    if result["ContentLength"] != row["byte_size"]:
        result["Body"].close()
        raise DomainError("OBJECT_SIZE_MISMATCH", "原件实际大小与资料记录不符", 409)
    return row, result["Body"]


def _download_chunks(body, length, deadline):
    remaining = length
    until = time.monotonic() + deadline
    try:
        while remaining:
            if time.monotonic() >= until:
                raise DomainError(
                    "DOWNLOAD_INTERRUPTED", "原件下载超时，请重新下载", 408, retryable=True
                )
            chunk = body.read(min(_CHUNK, remaining))
            if time.monotonic() >= until:
                raise DomainError(
                    "DOWNLOAD_INTERRUPTED", "原件下载超时，请重新下载", 408, retryable=True
                )
            if not chunk or len(chunk) > remaining:
                raise DomainError("OBJECT_SIZE_MISMATCH", "原件实际字节数不符", 409)
            remaining -= len(chunk)
            yield chunk
    finally:
        body.close()


class _DownloadResponse(StreamingResponse):
    """客户端中断、发送阻塞或生成器尚未迭代时也关闭原生对象连接。"""

    def __init__(self, *args, source_body, timeout, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_body = source_body
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        try:
            async with asyncio.timeout(self.timeout):
                await super().__call__(scope, receive, send)
        except TimeoutError:
            # 已发送响应头时只能中止流，不能伪造一次成功的完整下载。
            raise DomainError(
                "DOWNLOAD_INTERRUPTED", "原件下载超时，请重新下载", 408, retryable=True
            ) from None
        finally:
            self.source_body.close()


@router.get(
    _BASE + "/versions/{version_id}",
    operation_id="getPrivateVersionBytes",
    response_class=Response,
    openapi_extra={
        "security": [{"SessionCookie": []}],
        "responses": {
            "200": {
                "description": "鉴权后的全量原件；Range请求返回200全量",
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        },
    },
)
def download_bytes(version_id: UUID, request: Request, ticket: str = Query(..., max_length=160)):
    started = time.monotonic()
    row, body = _open_download(version_id, request, ticket)
    _, deadline = _limits()
    remaining = deadline - (time.monotonic() - started)
    if remaining <= 0:
        body.close()
        raise DomainError("DOWNLOAD_INTERRUPTED", "原件下载超时，请重新下载", 408, retryable=True)
    filename = quote(row["original_name"], safe="")
    return _DownloadResponse(
        _download_chunks(body, row["byte_size"], remaining),
        source_body=body,
        timeout=remaining,
        media_type=row["media_type"],
        headers={
            "Content-Length": str(row["byte_size"]),
            "Content-Disposition": f"attachment; filename=\"document.pdf\"; filename*=UTF-8''{filename}",
            "Accept-Ranges": "none",
            "Cache-Control": "private,no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
