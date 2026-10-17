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
            "items": [conversations.message_view(row) for row in reversed(rows[:limit])],
            "has_more": len(rows) > limit,
            "before": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }
