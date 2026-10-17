"""Persistent work-conversation routes. Recording a message never implies AI completion."""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.assistant import conversations
from packages.assistant.contracts import validate
from packages.domain.runtime import Command, DomainError, connection, etag, fetch, match

router = APIRouter(prefix="/api/v1", tags=["work-conversations"])


def saved(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def command(c, r, actor, kind, target, body, conditional=False):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match") if conditional else None,
    )


@router.post("/bids/{bid_id}/conversations/main")
def ensure_main_conversation(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if body != {}:
        raise DomainError("INVALID_REQUEST", "主会话请求不接受额外上下文", 422)
    with connection() as c:
        conversations.lock_project(c, bid_id)
        cmd = command(c, r, actor, "ensure_main_conversation", bid_id, {})
        if cmd.replay:
            return saved(cmd.replay, True)
        value = conversations.ensure_main(c, bid_id, actor["member_id"], cmd.id)
        return saved(
            cmd.finish(value, 200, {"ETag": etag("conversation", value["id"], value["revision"])})
        )


@router.get("/bids/{bid_id}/conversations/main")
def get_main_conversation(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        value = conversations.view(conversations.get(c, bid_id, conversations.main_id(bid_id)))
        return JSONResponse(
            value, headers={"ETag": etag("conversation", value["id"], value["revision"])}
        )


@router.get("/bids/{bid_id}/conversations/{conversation_id}")
def get_work_conversation(bid_id: UUID, conversation_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        value = conversations.view(conversations.get(c, bid_id, conversation_id))
        return JSONResponse(
            value, headers={"ETag": etag("conversation", value["id"], value["revision"])}
        )


@router.post("/bids/{bid_id}/conversations/{conversation_id}/state")
def change_work_conversation(
    bid_id: UUID, conversation_id: UUID, r: Request, body: dict = Body(...)
):
    actor = auth(r, True)
    validate("WorkConversationStateRequest", body)
    with connection() as c:
        conversations.lock_project(c, bid_id)
        conversations.get(c, bid_id, conversation_id)
        cmd = command(c, r, actor, "change_work_conversation", conversation_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        current = conversations.get(c, bid_id, conversation_id)
        match(r.headers.get("if-match"), "conversation", conversation_id, current["revision"])
        value = conversations.change_state(
            c, bid_id, conversation_id, actor["member_id"], body["state"], cmd.id
        )
        return saved(
            cmd.finish(
                value, headers={"ETag": etag("conversation", conversation_id, value["revision"])}
            )
        )


@router.post("/bids/{bid_id}/conversations/{conversation_id}/messages")
def record_work_message(bid_id: UUID, conversation_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("WorkMessageRequest", body)
    with connection() as c:
        conversations.lock_project(c, bid_id)
        conversations.get(c, bid_id, conversation_id)
        cmd = command(c, r, actor, "record_work_message", conversation_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        current = conversations.get(c, bid_id, conversation_id)
        match(r.headers.get("if-match"), "conversation", conversation_id, current["revision"])
        value = conversations.record_message(
            c, bid_id, conversation_id, actor["member_id"], body, cmd.id
        )
        revision = conversations.get(c, bid_id, conversation_id)["revision"]
        return saved(
            cmd.finish(value, 201, {"ETag": etag("conversation", conversation_id, revision)})
        )


@router.get("/bids/{bid_id}/conversations/{conversation_id}/messages")
def list_work_messages(
    bid_id: UUID,
    conversation_id: UUID,
    r: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before: UUID | None = None,
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conversations.get(c, bid_id, conversation_id)
        key = (
            fetch(
                c,
                "SELECT created_at,id FROM messages WHERE bid_id=%s AND conversation_id=%s AND id=%s",
                (bid_id, conversation_id, before),
            )
            if before
            else None
        )
        rows = c.execute(
            "SELECT * FROM messages WHERE bid_id=%s AND conversation_id=%s AND (%s::uuid IS NULL OR (created_at,id)<(%s,%s)) ORDER BY created_at DESC,id DESC LIMIT %s",
            (
                bid_id,
                conversation_id,
                before,
                key["created_at"] if key else None,
                before,
                limit + 1,
            ),
        ).fetchall()
        return {
            "items": [conversations.message_view(row, c) for row in reversed(rows[:limit])],
            "has_more": len(rows) > limit,
            "before": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@router.get("/assistant/global-conversations/main")
def get_global_main(r: Request):
    from packages.assistant import jobs

    actor = auth(r)
    with connection() as c:
        jobs.require_schema(c)
        value = jobs.global_view(
            jobs.global_get(c, jobs.global_id(actor["member_id"]), actor["member_id"])
        )
        return JSONResponse(
            value, headers={"ETag": etag("conversation", value["id"], value["revision"])}
        )


@router.post("/assistant/global-conversations/main")
def ensure_global_main(r: Request, body: dict = Body(...)):
    from packages.assistant import jobs

    actor = auth(r, True)
    if body != {}:
        raise DomainError("INVALID_REQUEST", "全局主会话不接受项目或模型上下文", 422)
    with connection() as c:
        jobs.require_schema(c)
        cmd = command(
            c, r, actor, "assistant-global-main", jobs.global_id(actor["member_id"]), body
        )
        if cmd.replay:
            return saved(cmd.replay, True)
        value = jobs.ensure_global(c, actor["member_id"], cmd.id)
        return saved(
            cmd.finish(value, 200, {"ETag": etag("conversation", value["id"], value["revision"])})
        )


@router.get("/assistant/conversations")
def discover_conversations(r: Request, scope: str = "project", bid_id: UUID | None = None):
    from packages.assistant import jobs

    actor = auth(r)
    with connection() as c:
        if scope == "global":
            jobs.require_schema(c)
            rows = c.execute(
                "SELECT * FROM assistant_global_conversations WHERE created_by=%s",
                (actor["member_id"],),
            ).fetchall()
            return {"items": [jobs.global_view(row) for row in rows]}
        if scope != "project" or bid_id is None:
            raise DomainError("ASSISTANT_TARGET_REQUIRED", "请明确会话范围及项目", 422)
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        ids = c.execute(
            "SELECT id FROM conversations WHERE bid_id=%s ORDER BY created_at,id", (bid_id,)
        ).fetchall()
        return {
            "items": [conversations.view(conversations.get(c, bid_id, row["id"])) for row in ids]
        }


@router.post("/assistant/global-conversations/{conversation_id}/state")
def change_global_state(conversation_id: UUID, r: Request, body: dict = Body(...)):
    from packages.assistant import jobs

    actor = auth(r, True)
    validate("WorkConversationStateRequest", body)
    with connection() as c:
        jobs.require_schema(c)
        row = jobs.global_get(c, conversation_id, actor["member_id"], lock=True)
        cmd = command(c, r, actor, "assistant-global-state", conversation_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        match(r.headers.get("if-match"), "conversation", conversation_id, row["revision"])
        value = jobs.global_state(c, conversation_id, actor["member_id"], body["state"], cmd.id)
        return saved(
            cmd.finish(
                value, 200, {"ETag": etag("conversation", conversation_id, value["revision"])}
            )
        )


def assistant_turn(r, body, conversation_id, bid_id=None):
    from packages.assistant import jobs
    from packages.assistant.protocol import normalized_turn

    actor = auth(r, True)
    body = normalized_turn(body)
    with connection() as c:
        jobs.require_schema(c)
        cmd = command(c, r, actor, "assistant-turn", conversation_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        value = jobs.queue_answer(
            c,
            actor["member_id"],
            conversation_id,
            body,
            cmd.id,
            bid=bid_id,
            condition=r.headers.get("if-match"),
        )
        return saved(cmd.finish(value, 202))


@router.post("/bids/{bid_id}/conversations/{conversation_id}/turns")
def project_assistant_turn(bid_id: UUID, conversation_id: UUID, r: Request, body: dict = Body(...)):
    return assistant_turn(r, body, conversation_id, bid_id)


@router.post("/assistant/global-conversations/{conversation_id}/turns")
def global_assistant_turn(conversation_id: UUID, r: Request, body: dict = Body(...)):
    return assistant_turn(r, body, conversation_id)


@router.get("/assistant/global-conversations/{conversation_id}/messages")
def global_messages(
    conversation_id: UUID,
    r: Request,
    limit: int = Query(50, ge=1, le=100),
    before: UUID | None = None,
):
    from packages.assistant import jobs

    actor = auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        jobs.require_schema(c)
        jobs.global_get(c, conversation_id, actor["member_id"])
        cursor = (
            fetch(
                c,
                "SELECT created_at FROM assistant_global_messages WHERE id=%s AND conversation_id=%s",
                (before, conversation_id),
            )
            if before
            else None
        )
        rows = c.execute(
            "SELECT * FROM assistant_global_messages WHERE conversation_id=%s AND (%s::uuid IS NULL OR (created_at,id)<(%s,%s)) ORDER BY created_at DESC,id DESC LIMIT %s",
            (conversation_id, before, cursor["created_at"] if cursor else None, before, limit + 1),
        ).fetchall()
        return {
            "items": [
                conversations.message_view(row, c) | {"bid_id": None, "scope": "global"}
                for row in reversed(rows[:limit])
            ],
            "has_more": len(rows) > limit,
            "before": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@router.get("/assistant/global-conversations/{conversation_id}/events")
def global_events(
    conversation_id: UUID,
    r: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    from packages.assistant import jobs
    from packages.domain.runtime import clean

    actor = auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        jobs.require_schema(c)
        row = jobs.global_get(c, conversation_id, actor["member_id"])
        events = c.execute(
            "SELECT seq,event_id,event_type,payload,created_at FROM assistant_global_events WHERE conversation_id=%s AND seq>%s ORDER BY seq LIMIT %s",
            (conversation_id, after, limit),
        ).fetchall()
        return clean(
            {
                "stream_id": conversation_id,
                "items": events,
                "next_cursor": events[-1]["seq"] if events else after,
                "head_cursor": row["last_seq"],
            }
        )


@router.get("/assistant/sources")
def assistant_sources(
    r: Request,
    kind: str,
    bid_id: UUID | None = None,
    q: str | None = None,
    trace_id: UUID | None = None,
    physical_page: int | None = Query(None, ge=1),
):
    from packages.assistant import context

    auth(r)
    with connection() as c:
        return context.choices(
            c, kind, bid=bid_id, query=q, trace_id=trace_id, physical_page=physical_page
        )


@router.get("/assistant/jobs/{job_id}")
def assistant_job(job_id: UUID, r: Request):
    from packages.assistant import jobs

    actor = auth(r)
    with connection() as c:
        jobs.require_schema(c)
        return jobs.view(c, job_id, actor["member_id"])


@router.get("/assistant/jobs")
def assistant_jobs(
    r: Request,
    conversation_id: UUID,
    scope: str,
    bid_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    from packages.assistant import jobs

    actor = auth(r)
    with connection() as c:
        jobs.require_schema(c)
        if scope == "global":
            jobs.global_get(c, conversation_id, actor["member_id"])
        elif scope == "project" and bid_id:
            conversations.get(c, bid_id, conversation_id)
        else:
            raise DomainError("ASSISTANT_TARGET_REQUIRED", "请明确真实会话范围", 422)
        ids = c.execute(
            "SELECT id FROM assistant_jobs WHERE (%s='global' AND global_conversation_id=%s) OR (%s='project' AND bid_id=%s AND project_conversation_id=%s) ORDER BY created_at DESC,id DESC LIMIT %s",
            (scope, conversation_id, scope, bid_id, conversation_id, limit),
        ).fetchall()
        return {"items": [jobs.view(c, row["id"], actor["member_id"]) for row in ids]}


@router.post("/assistant/jobs/{job_id}/cancel")
def cancel_assistant_job(job_id: UUID, r: Request, body: dict = Body(...)):
    from packages.assistant import jobs

    actor = auth(r, True)
    if body != {}:
        raise DomainError("INVALID_REQUEST", "取消请求不接受额外输入", 422)
    with connection() as c:
        jobs.require_schema(c)
        row = jobs.job(c, job_id)
        jobs.authorize_job(c, row, actor["member_id"])
        cmd = command(c, r, actor, "assistant-cancel", job_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        if row["scope"] == "global":
            c.execute(
                "UPDATE assistant_global_runs SET cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()) WHERE id=%s AND state IN ('queued','running','cancel_requested')",
                (job_id,),
            )
        else:
            fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (row["bid_id"],))
            c.execute(
                "UPDATE operations SET state='cancel_requested',cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()) WHERE id=%s AND state IN ('queued','running','cancel_requested')",
                (row["operation_id"],),
            )
        return saved(cmd.finish(jobs.view(c, job_id, actor["member_id"]), 202))


@router.get("/assistant/actions/{action_id}")
def assistant_action(action_id: UUID, r: Request):
    from packages.assistant import actions

    auth(r)
    with connection() as c:
        value = actions.view(c, action_id)
        return JSONResponse(value, headers={"ETag": actions.tag(value)})


@router.post("/assistant/actions/{action_id}/resolve")
def resolve_assistant_action(action_id: UUID, r: Request, body: dict = Body(...)):
    from packages.assistant import actions
    from packages.assistant.protocol import validate as validate_assistant

    actor = auth(r, True)
    validate_assistant("AssistantActionResolveRequest", body)
    with connection() as c:
        actions.lock(c, action_id)
        cmd = command(c, r, actor, "assistant-action-resolve", action_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        value, status = actions.resolve(
            c, action_id, actor["member_id"], body["decision"], cmd.id, r.headers.get("if-match")
        )
        return saved(cmd.finish(value, status, {"ETag": actions.tag(value)}))


@router.post("/bids/{bid_id}/authoring/generations")
def generate_authoring(bid_id: UUID, r: Request, body: dict = Body(...)):
    from packages.assistant import jobs
    from packages.assistant.protocol import validate as validate_assistant

    actor = auth(r, True)
    validate_assistant("AssistantGenerationRequest", body)
    with connection() as c:
        section = fetch(
            c,
            "SELECT draft_id FROM sections WHERE id=%s AND bid_id=%s",
            (UUID(body["section_id"]), bid_id),
        )
        draft = fetch(c, "SELECT revision FROM drafts WHERE id=%s", (section["draft_id"],))
        cmd = command(c, r, actor, "assistant-authoring-generate", bid_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        match(r.headers.get("if-match"), "draft", section["draft_id"], draft["revision"])
        value = jobs.queue_generation(c, bid_id, actor["member_id"], body, cmd.id)
        return saved(cmd.finish(value, 202))
