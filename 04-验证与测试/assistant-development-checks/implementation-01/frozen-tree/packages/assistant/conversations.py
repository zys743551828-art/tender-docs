"""Persistent project work conversations, separate from authentication sessions."""

from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, fetch, now, sha, uid

NAMESPACE = UUID("09b0ee62-bbbe-44fa-b73a-2875b775bb73")
TRANSITIONS = {
    "active": {"closed", "hidden"},
    "closed": {"active", "hidden"},
    "hidden": {"closed"},
}


def lock_project(c, bid):
    return fetch(c, "SELECT * FROM bids WHERE id=%s FOR UPDATE", (bid,))


def get(c, bid, conversation):
    return fetch(
        c,
        "SELECT v.*,s.id AS stream_id FROM conversations v LEFT JOIN stream_heads s ON s.bid_id=v.bid_id WHERE v.bid_id=%s AND v.id=%s",
        (bid, conversation),
    )


def main_id(bid):
    return uuid5(NAMESPACE, "project-main:" + str(UUID(str(bid))))


def view(row):
    return clean(row) | {
        "scope": "project",
        "is_main": row["id"] == main_id(row["bid_id"]),
        "title": "项目主会话",
    }


def ensure_main(c, bid, actor, command_id):
    lock_project(c, bid)
    conversation = main_id(bid)
    inserted = c.execute(
        "INSERT INTO conversations(id,bid_id,state,revision) VALUES(%s,%s,'active',1) ON CONFLICT(id) DO NOTHING",
        (conversation, bid),
    ).rowcount
    if inserted:
        audit(c, actor, bid, "conversation.created", {"conversation_id": conversation}, command_id)
    # Merely opening the panel must not undo an explicit close/archive.
    return view(get(c, bid, conversation))


def change_state(c, bid, conversation, actor, target, command_id):
    lock_project(c, bid)
    current = get(c, bid, conversation)
    if target == current["state"]:
        return view(current)
    if target not in TRANSITIONS[current["state"]]:
        raise DomainError("CONVERSATION_TRANSITION_INVALID", "请先恢复已归档会话，再继续讨论", 409)
    changed = c.execute(
        "UPDATE conversations SET state=%s,revision=revision+1 WHERE id=%s RETURNING *",
        (target, conversation),
    ).fetchone()
    audit(
        c,
        actor,
        bid,
        "conversation.state_changed",
        {"conversation_id": conversation, "before": current["state"], "after": target},
        command_id,
    )
    # No operation or authentication-session mutation belongs to this transition.
    return view(get(c, bid, changed["id"]))


def record_message(c, bid, conversation, actor, body, command_id):
    """Persist human text and selected source identities; no implicit tool execution."""
    lock_project(c, bid)
    current = get(c, bid, conversation)
    if current["state"] != "active":
        raise DomainError("CONVERSATION_CLOSED", "请先继续这条会话再发送消息", 409)
    message_id = UUID(body["client_message_id"])
    sources = [UUID(value) for value in body["source_version_ids"]]
    if len(sources) != len(set(sources)):
        raise DomainError("DUPLICATE_SOURCE", "请勿重复选择同一来源版本", 422)
    parts = [{"type": "text", "text": body["text"]}]
    for source in sources:
        row = fetch(c, "SELECT id,bid_id FROM document_versions WHERE id=%s", (source,))
        parts.append(
            {
                "type": "source_reference",
                "document_version_id": str(row["id"]),
                "source_bid_id": str(row["bid_id"]),
            }
        )
    existing = c.execute(
        "SELECT * FROM messages WHERE conversation_id=%s AND client_message_id=%s",
        (conversation, message_id),
    ).fetchone()
    if existing:
        if existing["created_by"] != actor or sha(existing["parts"]) != sha(parts):
            raise DomainError("MESSAGE_ID_CONFLICT", "这条消息标识已用于其他内容或成员", 409)
        return message_view(existing)
    row = c.execute(
        """INSERT INTO messages(id,bid_id,conversation_id,client_message_id,role,parts,created_by,created_at)
           VALUES(%s,%s,%s,%s,'user',%s,%s,%s) RETURNING *""",
        (uid(), bid, conversation, message_id, Jsonb(parts), actor, now()),
    ).fetchone()
    c.execute("UPDATE conversations SET revision=revision+1 WHERE id=%s", (conversation,))
    audit(
        c,
        actor,
        bid,
        "conversation.message_recorded",
        {"conversation_id": conversation, "message_id": row["id"]},
        command_id,
    )
    return message_view(row)


def message_view(row, c=None):
    execution = None
    if (
        c is not None
        and c.execute("SELECT to_regclass('design.assistant_jobs') AS id").fetchone()["id"]
    ):
        execution = c.execute(
            "SELECT id,scope,operation_id FROM assistant_jobs WHERE project_message_id=%s OR global_message_id=%s",
            (row["id"], row["id"]),
        ).fetchone()
        part = next(
            (p for p in row["parts"] if p.get("type") == "assistant_reference_answer"), None
        )
        if execution is None and part:
            execution = c.execute(
                "SELECT id,scope,operation_id FROM assistant_jobs WHERE id=%s",
                (UUID(part["job_id"]),),
            ).fetchone()
    value = clean(row) | {
        "recorded": True,
        "assistant_operation_id": str(execution["operation_id"] or execution["id"])
        if execution
        else None,
        "formal_scoring_input": False,
    }
    if execution:
        value.update(
            assistant_job_id=str(execution["id"]), assistant_operation_scope=execution["scope"]
        )
    return value
