"""同源分片字节端点；现有JSON路由由main按mode调用service hooks。"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from apps.api.auth import auth
from packages.domain.runtime import DomainError
from packages.uploads import service
from packages.uploads.native import NativeMultipart
from packages.uploads.protocol import part_size, unpack

router = APIRouter(tags=["Private multipart transfers"])
# Per-process transport limit. Browser scheduler must also cap its pool at four.
_pool = asyncio.Semaphore(4)


@router.put(
    service.BASE + "/{upload_id}/parts/{part_number}",
    operation_id="putPrivateMultipartPart",
    response_class=Response,
    openapi_extra={
        "security": [{"SessionCookie": []}],
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        "responses": {
            "200": {"description": "分片已被原生OSS接收；完整SHA核验前不是正式原件"},
            "default": {"$ref": "#/components/responses/DomainError"},
        },
    },
)
async def put_part(
    upload_id: UUID, part_number: int, request: Request, ticket: str = Query(..., max_length=160)
):
    async with _pool:
        session = await run_in_threadpool(auth, request, True)
        row = await run_in_threadpool(service.snapshot, upload_id, session, part_number, ticket)
        expected = part_size(row, part_number)
        if request.headers.get("content-type", "").lower() != "application/octet-stream":
            raise DomainError("PART_MEDIA_TYPE_INVALID", "分片需要application/octet-stream", 415)
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise DomainError("CONTENT_ENCODING_UNSUPPORTED", "分片不支持压缩编码", 415)
        try:
            length = int(request.headers["content-length"])
        except (ValueError, KeyError):
            raise DomainError("CONTENT_LENGTH_REQUIRED", "请提供准确的分片字节数", 411) from None
        if length != expected:
            raise DomainError("UPLOAD_SIZE_MISMATCH", "分片大小与授权不符", 422)
        data = bytearray()
        try:
            async with asyncio.timeout(90):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > expected:
                        raise DomainError("UPLOAD_SIZE_MISMATCH", "分片实际大小超出授权", 422)
                    data.extend(chunk)
                if len(data) != expected:
                    raise DomainError("UPLOAD_SIZE_MISMATCH", "分片未传输完整", 422)
                if part_number == 1 and data[:5] != b"%PDF-":
                    raise DomainError("PDF_REQUIRED", "实际原件不是PDF", 415)
                await run_in_threadpool(service.snapshot, upload_id, session, part_number, ticket)
                state = unpack(row["provider_upload_id"], row["staging_key"])
                remote_etag = await run_in_threadpool(
                    NativeMultipart().upload,
                    row["staging_key"],
                    state["provider_id"],
                    part_number,
                    bytes(data),
                )
                await run_in_threadpool(
                    service.publish_part, upload_id, session, part_number, ticket, row, remote_etag
                )
        except (TimeoutError, ClientDisconnect):
            raise DomainError(
                "UPLOAD_INTERRUPTED", "分片传输中断，请查询已上传分片后重试", 408, retryable=True
            ) from None
        return Response(
            status_code=200, headers={"ETag": remote_etag, "Cache-Control": "private,no-store"}
        )
