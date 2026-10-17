"""Versioned task configuration only; saving a template never starts its work."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import match_bid
from packages.domain.runtime import (
    Command,
    clean,
    connection,
    decode_cursor,
    encode_cursor,
    etag,
    match,
)
from packages.lifecycle.service import assert_work_writable, lock
from packages.tasks import runs as task_runs
from packages.tasks import templates
from packages.tasks.models import (
    PauseRequest,
    RunPage,
    RunRequest,
    RunView,
    TaskKindPage,
    TaskNotificationPage,
    TaskScheduleRecoveryPage,
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


@router.post(
    "/bids/{bid_id}/task-definitions/{definition_id}/runs", status_code=202, response_model=RunView
)
def run_template(bid_id: UUID, definition_id: UUID, r: Request, body: RunRequest):
    actor = auth(r, True)
    with connection() as c:
        lock(c, bid_id)
        row = templates.current(c, bid_id, definition_id)
        cmd = command(
            c, bid_id, actor, r, "task-run-manual", definition_id, body.model_dump(mode="json")
        )
        if cmd.replay:
            original = cmd.replay["body"]
            value = task_runs.view(c, bid_id, UUID(original["id"]))
            return response({**cmd.replay, "body": value}, True)
        match(r.headers.get("if-match"), "task-template", definition_id, row["revision"])
        value, reused = task_runs.dispatch(
            c,
            bid=bid_id,
            definition=definition_id,
            actor=actor["member_id"],
            command=cmd.id,
            rerun=body.rerun,
        )
        return response(cmd.finish(value, 202, {"Task-Run-Reused": "true" if reused else "false"}))


@router.get("/bids/{bid_id}/task-runs/{run_id}", response_model=RunView)
def task_run(bid_id: UUID, run_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return task_runs.view(c, bid_id, run_id)


@router.get("/bids/{bid_id}/task-runs", response_model=RunPage)
def task_run_page(
    bid_id: UUID, r: Request, after: UUID | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = c.execute(
            "SELECT id FROM task_runs WHERE bid_id=%s AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s",
            (bid_id, after, after, limit + 1),
        ).fetchall()
        return {
            "items": [task_runs.view(c, bid_id, row["id"]) for row in rows[:limit]],
            "next_after": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@router.get("/bids/{bid_id}/task-notifications", response_model=TaskNotificationPage)
def task_notifications(
    bid_id: UUID, r: Request, after: UUID | None = None, limit: int = Query(20, ge=1, le=100)
):
    actor = auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = c.execute(
            "SELECT id,payload,created_at FROM audit_entries WHERE bid_id=%s AND actor_id=%s AND event_type='task.notification' AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s",
            (bid_id, actor["member_id"], after, after, limit + 1),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "next_after": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@router.get("/bids/{bid_id}/task-schedule-recoveries", response_model=TaskScheduleRecoveryPage)
def task_recoveries(
    bid_id: UUID, r: Request, after: UUID | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = c.execute(
            "SELECT id,payload,created_at FROM audit_entries WHERE bid_id=%s AND event_type='task.schedule_skipped' AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s",
            (bid_id, after, after, limit + 1),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "next_after": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            "run_current_once_supported": True,
        }


@router.get("/task-kinds", response_model=TaskKindPage, response_model_exclude_unset=True)
def task_kinds(r: Request):
    auth(r)
    from packages.domain import reviews
    from packages.tasks.inputs import review_guard

    review_ready = getattr(reviews, "TASK_INPUT_GUARD", None) is review_guard
    return {
        "items": [
            {
                "task_kind": "denial_check",
                "name": "否决核对",
                "purpose": "compliance",
                "execution": "existing_review_control_plane" if review_ready else "unavailable",
                "reason": None if review_ready else "TASK_REVIEW_EXECUTOR_NOT_READY",
                "formal_scoring_eligible": False,
            },
            {
                "task_kind": "scoring_review",
                "name": "评分项评审",
                "purpose": "compliance",
                "execution": "unavailable",
                "reason": "TASK_SCORING_NOT_QUALIFIED",
                "formal_scoring_eligible": False,
            },
            {
                "task_kind": "supplement_impact",
                "name": "补遗影响检查",
                "purpose": "assist",
                "execution": "partial",
                "coverage": "registered_effectivity_only",
                "semantic_impact_coverage": "unknown",
                "formal_decisions_allowed": False,
            },
            {
                "task_kind": "chapter_suggestion",
                "name": "章节修改建议",
                "purpose": "assist",
                "execution": "existing_authoring_proposal_queue",
                "automatic_apply": False,
            },
        ],
        "default_trigger": "manual",
        "saving_preset_creates_run": False,
    }
