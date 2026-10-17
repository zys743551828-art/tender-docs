"""External Word receipt discovery and explicit bounded local conversion retry."""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, accepted, connection, fetch
from packages.lifecycle.service import assert_work_writable
from packages.word_intake.renderer import configured_profile
from packages.word_intake.repository import queue_render, word_etag, word_view

router = APIRouter(prefix="/api/v1", tags=["word-intake"])


def read_profile():
    try:
        return configured_profile()
    except DomainError:
        return None


@router.get("/bids/{bid_id}/word-originals")
def list_originals(
    bid_id: UUID,
    r: Request,
    limit: int = Query(default=50, ge=1, le=100),
    after: UUID | None = None,
):
    auth(r)
    profile = read_profile()
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        rows = c.execute(
            "SELECT document_version_id FROM word_intake_receipts WHERE bid_id=%s AND (%s::uuid IS NULL OR document_version_id<%s) ORDER BY document_version_id DESC LIMIT %s",
            (bid_id, after, after, limit + 1),
        ).fetchall()
        return {
            "items": [
                word_view(c, bid_id, row["document_version_id"], current_profile=profile)
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "next_after": str(rows[limit - 1]["document_version_id"])
            if len(rows) > limit
            else None,
        }


@router.get("/bids/{bid_id}/word-originals/{version_id}")
def get_original(bid_id: UUID, version_id: UUID, r: Request):
    auth(r)
    profile = read_profile()
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        body = word_view(c, bid_id, version_id, current_profile=profile)
        return JSONResponse(body, headers={"ETag": word_etag(c, bid_id, version_id, body=body)})


@router.post("/bids/{bid_id}/word-originals/{version_id}/retry-conversion")
def retry_conversion(bid_id: UUID, version_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if body != {}:
        raise DomainError("INVALID_REQUEST", "重试只接受明确原件身份，不接受自定义转换参数", 422)
    profile_error = None
    try:
        profile = configured_profile()  # Trusted local read before taking the bid lock.
    except DomainError as error:
        profile, profile_error = None, error
    with connection() as c:
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        old = word_view(c, bid_id, version_id, current_profile=profile)
        cmd = Command(
            c,
            actor["member_id"],
            "word_retry_conversion",
            version_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            response = cmd.replay
            return JSONResponse(
                response["body"],
                status_code=response["status"],
                headers=response["headers"] | {"Idempotent-Replayed": "true"},
            )
        assert_work_writable(c, bid_id)
        if profile_error is not None:
            raise profile_error
        if profile is None or profile["identity"].get("enabled") is False:
            raise DomainError(
                "WORD_RENDER_RETRY_UNAVAILABLE", "当前转换能力未配置，请上传此修改稿的PDF", 409
            )
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请刷新Word转换状态后重试", 428)
        if r.headers["if-match"] != word_etag(c, bid_id, version_id, body=old):
            raise DomainError(
                "WORD_CONVERSION_STATE_CHANGED", "Word转换状态已变化，请刷新后重试", 412
            )
        if not old["conversion"]["retryable"] or old["conversion"]["state"] not in {
            "failed",
            "cancelled",
        }:
            raise DomainError(
                "WORD_RENDER_RETRY_UNAVAILABLE", "此状态不可重试，请查看缺口或补充PDF", 409
            )
        if old["inspection"]["active_content_gaps"]:
            raise DomainError(
                "WORD_PACKAGE_ACTIVE_CONTENT_BLOCKED",
                "Word含外部或活动对象，请提供PDF，不执行转换重试",
                409,
            )
        operation = queue_render(c, bid_id, version_id, cmd.id, profile)
        response = cmd.finish(accepted(operation, version_id, "ingestion"), 202)
        return JSONResponse(
            response["body"], status_code=response["status"], headers=response["headers"]
        )
