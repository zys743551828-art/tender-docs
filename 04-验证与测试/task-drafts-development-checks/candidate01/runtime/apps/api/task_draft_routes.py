"""Task drafts are private form proposals; template saving remains a separate explicit action."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import match_bid
from packages.domain.runtime import clean, connection, etag, match
from packages.domain.work_access import begin
from packages.lifecycle.service import lock
from packages.tasks import drafts
from packages.tasks.draft_interpreter import DraftRequest
from packages.tasks.models import TemplateRequest

router = APIRouter(prefix="/api/v1", tags=["task drafts"])


def response(stored, replay=False):
    return JSONResponse(
        stored["body"],
        status_code=stored["status"],
        headers=stored["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


@router.post(
    "/bids/{bid_id}/task-definition-drafts", response_model=drafts.DraftView, status_code=201
)
def create_draft(bid_id: UUID, r: Request, body: DraftRequest):
    actor = auth(r, True)["member_id"]
    with connection() as c:
        cmd = begin(
            c,
            actor,
            "task-draft-create",
            bid_id,
            r.headers.get("idempotency-key"),
            body.model_dump(mode="json"),
            r.headers.get("if-match"),
            bid=bid_id,
        )
        if cmd.replay:
            return response(cmd.replay, True)
        match_bid(c, r.headers.get("if-match"), bid_id)
        value = drafts.create(c, bid=bid_id, actor=actor, request=body, command=cmd.id)
        return response(
            cmd.finish(
                value.model_dump(mode="json"),
                201,
                {"ETag": etag("task-draft", value.id, value.revision)},
            )
        )


@router.get("/bids/{bid_id}/task-definition-drafts/{draft_id}", response_model=drafts.DraftView)
def get_draft(bid_id: UUID, draft_id: UUID, r: Request):
    actor = auth(r)["member_id"]
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = drafts.current(c, bid_id, actor, draft_id)
        return JSONResponse(
            clean(value.model_dump(mode="json")),
            headers={"ETag": etag("task-draft", value.id, value.revision)},
        )


@router.put("/bids/{bid_id}/task-definition-drafts/{draft_id}", response_model=drafts.DraftView)
def revise_draft(bid_id: UUID, draft_id: UUID, r: Request, body: TemplateRequest):
    actor = auth(r, True)["member_id"]
    with connection() as c:
        lock(c, bid_id)
        previous = drafts.current(c, bid_id, actor, draft_id)
        cmd = begin(
            c,
            actor,
            "task-draft-revise",
            draft_id,
            r.headers.get("idempotency-key"),
            body.model_dump(mode="json"),
            r.headers.get("if-match"),
            bid=bid_id,
        )
        if cmd.replay:
            return response(cmd.replay, True)
        match(r.headers.get("if-match"), "task-draft", draft_id, previous.revision)
        value = drafts.revise(c, previous=previous, configuration=body, command=cmd.id)
        return response(
            cmd.finish(
                value.model_dump(mode="json"),
                200,
                {"ETag": etag("task-draft", value.id, value.revision)},
            )
        )
