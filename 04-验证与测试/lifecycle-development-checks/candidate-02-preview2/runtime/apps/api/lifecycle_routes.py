"""Explicit reversible business lifecycle; archive never cancels work implicitly."""

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import bid_etag, match_bid
from packages.domain.runtime import Command, DomainError, connection, etag, match, sha
from packages.lifecycle import service

router = APIRouter(prefix="/api/v1", tags=["project lifecycle"])


@router.get("/bids/{bid_id}/lifecycle")
def get_project_lifecycle(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return JSONResponse(service.view(c, bid_id), headers={"ETag": bid_etag(c, bid_id)})


@router.post("/bids/{bid_id}/lifecycle")
def change_project_lifecycle(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if (
        set(body) != {"action", "reason"}
        or not isinstance(body.get("action"), str)
        or body["action"]
        not in {
            "start",
            "end",
            "terminate",
            "archive",
            "reopen",
        }
    ):
        raise DomainError("INVALID_REQUEST", "请选择明确的项目生命周期动作及原因", 422)
    if (
        not isinstance(body["reason"], str)
        or not body["reason"].strip()
        or len(body["reason"]) > 4000
    ):
        raise DomainError("LIFECYCLE_REASON_REQUIRED", "请记录本次项目状态变更的原因", 422)
    with connection() as c:
        service.lock(c, bid_id)
        cmd = Command(
            c,
            actor["member_id"],
            "change_project_lifecycle",
            bid_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            return JSONResponse(
                cmd.replay["body"],
                status_code=cmd.replay["status"],
                headers=cmd.replay["headers"] | {"Idempotent-Replayed": "true"},
            )
        match_bid(c, r.headers.get("if-match"), bid_id)
        value = service.change(
            c, bid_id, actor["member_id"], body["action"], body["reason"], cmd.id
        )
        result = cmd.finish(value, headers={"ETag": bid_etag(c, bid_id)})
        return JSONResponse(result["body"], status_code=result["status"], headers=result["headers"])


@router.get("/bids/{bid_id}/upload-batches/{batch_id}/closure-state")
def get_batch_closure_state(bid_id: UUID, batch_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        value = service.batch_closure_view(c, bid_id, batch_id)
        return JSONResponse(value, headers={"ETag": etag("batch-close", batch_id, sha(value))})


@router.post("/bids/{bid_id}/upload-batches/{batch_id}/close")
def close_batch_keep_staging(bid_id: UUID, batch_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if (
        set(body) != {"reason"}
        or not isinstance(body.get("reason"), str)
        or not body["reason"].strip()
        or len(body["reason"]) > 4000
    ):
        raise DomainError("INVALID_REQUEST", "请记录保留暂存资料结束批次的原因", 422)
    with connection() as c:
        service.lock(c, bid_id)
        original = service.batch_closure_view(c, bid_id, batch_id)
        cmd = Command(
            c,
            actor["member_id"],
            "close_batch_keep_staging",
            batch_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            return JSONResponse(
                cmd.replay["body"],
                status_code=cmd.replay["status"],
                headers=cmd.replay["headers"] | {"Idempotent-Replayed": "true"},
            )
        match(r.headers.get("if-match"), "batch-close", batch_id, sha(original))
        service.close_batch(c, bid_id, batch_id, actor["member_id"], body["reason"], cmd.id)
        value = service.batch_closure_view(c, bid_id, batch_id)
        stored = cmd.finish(value, headers={"ETag": etag("batch-close", batch_id, sha(value))})
        return JSONResponse(stored["body"], status_code=stored["status"], headers=stored["headers"])
