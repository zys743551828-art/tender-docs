"""Explicit reversible business lifecycle; archive never cancels work implicitly."""

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import bid_etag, match_bid
from packages.domain.runtime import Command, DomainError, connection
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
