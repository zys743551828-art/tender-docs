"""Versioned task configuration only; saving a template never starts its work."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import match_bid
from packages.domain.runtime import (
    Command,
    connection,
    decode_cursor,
    encode_cursor,
    etag,
    match,
)
from packages.lifecycle.service import assert_work_writable, lock
from packages.tasks import templates
from packages.tasks.models import (
    PauseRequest,
    TemplatePage,
    TemplateRequest,
    TemplateVersionView,
    TemplateView,
)

router = APIRouter(prefix="/api/v1", tags=["task templates"])


def response(stored, replay=False):
    return JSONResponse(
        stored["body"],
        status_code=stored["status"],
        headers=stored["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def command(c, bid, actor, request, kind, target, body):
    # Same order as archive and every task producer: bid, command, domain object.
    lock(c, bid)
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match"),
    )


@router.get("/bids/{bid_id}/task-definitions", response_model=TemplatePage)
def list_templates(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"task-templates:{bid_id}:1"
    after = decode_cursor(cursor, scope=scope)[1] if cursor else None
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        value = templates.page(c, bid_id, limit, after)
        last = value.pop("last_id")
        # This directory orders UUIDs; the shared envelope's timestamp is a reserved constant.
        value["next_cursor"] = (
            encode_cursor({"created_at": datetime(1970, 1, 1, tzinfo=UTC), "id": last}, scope=scope)
            if last
            else None
        )
        return value


@router.post("/bids/{bid_id}/task-definitions", response_model=TemplateView, status_code=201)
def create_template(bid_id: UUID, r: Request, body: TemplateRequest):
    actor = auth(r, True)
    with connection() as c:
        cmd = command(
            c, bid_id, actor, r, "task-template-create", bid_id, body.model_dump(mode="json")
        )
        if cmd.replay:
            return response(cmd.replay, True)
        assert_work_writable(c, bid_id)
        match_bid(c, r.headers.get("if-match"), bid_id)
        value = templates.create(c, bid_id, actor["member_id"], body, cmd.id)
        return response(
            cmd.finish(value, 201, {"ETag": etag("task-template", value["id"], value["revision"])})
        )


@router.get("/bids/{bid_id}/task-definitions/{definition_id}", response_model=TemplateView)
def get_template(bid_id: UUID, definition_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        value = templates.view(c, bid_id, definition_id)
        return JSONResponse(
            value, headers={"ETag": etag("task-template", definition_id, value["revision"])}
        )


def mutate(bid, definition, request, body, kind):
    actor = auth(request, True)
    with connection() as c:
        # Validate URL scope before a potentially cross-project idempotent receipt.
        lock(c, bid)
        row = templates.current(c, bid, definition)
        cmd = command(c, bid, actor, request, kind, definition, body.model_dump(mode="json"))
        if cmd.replay:
            return response(cmd.replay, True)
        assert_work_writable(c, bid)
        match(request.headers.get("if-match"), "task-template", definition, row["revision"])
        if isinstance(body, PauseRequest):
            value = templates.pause(c, bid, definition, actor["member_id"], body.paused, cmd.id)
        else:
            value = templates.revise(c, bid, definition, actor["member_id"], body, cmd.id)
        return response(
            cmd.finish(
                value, headers={"ETag": etag("task-template", definition, value["revision"])}
            )
        )


@router.put("/bids/{bid_id}/task-definitions/{definition_id}", response_model=TemplateView)
def revise_template(bid_id: UUID, definition_id: UUID, r: Request, body: TemplateRequest):
    return mutate(bid_id, definition_id, r, body, "task-template-revise")


@router.post("/bids/{bid_id}/task-definitions/{definition_id}/pause", response_model=TemplateView)
def pause_template(bid_id: UUID, definition_id: UUID, r: Request, body: PauseRequest):
    return mutate(bid_id, definition_id, r, body, "task-template-pause")


@router.get(
    "/bids/{bid_id}/task-definitions/{definition_id}/versions/{version_id}",
    response_model=TemplateVersionView,
)
def get_template_version(bid_id: UUID, definition_id: UUID, version_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        value = templates.version(c, bid_id, definition_id, version_id)
        return JSONResponse(
            value, headers={"ETag": etag("task-template-version", version_id, value["digest"])}
        )
