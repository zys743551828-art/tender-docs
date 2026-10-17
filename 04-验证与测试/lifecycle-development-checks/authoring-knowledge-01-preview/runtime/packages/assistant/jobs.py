"""API control plane for immutable assistant jobs; never performs model I/O."""

from __future__ import annotations

from uuid import UUID, uuid5

from psycopg import sql
from psycopg.types.json import Jsonb

from packages.assistant import context, conversations
from packages.assistant.protocol import normalized_turn
from packages.domain.runtime import DomainError, audit, clean, enqueue_plan, fetch, match, sha, uid

GLOBAL_NAMESPACE = UUID("ace0943a-a88b-49ce-9bb9-7552b381cfd0")
TABLES = (
    "assistant_jobs",
    "assistant_outputs",
    "assistant_publications",
    "assistant_action_links",
    "assistant_global_conversations",
    "assistant_global_messages",
    "assistant_global_runs",
    "assistant_global_calls",
    "assistant_global_outbox",
    "assistant_global_events",
)


def require_schema(c, worker=False):
    tables = TABLES[:2] + TABLES[6:9] if worker else TABLES
    for table in tables:
        relation = "design." + table
        if (
            c.execute("SELECT to_regclass(%s) AS id", (relation,)).fetchone()["id"] is None
            or not c.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT') AS ok", (relation,)
            ).fetchone()["ok"]
        ):
            raise DomainError("ASSISTANT_NOT_READY", "助手后台尚未配置所需存储或角色能力", 503)


def global_id(actor):
    return uuid5(GLOBAL_NAMESPACE, "global-main:" + str(actor))


def global_get(c, conversation, actor, *, lock=False):
    query = (
        "SELECT * FROM assistant_global_conversations WHERE id=%s AND created_by=%s FOR UPDATE"
        if lock
        else "SELECT * FROM assistant_global_conversations WHERE id=%s AND created_by=%s"
    )
    return fetch(
        c,
        query,
        (conversation, actor),
    )


def global_view(row):
    return clean(
        {
            "id": row["id"],
            "bid_id": None,
            "scope": "global",
            "title": "全局主会话",
            "is_main": True,
            "state": row["state"],
            "revision": row["revision"],
            "stream_id": row["id"],
            "last_event_seq": row["last_seq"],
            "created_at": row["created_at"],
        }
    )


def global_event(c, conversation, event, payload, event_id=None):
    head = fetch(
        c,
        "UPDATE assistant_global_conversations SET last_seq=last_seq+1,revision=revision+1 WHERE id=%s RETURNING last_seq",
        (conversation,),
    )
    c.execute(
        "INSERT INTO assistant_global_events(conversation_id,seq,event_id,event_type,payload) VALUES(%s,%s,%s,%s,%s)",
        (conversation, head["last_seq"], event_id or uid(), event, Jsonb(clean(payload))),
    )


def ensure_global(c, actor, command_id):
    require_schema(c)
    identity = global_id(actor)
    inserted = c.execute(
        "INSERT INTO assistant_global_conversations(id,created_by) VALUES(%s,%s) ON CONFLICT(created_by) DO NOTHING",
        (identity, actor),
    ).rowcount
    global_get(c, identity, actor, lock=True)
    if inserted:
        audit(c, actor, None, "assistant.global_created", {"conversation_id": identity}, command_id)
        global_event(c, identity, "conversation.created", {"conversation_id": str(identity)})
    return global_view(global_get(c, identity, actor))


def global_state(c, conversation, actor, target, command_id):
    row = global_get(c, conversation, actor, lock=True)
    if target == row["state"]:
        return global_view(row)
    if target not in conversations.TRANSITIONS[row["state"]]:
        raise DomainError("CONVERSATION_TRANSITION_INVALID", "先恢复已隐藏会话，再继续", 409)
    c.execute(
        "UPDATE assistant_global_conversations SET state=%s,revision=revision+1 WHERE id=%s",
        (target, conversation),
    )
    audit(
        c,
        actor,
        None,
        "conversation.state_changed",
        {
            "conversation_id": conversation,
            "before": row["state"],
            "after": target,
            "operations_cancelled": False,
        },
        command_id,
    )
    global_event(
        c,
        conversation,
        "conversation.state_changed",
        {"state": target, "operations_cancelled": False},
    )
    return global_view(global_get(c, conversation, actor))


def history(c, conversation, *, scope, actor):
    table = "messages" if scope == "project" else "assistant_global_messages"
    rows = c.execute(
        sql.SQL(
            "SELECT role,parts FROM {} WHERE conversation_id=%s ORDER BY created_at DESC,id DESC LIMIT 6"
        ).format(sql.Identifier(table)),
        (conversation,),
    ).fetchall()
    values = []
    for row in reversed(rows):
        text = "\n".join(p["text"] for p in row["parts"] if p.get("type") == "text")
        if text:
            values.append(
                {"role": "assistant" if row["role"] == "assistant" else "user", "content": text}
            )
    if sum(len(value["content"]) for value in values) > 50_000:
        raise DomainError(
            "ASSISTANT_HISTORY_TOO_LARGE", "最近讨论超过单次范围，请明确较短问题或选择具体来源", 422
        )
    return values


def job(c, identity):
    row = fetch(c, "SELECT * FROM assistant_jobs WHERE id=%s", (identity,))
    if sha(row["input_manifest"]) != row["input_digest"]:
        raise DomainError("ASSISTANT_INPUT_CORRUPTED", "助手冻结输入校验失败", 409)
    return row


def authorize_job(c, row, actor):
    if row["scope"] == "global":
        global_get(c, row["global_conversation_id"], actor)
    else:
        fetch(c, "SELECT id FROM bids WHERE id=%s", (row["bid_id"],))


def view(c, identity, actor=None):
    row = job(c, identity)
    if actor:
        authorize_job(c, row, actor)
    if row["scope"] == "project":
        op = fetch(
            c,
            "SELECT id,state,cancel_requested_at,handler_version FROM operations WHERE id=%s AND bid_id=%s",
            (row["operation_id"], row["bid_id"]),
        )
        operation = clean(op) | {"scope": "project", "bid_id": str(row["bid_id"])}
    else:
        op = fetch(
            c,
            "SELECT id,state,cancel_requested_at,error_code FROM assistant_global_runs WHERE id=%s",
            (identity,),
        )
        operation = clean(op) | {
            "scope": "global",
            "bid_id": None,
            "handler_version": "assistant-global/1",
        }
    pub = c.execute(
        "SELECT state,project_message_id,global_message_id,action_id,error_code FROM assistant_publications WHERE job_id=%s",
        (identity,),
    ).fetchone()
    output = c.execute(
        "SELECT payload,digest FROM assistant_outputs WHERE job_id=%s", (identity,)
    ).fetchone()
    if output and sha(output["payload"]) != output["digest"]:
        raise DomainError("ASSISTANT_OUTPUT_CORRUPTED", "助手输出校验失败", 409)
    if row["scope"] == "project":
        call = c.execute(
            "SELECT id,state,usage->>'billing_state' AS billing_state FROM model_calls WHERE operation_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["operation_id"],),
        ).fetchone()
    else:
        call = c.execute(
            "SELECT id,state,usage->>'billing_state' AS billing_state FROM assistant_global_calls WHERE run_id=%s",
            (row["id"],),
        ).fetchone()
    return clean(
        {
            "id": identity,
            "kind": row["kind"],
            "scope": row["scope"],
            "bid_id": row["bid_id"],
            "conversation_id": row["project_conversation_id"] or row["global_conversation_id"],
            "user_message_id": row["project_message_id"] or row["global_message_id"],
            "operation": operation,
            "cancel_requested": op["cancel_requested_at"] is not None,
            "cancellation_terminal": op["state"] == "cancelled",
            "model_call": call,
            "requires_billing_reconciliation": bool(
                call and call["state"] in ("reserved", "unknown")
            ),
            "publication": pub,
            "candidate": output["payload"] if output else None,
            "formal_scoring_input": False,
            "automatic_retry_authorized": False,
        }
    )


def queue_answer(c, actor, conversation, body, command_id, *, bid=None, condition=None):
    require_schema(c)
    body = normalized_turn(body)
    scope = "project" if bid else "global"
    context.lock_scope(c, context.turn_bids(c, body, bid))
    current = (
        conversations.get(c, bid, conversation)
        if bid
        else global_get(c, conversation, actor, lock=True)
    )
    table = "messages" if bid else "assistant_global_messages"
    old = c.execute(
        sql.SQL("SELECT * FROM {} WHERE conversation_id=%s AND client_message_id=%s").format(
            sql.Identifier(table)
        ),
        (conversation, UUID(body["client_message_id"])),
    ).fetchone()
    if old:
        existing = c.execute(
            sql.SQL("SELECT id,input_manifest FROM assistant_jobs WHERE {}=%s").format(
                sql.Identifier("project_message_id" if bid else "global_message_id")
            ),
            (old["id"],),
        ).fetchone()
        if existing:
            if existing["input_manifest"]["request"] != body or old["created_by"] != actor:
                raise DomainError(
                    "MESSAGE_ID_CONFLICT", "同一消息标识不能改变内容、来源、成员或预算", 409
                )
            return view(c, existing["id"], actor)
    if current["state"] != "active":
        raise DomainError("CONVERSATION_CLOSED", "请先继续这条会话再发消息", 409)
    match(condition, "conversation", conversation, current["revision"])
    frozen = context.freeze_turn(c, body, bid)
    intent = sha(
        {
            "text": body["text"],
            "context": frozen,
            "section_id": body["section_id"],
            "purpose": "assistant",
            "global_actor": str(actor) if not bid else None,
        }
    )
    existing = same_intent(c, intent, bid=bid, conversation=conversation)
    if existing:
        return view(c, existing, actor)
    from packages.domain.providers.deepseek import DeepSeekClient

    DeepSeekClient.from_env()  # Local config only; replay/reuse never needs provider capability.
    recent = history(c, conversation, scope=scope, actor=actor)
    if bid:
        saved = conversations.record_message(
            c,
            bid,
            conversation,
            actor,
            {
                "client_message_id": body["client_message_id"],
                "text": body["text"],
                "source_version_ids": sorted(
                    {s["id"] for s in body["source_selection"] if s["kind"] == "document"}
                ),
            },
            command_id,
        )
        message_id = UUID(saved["id"])
    else:
        parts = [{"type": "text", "text": body["text"]}]
        if old:
            if old["created_by"] != actor or old["parts"] != parts:
                raise DomainError("MESSAGE_ID_CONFLICT", "同一消息标识对应其他内容", 409)
            message_id = old["id"]
        else:
            message_id = uid()
            c.execute(
                "INSERT INTO assistant_global_messages(id,conversation_id,client_message_id,role,parts,created_by) VALUES(%s,%s,%s,'user',%s,%s)",
                (message_id, conversation, UUID(body["client_message_id"]), Jsonb(parts), actor),
            )
            c.execute(
                "UPDATE assistant_global_conversations SET revision=revision+1 WHERE id=%s",
                (conversation,),
            )
    identity = uid()
    manifest = clean(
        {
            "schema_version": 1,
            "kind": "answer",
            "purpose": "assistant",
            "scope": scope,
            "bid_id": bid,
            "actor_id": actor,
            "conversation_id": conversation,
            "request": body,
            "context": frozen,
            "history": recent,
            "history_coverage": "recent_6_text_messages",
            "formal_scoring_input": False,
        }
    )
    from packages.assistant.generation import request_for

    request_for(manifest)
    digest = sha(manifest)
    if bid:
        op = enqueue_plan(
            c,
            bid,
            command_id,
            "assistant-answer",
            {"id": str(identity), "digest": digest},
            stages=[{"key": "assistant-answer", "depends_on": []}],
            handler_version="assistant/1",
            topic="tender.task.assistant.v1",
        )
        operation = UUID(op["id"])
    else:
        operation = None
    c.execute(
        """INSERT INTO assistant_jobs(id,kind,scope,bid_id,project_conversation_id,project_message_id,global_conversation_id,global_message_id,operation_id,actor_id,input_manifest,input_digest,intent_digest)
        VALUES(%s,'answer',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            identity,
            scope,
            bid,
            conversation if bid else None,
            message_id if bid else None,
            conversation if not bid else None,
            message_id if not bid else None,
            operation,
            actor,
            Jsonb(manifest),
            digest,
            intent,
        ),
    )
    if not bid:
        c.execute(
            "INSERT INTO assistant_global_runs(id,conversation_id,actor_id,command_id,input_digest,max_tokens) VALUES(%s,%s,%s,%s,%s,%s)",
            (identity, conversation, actor, command_id, digest, body["max_tokens"]),
        )
        event = uid()
        payload = {
            "schema_version": 1,
            "type": "assistant-global/1",
            "event_id": str(event),
            "run_id": str(identity),
            "input_digest": digest,
        }
        c.execute(
            "INSERT INTO assistant_global_outbox(id,run_id,payload,payload_digest) VALUES(%s,%s,%s,%s)",
            (event, identity, Jsonb(payload), sha(payload)),
        )
        global_event(
            c,
            conversation,
            "assistant.queued",
            {"job_id": str(identity), "user_message_id": str(message_id)},
        )
    audit(
        c,
        actor,
        bid,
        "assistant.queued",
        {"job_id": identity, "scope": scope, "input_digest": digest, "formal_scoring_input": False},
        command_id,
    )
    return view(c, identity, actor)


def same_intent(c, intent, *, bid=None, conversation=None):
    if bid:
        rows = c.execute(
            """SELECT j.id,o.state,EXISTS(SELECT 1 FROM model_calls m WHERE m.operation_id=o.id AND m.state IN ('reserved','unknown')) AS unresolved
            FROM assistant_jobs j JOIN operations o ON o.id=j.operation_id
            WHERE j.bid_id=%s AND j.intent_digest=%s ORDER BY j.created_at DESC,j.id""",
            (bid, intent),
        ).fetchall()
    else:
        rows = c.execute(
            """SELECT j.id,r.state,EXISTS(SELECT 1 FROM assistant_global_calls m WHERE m.run_id=r.id AND m.state IN ('reserved','unknown')) AS unresolved
            FROM assistant_jobs j JOIN assistant_global_runs r ON r.id=j.id
            WHERE j.global_conversation_id=%s AND j.intent_digest=%s ORDER BY j.created_at DESC,j.id""",
            (conversation, intent),
        ).fetchall()
    for row in rows:
        if row["state"] in ("queued", "running", "cancel_requested"):
            return row["id"]
        if row["unresolved"]:
            raise DomainError(
                "MODEL_CALL_REQUIRES_RECONCILIATION",
                "此前同一问题的调用或费用尚未核清，未授权重新调用",
                409,
                {"job_id": str(row["id"]), "new_attempt_authorized": False},
            )
    return None


def queue_generation(c, bid, actor, body, command_id, origin_action=None):
    from packages.assistant.generation import request_for, selected_range
    from packages.assistant.protocol import validate
    from packages.authoring.generation import freeze_generation_plan
    from packages.lifecycle.service import assert_work_writable

    require_schema(c)
    validate("AssistantGenerationRequest", body)
    assert_work_writable(c, bid)
    frozen = freeze_generation_plan(c, bid, body)
    for version in body["source_version_ids"]:
        context.document_source(c, UUID(version))
    selection = selected_range(frozen["plan"], body)
    memory = context.project_memory(c, bid)
    intent = sha({"plan": frozen["plan"], "selection": selection, "project_memory": memory})
    existing = same_intent(c, intent, bid=bid)
    if existing:
        return view(c, existing, actor)
    from packages.domain.providers.deepseek import DeepSeekClient

    DeepSeekClient.from_env()
    identity = uid()
    manifest = clean(
        {
            "schema_version": 1,
            "kind": "authoring",
            "purpose": "authoring",
            "scope": "project",
            "bid_id": bid,
            "actor_id": actor,
            "writing_plan_id": frozen["writing_plan_id"],
            "plan": frozen["plan"],
            "project_memory": memory,
            "selection": selection,
            "request": body,
            "formal_scoring_input": False,
        }
    )
    request_for(manifest)
    digest = sha(manifest)
    op = enqueue_plan(
        c,
        bid,
        command_id,
        "authoring-generate",
        {"id": str(identity), "digest": digest},
        stages=[{"key": "authoring-generate", "depends_on": []}],
        handler_version="authoring-generate/1",
        topic="tender.task.assistant.v1",
    )
    c.execute(
        """INSERT INTO assistant_jobs(id,kind,scope,bid_id,writing_plan_id,operation_id,actor_id,origin_action_id,input_manifest,input_digest,intent_digest)
        VALUES(%s,'authoring','project',%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            identity,
            bid,
            UUID(frozen["writing_plan_id"]),
            UUID(op["id"]),
            actor,
            origin_action,
            Jsonb(manifest),
            digest,
            intent,
        ),
    )
    audit(
        c,
        actor,
        bid,
        "authoring.generation_queued",
        {
            "job_id": identity,
            "writing_plan_id": frozen["writing_plan_id"],
            "operation_id": op["id"],
            "applied": False,
        },
        command_id,
    )
    return view(c, identity, actor)
