"""Real restricted PG/ASGI/Worker protocols with explicit offline MockTransport only."""

import hashlib
import json
import os
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import test_knowledge_integration as knowledge_fixture
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.assistant_routes import router
from packages.assistant import coordinator, global_runtime, jobs
from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, assert_role, clean, connection, now, secret_hash

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ASSISTANT_INTEGRATION") != "1",
    reason="Explicit fresh assistant01 real PG opt-in; zero paid calls",
)


@pytest.fixture
def assistant(monkeypatch):
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    env = knowledge_fixture.environment.__wrapped__()
    actors = [env["actor"], uuid4()]
    tokens = [uuid4().hex, uuid4().hex]
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成第二成员')", (actors[1],))
        for actor, token in zip(actors, tokens, strict=True):
            c.execute(
                "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    actor,
                    secret_hash(token),
                    hashlib.sha256(b"explicit-csrf").hexdigest(),
                    now() + timedelta(hours=2),
                ),
            )
    calls = []
    response = {
        "answer": "有限只读解释，不代表正式评审或操作已完成。",
        "citations": [],
        "issue_suggestions": [],
        "tool_proposals": [],
        "gaps": ["显式离线机制用例，非真实模型效果"],
    }

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "mock-" + uuid4().hex,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(response, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
            },
        )

    client = DeepSeekClient(
        api_key="explicit-offline-test-only", transport=httpx.MockTransport(transport)
    )
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: client))
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "details": clean(exc.details)}, status_code=exc.status
        )

    def headers(actor=0, key=None, tag=None):
        return {
            "Cookie": "session=" + tokens[actor],
            "Origin": os.environ["APP_ORIGIN"],
            "X-CSRF-Token": "explicit-csrf",
            "Idempotency-Key": key or uuid4().hex,
        } | ({"If-Match": tag} if tag else {})

    with TestClient(app) as api:
        yield {
            "env": env,
            "actors": actors,
            "api": api,
            "headers": headers,
            "calls": calls,
            "response": response,
            "objects": objects,
        }


def global_turn(s, *, text="你好", selection=None):
    api, h = s["api"], s["headers"]
    main = api.post("/api/v1/assistant/global-conversations/main", json={}, headers=h())
    assert main.status_code == 200, main.json()
    conversation = main.json()["id"]
    request = {"client_message_id": str(uuid4()), "text": text, "source_selection": selection or []}
    accepted = api.post(
        f"/api/v1/assistant/global-conversations/{conversation}/turns",
        json=request,
        headers=h(tag=main.headers["etag"]),
    )
    assert accepted.status_code == 202, accepted.json()
    with connection() as c:
        message = c.execute(
            "SELECT payload FROM assistant_global_outbox WHERE run_id=%s",
            (UUID(accepted.json()["id"]),),
        ).fetchone()["payload"]
    return main, request, accepted, message


def test_global_real_null_scope_complete_close_restore_and_owner_isolation(assistant):
    s = assistant
    assert_role()
    assert_role(True)
    with connection() as c:
        before = c.execute("SELECT count(*) AS n FROM bids").fetchone()["n"]
    main, body, accepted, message = global_turn(s)
    api, h = s["api"], s["headers"]
    with connection() as c:
        assert c.execute("SELECT count(*) AS n FROM bids").fetchone()["n"] == before
        row = jobs.job(c, UUID(accepted.json()["id"]))
        assert row["bid_id"] is None and row["operation_id"] is None
    conversation = main.json()["id"]
    refreshed = api.get("/api/v1/assistant/global-conversations/main", headers=h())
    close = api.post(
        f"/api/v1/assistant/global-conversations/{conversation}/state",
        json={"state": "closed"},
        headers=h(tag=refreshed.headers["etag"]),
    )
    assert close.status_code == 200
    assert global_runtime.handle(message)
    assert not global_runtime.handle(message)
    published = coordinator.publish(UUID(accepted.json()["id"]))
    assert published["publication"]["state"] == "published" and len(s["calls"]) == 1
    assert (
        published["operation"]["bid_id"] is None and published["operation"]["state"] == "succeeded"
    )
    history = api.get(
        f"/api/v1/assistant/global-conversations/{conversation}/messages", headers=h()
    )
    assert history.status_code == 200 and [m["role"] for m in history.json()["items"]] == [
        "user",
        "assistant",
    ]
    assert all(m["bid_id"] is None for m in history.json()["items"])
    assert history.json()["items"][0]["assistant_operation_id"] == accepted.json()["id"]
    assert (
        api.get(
            f"/api/v1/assistant/global-conversations/{conversation}/messages", headers=h(actor=1)
        ).status_code
        == 404
    )
    events = api.get(
        f"/api/v1/assistant/global-conversations/{conversation}/events?after=0", headers=h()
    )
    assert events.status_code == 200 and events.json()["next_cursor"] >= 3
    last = events.json()["next_cursor"]
    assert (
        api.get(
            f"/api/v1/assistant/global-conversations/{conversation}/events?after={last}",
            headers=h(),
        ).json()["items"]
        == []
    )
    assert (
        api.get("/api/v1/assistant/global-conversations/main", headers=h()).json()["state"]
        == "closed"
    )
    assert not any(m.get("bid_id") for m in history.json()["items"])
    current = api.get("/api/v1/assistant/global-conversations/main", headers=h())
    restored = api.post(
        f"/api/v1/assistant/global-conversations/{conversation}/state",
        json={"state": "active"},
        headers=h(tag=current.headers["etag"]),
    )
    assert restored.status_code == 200 and restored.json()["id"] == conversation
    assert (
        len(
            api.get(
                f"/api/v1/assistant/global-conversations/{conversation}/messages", headers=h()
            ).json()["items"]
        )
        == 2
        and len(s["calls"]) == 1
    )


def test_global_unknown_never_reissues_for_replay_or_new_nonce_same_question(
    assistant, monkeypatch
):
    s = assistant
    seen = []

    def timeout(request):
        seen.append(request)
        raise httpx.ReadTimeout("explicit-offline-timeout")

    mock = DeepSeekClient(api_key="explicit-offline-only", transport=httpx.MockTransport(timeout))
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: mock))
    main, body, accepted, message = global_turn(s, text="明确同一问题")
    assert global_runtime.handle(message) and len(seen) == 1
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert (
        result["operation"]["state"] == "failed"
        and result["model_call"]["state"] == "unknown"
        and result["requires_billing_reconciliation"]
    )
    assert result["candidate"] is None
    api, h = s["api"], s["headers"]
    replay = api.post(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/turns",
        json=body,
        headers=h(tag=main.headers["etag"]),
    )
    assert replay.status_code == 202 and replay.json()["id"] == accepted.json()["id"]
    latest = api.get("/api/v1/assistant/global-conversations/main", headers=h())
    rerun = api.post(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/turns",
        json=body | {"client_message_id": str(uuid4())},
        headers=h(tag=latest.headers["etag"]),
    )
    assert rerun.status_code == 409 and rerun.json()["code"] == "MODEL_CALL_REQUIRES_RECONCILIATION"
    assert len(seen) == 1
    assert not global_runtime.handle(message)


def test_global_cancellation_before_execution_reports_request_then_real_terminal(assistant):
    s = assistant
    main, body, accepted, message = global_turn(s)
    cancel = s["api"].post(
        f"/api/v1/assistant/jobs/{accepted.json()['id']}/cancel", json={}, headers=s["headers"]()
    )
    assert (
        cancel.status_code == 202
        and cancel.json()["cancel_requested"]
        and not cancel.json()["cancellation_terminal"]
    )
    assert not global_runtime.handle(message)
    current = s["api"].get(
        f"/api/v1/assistant/jobs/{accepted.json()['id']}", headers=s["headers"]()
    )
    assert (
        current.json()["operation"]["state"] == "cancelled"
        and current.json()["cancellation_terminal"]
    )
    assert s["calls"] == []
    coordinator.tick([UUID(accepted.json()["id"])])
    events = (
        s["api"]
        .get(
            f"/api/v1/assistant/global-conversations/{main.json()['id']}/events",
            headers=s["headers"](),
        )
        .json()["items"]
    )
    assert sum(e["event_type"] == "assistant.execution_finished" for e in events) == 1
    coordinator.tick([UUID(accepted.json()["id"])])
    assert (
        s["api"]
        .get(
            f"/api/v1/assistant/global-conversations/{main.json()['id']}/events",
            headers=s["headers"](),
        )
        .json()["items"]
        == events
    )


def test_global_expired_fence_cannot_reserve_or_publish(assistant):
    s = assistant
    _, _, accepted, message = global_turn(s)
    old = global_runtime.acquire(message)
    with connection(True) as c:
        c.execute(
            "UPDATE assistant_global_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (UUID(accepted.json()["id"]),),
        )
    current = global_runtime.acquire(message)
    assert current["fence"] > old["fence"]
    with pytest.raises(DomainError) as error:
        global_runtime.compute(message, old)
    assert error.value.code == "MODEL_EXECUTION_STALE" and s["calls"] == []
    assert not global_runtime.finish(message, old, error=DomainError("EXPLICIT_FIXTURE", "stale"))
    assert global_runtime.finish(
        message, current, error=DomainError("EXPLICIT_FIXTURE", "new fence")
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_outputs WHERE job_id=%s",
                (UUID(accepted.json()["id"]),),
            ).fetchone()["n"]
            == 0
        )


def test_global_actual_schema_and_cursor_contracts(assistant):
    from jsonschema import Draft202012Validator, FormatChecker

    from apps.api.export_openapi import implemented_openapi

    s = assistant
    main, _, accepted, message = global_turn(s)
    actual = implemented_openapi(s["api"].app)

    def validate(name, value):
        Draft202012Validator(
            {"components": actual["components"], "$ref": "#/components/schemas/" + name},
            format_checker=FormatChecker(),
        ).validate(value)

    validate("AssistantGlobalConversation", main.json())
    validate("AssistantJob", accepted.json())
    assert global_runtime.handle(message)
    validate("AssistantJob", coordinator.publish(UUID(accepted.json()["id"])))
    history = s["api"].get(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/messages",
        headers=s["headers"](),
    )
    validate("AssistantGlobalMessagePage", history.json())
    events = s["api"].get(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/events", headers=s["headers"]()
    )
    validate("AssistantGlobalEventPage", events.json())


def selection(s, kind, bid, **kwargs):
    response = s["api"].get(
        "/api/v1/assistant/sources",
        params={"kind": kind, "bid_id": str(bid)} | kwargs,
        headers=s["headers"](),
    )
    assert response.status_code == 200, response.json()
    rows = [r for r in response.json()["items"] if r["available"] and r["bid_id"] == str(bid)]
    assert rows, response.json()
    return rows[0]["reference"]


def sourced_turn(s, *, tools=True):
    bid = s["env"]["origin"]
    refs = [selection(s, "project", bid), selection(s, "document", bid, physical_page=1)]
    if tools:
        s["response"]["tool_proposals"] = [
            {
                "tool": "knowledge.candidate",
                "target_bid_id": str(bid),
                "arguments": {
                    "title": "助手合成待核候选",
                    "content": "仅待完善，不代表当前工程事实。",
                    "scope": "待人工核验",
                    "source_version_ids": [refs[1]["id"]],
                },
            }
        ]
    main, request, accepted, message = global_turn(s, selection=refs)
    with connection() as c:
        row = jobs.job(c, UUID(accepted.json()["id"]))
        source = next(
            v
            for v in row["input_manifest"]["context"]["sources"]
            if v["selection"]["kind"] == "document"
        )
    s["response"]["citations"] = [
        {"source_key": source["source_key"], "quote": s["env"]["body"]["source_refs"][0]["excerpt"]}
    ]
    s["response"]["issue_suggestions"] = [
        {
            "statement": "合成待核建议，不作为已确认问题",
            "severity": "minor",
            "source_keys": [source["source_key"]],
            "needs_basis": False,
        }
    ]
    return main, request, accepted, message


def published_card(s):
    _, _, accepted, message = sourced_turn(s)
    assert global_runtime.handle(message)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert result["publication"]["state"] == "published", result
    with connection() as c:
        identity = c.execute(
            "SELECT action_id FROM assistant_action_links WHERE job_id=%s",
            (UUID(accepted.json()["id"]),),
        ).fetchone()["action_id"]
    card = s["api"].get(f"/api/v1/assistant/actions/{identity}", headers=s["headers"]())
    assert card.status_code == 200 and card.json()["state"] == "pending"
    return identity, card


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_sourced_global_action_cross_member_exactly_once_actual_candidate_or_rejection(
    assistant, decision
):
    s = assistant
    identity, card = published_card(s)
    with connection() as c:
        before = c.execute("SELECT count(*) AS n FROM retrieval.knowledge_items").fetchone()["n"]
    resolved = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": decision},
        headers=s["headers"](actor=1, tag=card.headers["etag"]),
    )
    assert resolved.status_code == 200, resolved.json()
    expected = "executed" if decision == "confirm" else "rejected"
    assert resolved.json()["state"] == expected
    # Another member, opposite choice, new command and stale original ETag sees the one receipt.
    repeat = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "reject" if decision == "confirm" else "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert repeat.status_code == 200 and repeat.json() == resolved.json()
    with connection() as c:
        assert c.execute("SELECT count(*) AS n FROM retrieval.knowledge_items").fetchone()[
            "n"
        ] == before + (decision == "confirm")
        assert (
            c.execute(
                "SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM findings WHERE bid_id=%s", (s["env"]["origin"],)
            ).fetchone()["n"]
            == 0
        )
    assert len(s["calls"]) == 1


def test_source_withdrawn_before_dispatch_no_call_and_during_model_no_publication(
    assistant, monkeypatch
):
    s = assistant
    _, _, accepted, message = sourced_turn(s, tools=False)
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (s["env"]["root"],),
        )
    assert global_runtime.handle(message) and len(s["calls"]) == 0
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert result["operation"]["state"] == "failed" and result["publication"] is None
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET status='published',revision=revision+1 WHERE source_id=%s",
            (s["env"]["root"],),
        )
    # New current selection has a fresh authority revision; a remote window withdrawal is final-API stale.
    _, _, accepted2, message2 = sourced_turn(s, tools=False)
    original = global_runtime.provider
    client = original()
    old_transport = client._transport

    def withdraw(request):
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                (s["env"]["root"],),
            )
        return old_transport.handle_request(request)

    changed = DeepSeekClient(
        api_key="explicit-offline-only", transport=httpx.MockTransport(withdraw)
    )
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: changed))
    assert global_runtime.handle(message2)
    result2 = coordinator.publish(UUID(accepted2.json()["id"]))
    assert (
        result2["operation"]["state"] == "succeeded" and result2["publication"]["state"] == "stale"
    )
    assert result2["publication"]["global_message_id"] is None and len(s["calls"]) == 1


def test_action_context_change_stale_and_plain_yes_cannot_consume(assistant):
    s = assistant
    identity, card = published_card(s)
    with connection() as c:
        c.execute("UPDATE bids SET revision=revision+1 WHERE id=%s", (s["env"]["origin"],))
    invalid = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "好的"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert invalid.status_code == 422
    stale = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert stale.status_code == 409 and stale.json()["state"] == "stale", stale.json()
    assert not stale.json()["result"]["assistant_resolution"]["business_effect_executed"]


def test_global_call_reservation_serializes_different_runs_same_intent(assistant):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from psycopg.types.json import Jsonb

    from packages.domain.runtime import sha

    s = assistant
    _, _, accepted, message = global_turn(s)
    with connection() as c:
        row = jobs.job(c, UUID(accepted.json()["id"]))
        second, second_message, command = uuid4(), uuid4(), uuid4()
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'explicit-concurrency-fixture',%s,%s,%s)",
            (command, row["actor_id"], second, uuid4().hex, sha("fixture")),
        )
        c.execute(
            "INSERT INTO assistant_global_messages(id,conversation_id,client_message_id,role,parts,created_by) VALUES(%s,%s,%s,'user','[]',%s)",
            (second_message, row["global_conversation_id"], uuid4(), row["actor_id"]),
        )
        c.execute(
            "INSERT INTO assistant_jobs(id,kind,scope,global_conversation_id,global_message_id,actor_id,input_manifest,input_digest,intent_digest) VALUES(%s,'answer','global',%s,%s,%s,%s,%s,%s)",
            (
                second,
                row["global_conversation_id"],
                second_message,
                row["actor_id"],
                Jsonb(row["input_manifest"]),
                row["input_digest"],
                row["intent_digest"],
            ),
        )
        c.execute(
            "INSERT INTO assistant_global_runs(id,conversation_id,actor_id,command_id,input_digest,max_tokens) VALUES(%s,%s,%s,%s,%s,2048)",
            (second, row["global_conversation_id"], row["actor_id"], command, row["input_digest"]),
        )
        event = uuid4()
        message2 = message | {"run_id": str(second), "event_id": str(event)}
        c.execute(
            "INSERT INTO assistant_global_outbox(id,run_id,payload,payload_digest) VALUES(%s,%s,%s,%s)",
            (event, second, Jsonb(message2), sha(message2)),
        )
    leases = [global_runtime.acquire(m) for m in (message, message2)]
    barrier = Barrier(2)

    def reserve(pair):
        msg, lease = pair
        with connection(True) as c:
            candidate = global_runtime.checked_message(c, msg)
        barrier.wait()
        try:
            call, created = global_runtime.reserve(
                candidate,
                lease,
                {"max_tokens": 2048, "history_variant": msg["event_id"]},
                {"purpose": "assistant", "fixture": str(second)},
            )
            return created
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, zip((message, message2), leases, strict=True)))
    assert results.count(True) == 1 and results.count("MODEL_CALL_REQUIRES_RECONCILIATION") == 1
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_global_calls WHERE run_id=ANY(%s)",
                ([row["id"], second],),
            ).fetchone()["n"]
            == 1
        )
    assert not s["calls"]


def test_remote_window_has_no_worker_transaction_and_cancel_suppresses_billed_answer(
    assistant, monkeypatch
):
    s = assistant
    _, _, accepted, message = global_turn(s)
    original_transport = global_runtime.provider()._transport
    probes = []

    def during_remote(request):
        with connection(True) as c:
            held = c.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'"
            ).fetchone()["n"]
            assert held == 0
            probes.append(held)
        cancelled = s["api"].post(
            f"/api/v1/assistant/jobs/{accepted.json()['id']}/cancel",
            json={},
            headers=s["headers"](),
        )
        assert cancelled.status_code == 202 and not cancelled.json()["cancellation_terminal"]
        return original_transport.handle_request(request)

    client = DeepSeekClient(
        api_key="explicit-offline-only", transport=httpx.MockTransport(during_remote)
    )
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: client))
    assert global_runtime.handle(message)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert probes == [0] and len(s["calls"]) == 1
    assert (
        result["operation"]["state"] == "cancelled" and result["model_call"]["state"] == "succeeded"
    )
    assert (
        result["model_call"]["billing_state"] == "usage_reported" and result["publication"] is None
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_outputs WHERE job_id=%s",
                (UUID(accepted.json()["id"]),),
            ).fetchone()["n"]
            == 0
        )


def test_expired_card_rejects_effect_and_unsupported_model_tool_is_billed_failure(
    assistant, monkeypatch
):
    from packages.assistant import context

    s = assistant
    identity, card = published_card(s)
    with connection() as c:
        expires = c.execute(
            "SELECT expires_at FROM proposed_actions WHERE id=%s", (identity,)
        ).fetchone()["expires_at"]
    original_at = context.at
    monkeypatch.setattr(context, "at", lambda c: expires + timedelta(seconds=1))
    expired = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert expired.status_code == 409 and expired.json()["state"] == "expired"
    monkeypatch.setattr(context, "at", original_at)
    s["response"]["tool_proposals"] = [
        {"tool": "formal_score", "target_bid_id": str(s["env"]["origin"]), "arguments": {}}
    ]
    s["response"]["citations"] = []
    s["response"]["issue_suggestions"] = []
    _, _, accepted, message = global_turn(s, text="不能执行未知工具")
    assert global_runtime.handle(message)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert (
        result["operation"]["state"] == "failed"
        and result["model_call"]["state"] == "failed"
        and result["model_call"]["billing_state"] == "usage_reported"
        and result["publication"] is None
    )
    assert not result["requires_billing_reconciliation"]


def test_dedicated_global_outbox_relay_ack_and_consumer_duplicate_protocol(assistant):
    from packages.assistant import transport

    s = assistant
    _, _, accepted, message = global_turn(s)
    sent = []

    class Producer:
        def produce(self, topic, *, key, value, on_delivery):
            sent.append((topic, key, json.loads(value)))
            on_delivery(None, None)

        def poll(self, timeout):
            raise AssertionError("synchronous explicit producer fixture should complete")

    own_scope = [UUID(accepted.json()["id"])]
    assert transport.relay_once(Producer(), run_ids=own_scope) == 1
    assert sent == [(transport.TOPIC, accepted.json()["id"], message)]
    assert transport.relay_once(Producer(), run_ids=own_scope) == 0

    class Record:
        def error(self):
            return None

        def topic(self):
            return transport.TOPIC

        def value(self):
            return json.dumps(message).encode()

    class Consumer:
        commits = 0

        def poll(self, timeout):
            return Record()

        def commit(self, **kwargs):
            assert not kwargs["asynchronous"]
            self.commits += 1

    consumer = Consumer()
    assert transport.consume_one(consumer) and transport.consume_one(consumer)
    assert consumer.commits == 2 and len(s["calls"]) == 1
    assert coordinator.publish(UUID(accepted.json()["id"]))["publication"]["state"] == "published"


@pytest.fixture
def project_engine(monkeypatch):
    """Use actual shared registry when integrated; explicit two-port adapter until then."""
    from apps.worker import engine
    from packages.assistant import tasks

    if "assistant/1" not in engine.ORDINARY_HANDLERS:
        monkeypatch.setattr(
            engine,
            "ORDINARY_HANDLERS",
            engine.ORDINARY_HANDLERS + ("assistant/1", "authoring-generate/1"),
        )
        original_compute = engine.run_compute
        monkeypatch.setattr(
            engine,
            "run_compute",
            lambda message, lease: tasks.compute(message, lease)
            if message["handler_version"] in ("assistant/1", "authoring-generate/1")
            else original_compute(message, lease),
        )
        monkeypatch.setattr(engine, "publish", tasks.publish)
    return engine


def project_turn(s, bid, *, selection=None):
    main = s["api"].post(f"/api/v1/bids/{bid}/conversations/main", json={}, headers=s["headers"]())
    assert main.status_code == 200
    request = {
        "client_message_id": str(uuid4()),
        "text": "解释本项目当前资料，不执行正式评审",
        "source_selection": selection or [],
    }
    accepted = s["api"].post(
        f"/api/v1/bids/{bid}/conversations/{main.json()['id']}/turns",
        json=request,
        headers=s["headers"](tag=main.headers["etag"]),
    )
    assert accepted.status_code == 202, accepted.json()
    return main, accepted


def execute_project(s, engine, accepted):
    with connection() as c:
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s",
            (UUID(accepted.json()["operation"]["id"]),),
        ).fetchone()["payload"]
    lease = engine.acquire(message)
    assert lease
    computed = engine.run_compute(message, lease)
    assert engine.finish(message, lease, computed)
    assert engine.acquire(message) is None
    return coordinator.publish(UUID(accepted.json()["id"]))


def test_project_answer_real_core_ledger_receipt_and_original_conversation_recovery(
    assistant, project_engine
):
    s = assistant
    bid = s["env"]["origin"]
    doc = selection(s, "document", bid, physical_page=1)
    main, accepted = project_turn(s, bid, selection=[doc])
    assert accepted.json()["operation"]["bid_id"] == str(bid)
    result = execute_project(s, project_engine, accepted)
    assert result["publication"]["state"] == "published" and len(s["calls"]) == 1
    history = s["api"].get(
        f"/api/v1/bids/{bid}/conversations/{main.json()['id']}/messages",
        headers=s["headers"](actor=1),
    )
    assert history.status_code == 200 and len(history.json()["items"]) == 2
    assert {r["assistant_operation_id"] for r in history.json()["items"]} == {
        accepted.json()["operation"]["id"]
    }
    with connection() as c:
        call = c.execute(
            "SELECT * FROM model_calls WHERE operation_id=%s", (UUID(result["operation"]["id"]),)
        ).fetchone()
        assert (
            call["state"] == "succeeded"
            and call["research_run_id"] is None
            and call["reserved_amount"] is None
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (UUID(result["operation"]["id"]),),
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_global_runs WHERE id=%s", (UUID(result["id"]),)
            ).fetchone()["n"]
            == 0
        )


def authoring_request(s, *, source_versions=None, evidence_ids=None):
    import copy

    from packages.authoring.content import SCHEMA_DIGEST
    from packages.authoring.repository import create_draft, save_draft
    from packages.domain.runtime import etag

    bid = s["env"]["origin"]
    with connection() as c:
        draft, _ = create_draft(c, bid, s["actors"][0], "助手编制合成正文")
        section = c.execute(
            "SELECT * FROM section_revisions WHERE draft_id=%s", (draft,)
        ).fetchone()
        content = copy.deepcopy(section["content"])
        content["content"][0]["content"] = [
            {"type": "text", "text": "保留前文，选中文字，保留后文。"}
        ]
        save_draft(
            c,
            bid,
            draft,
            s["actors"][0],
            {
                "base_draft_revision": 1,
                "changes": [
                    {
                        "section_id": str(section["section_id"]),
                        "base_section_revision": 1,
                        "schema_digest": SCHEMA_DIGEST,
                        "content": content,
                    }
                ],
            },
            etag("draft", draft, 1),
        )
    body = {
        "section_id": str(section["section_id"]),
        "base_section_revision": 2,
        "instruction": "仅改选区，保留外部内容",
        "source_version_ids": source_versions or [],
        "node_id": content["content"][0]["attrs"]["id"],
        "selection_start": 5,
        "selection_end": 9,
    }
    s["response"].clear()
    s["response"].update(replacement_text="受控改写", evidence_ids=evidence_ids or [])
    accepted = s["api"].post(
        f"/api/v1/bids/{bid}/authoring/generations",
        json=body,
        headers=s["headers"](tag=etag("draft", draft, 2)),
    )
    assert accepted.status_code == 202, accepted.json()
    return bid, draft, section, content, body, accepted


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_selected_authoring_ledger_proposal_one_original_action_across_native_entry(
    assistant, project_engine, decision
):
    from packages.assistant.generation import node_text
    from packages.authoring.patches import apply_proposal
    from packages.domain.runtime import Command, etag

    s = assistant
    bid, draft, section, before, body, accepted = authoring_request(s)
    result = execute_project(s, project_engine, accepted)
    assert result["publication"]["state"] == "published" and len(s["calls"]) == 1
    identity = UUID(result["publication"]["action_id"])
    with connection() as c:
        assert (
            c.execute("SELECT revision FROM drafts WHERE id=%s", (draft,)).fetchone()["revision"]
            == 2
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM proposed_actions WHERE id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )
    card = s["api"].get(f"/api/v1/assistant/actions/{identity}", headers=s["headers"]())
    assert card.status_code == 200 and node_text(
        card.json()["preview"]["before"]["content"][0]
    ) == node_text(before["content"][0])
    assert (
        node_text(card.json()["preview"]["after"]["content"][0]) == "保留前文，受控改写，保留后文。"
    )
    resolved = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": decision},
        headers=s["headers"](actor=1, tag=card.headers["etag"]),
    )
    assert resolved.status_code == 200, resolved.json()
    assert resolved.json()["state"] == ("executed" if decision == "confirm" else "rejected")
    with connection() as c:
        assert c.execute("SELECT revision FROM drafts WHERE id=%s", (draft,)).fetchone()[
            "revision"
        ] == 2 + (decision == "confirm")
        assert (
            c.execute(
                "SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )
    # The original native A2 apply endpoint shares the same consumed Action, including reject.
    with pytest.raises(DomainError) as error, connection() as c:
        cmd = Command(c, s["actors"][0], "native-cross-entry-fixture", identity, uuid4().hex, {})
        apply_proposal(c, bid, identity, s["actors"][0], cmd.id, etag("draft", draft, 2))
    assert error.value.code == "ACTION_CONSUMED"


def test_authoring_different_selection_is_distinct_intent_and_edit_during_generation_stale(
    assistant, project_engine
):
    from packages.authoring.content import SCHEMA_DIGEST
    from packages.authoring.repository import save_draft
    from packages.domain.runtime import etag

    s = assistant
    bid, draft, section, before, body, accepted = authoring_request(s)
    other = s["api"].post(
        f"/api/v1/bids/{bid}/authoring/generations",
        json=body | {"selection_start": 0, "selection_end": 4},
        headers=s["headers"](tag=etag("draft", draft, 2)),
    )
    assert other.status_code == 202 and other.json()["id"] != accepted.json()["id"]
    with connection() as c:
        save_draft(
            c,
            bid,
            draft,
            s["actors"][0],
            {
                "base_draft_revision": 2,
                "changes": [
                    {
                        "section_id": str(section["section_id"]),
                        "base_section_revision": 2,
                        "schema_digest": SCHEMA_DIGEST,
                        "content": before,
                    }
                ],
            },
            etag("draft", draft, 2),
        )
    result = execute_project(s, project_engine, accepted)
    assert result["operation"]["state"] == "succeeded" and result["publication"]["state"] == "stale"
    assert result["publication"]["action_id"] is None
    with connection() as c:
        assert (
            c.execute("SELECT revision FROM drafts WHERE id=%s", (draft,)).fetchone()["revision"]
            == 3
        )


def validate_actual(s, name, value):
    from jsonschema import Draft202012Validator, FormatChecker

    from apps.api.export_openapi import implemented_openapi

    actual = implemented_openapi(s["api"].app)
    Draft202012Validator(
        {"components": actual["components"], "$ref": "#/components/schemas/" + name},
        format_checker=FormatChecker(),
    ).validate(value)


def test_source_chooser_strict_version_cross_project_guard_csrf_and_action_actual_schema(assistant):
    s = assistant
    identity, card = published_card(s)
    validate_actual(s, "AssistantAction", card.json())
    missing_csrf = s["headers"](tag=card.headers["etag"])
    missing_csrf.pop("X-CSRF-Token")
    assert (
        s["api"]
        .post(
            f"/api/v1/assistant/actions/{identity}/resolve",
            json={"decision": "confirm"},
            headers=missing_csrf,
        )
        .status_code
        == 403
    )
    resolved = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    validate_actual(s, "AssistantAction", resolved.json())
    wrong = selection(s, "document", s["env"]["origin"], physical_page=1)
    main = s["api"].post(
        f"/api/v1/bids/{s['env']['target']}/conversations/main", json={}, headers=s["headers"]()
    )
    body = {
        "client_message_id": str(uuid4()),
        "text": "不扩大跨项目原件资格",
        "source_selection": [wrong],
    }
    rejected = s["api"].post(
        f"/api/v1/bids/{s['env']['target']}/conversations/{main.json()['id']}/turns",
        json=body,
        headers=s["headers"](tag=main.headers["etag"]),
    )
    assert (
        rejected.status_code == 409 and rejected.json()["code"] == "ASSISTANT_SOURCE_SCOPE_INVALID"
    )
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s",
            (s["env"]["root"],),
        )
    global_main = s["api"].get(
        "/api/v1/assistant/global-conversations/main", headers=s["headers"]()
    )
    stale = s["api"].post(
        f"/api/v1/assistant/global-conversations/{global_main.json()['id']}/turns",
        json=body,
        headers=s["headers"](tag=global_main.headers["etag"]),
    )
    assert stale.status_code == 412 and stale.json()["code"] == "ASSISTANT_SOURCE_CHANGED"
    assert len(s["calls"]) == 1


def test_confirmed_current_facts_in_prompt_and_identity_change_native_apply_guard(
    assistant, project_engine
):
    from types import SimpleNamespace

    from packages.assistant import context
    from packages.authoring.patches import apply_proposal
    from packages.domain.runtime import Command, etag
    from packages.identity.repository import record_binding

    s = assistant
    env = knowledge_fixture.conditional.__wrapped__(s["env"], SimpleNamespace(param="3"))
    knowledge_fixture.add_confirmed_fact(env)
    with connection() as c:
        context.lock_scope(c, [env["target"]])
        memory = context.project_memory(c, env["target"])
        assert len(memory["confirmed_facts"]) == 1 and memory["source_authorities"]
        fact = memory["confirmed_facts"][0]
        assert (
            fact["subject"] == "工程"
            and fact["scope"] == "当前项目"
            and fact["value"] == "3"
            and fact["unit"] == "m"
        )
    _, accepted = project_turn(s, env["target"])
    result = execute_project(s, project_engine, accepted)
    assert result["publication"]["state"] == "published"
    prompt = json.loads(s["calls"][0]["messages"][-1]["content"])
    assert prompt["context"]["projects"][0]["confirmed_facts"] == memory["confirmed_facts"]
    assert "source_authorities" not in prompt["context"]["projects"][0]
    bid, draft, section, before, body, generated = authoring_request(s)
    proposal = execute_project(s, project_engine, generated)
    validate_actual(s, "AssistantJob", proposal)
    identity = UUID(proposal["publication"]["action_id"])
    with connection() as c:
        record_binding(
            c,
            bid,
            s["actors"][0],
            None,
            reason="相同UUID重新人工核验也形成新权威基准",
            confirmed=True,
        )
    with pytest.raises(DomainError) as error, connection() as c:
        command = Command(
            c, s["actors"][0], "native-context-change-fixture", identity, uuid4().hex, {}
        )
        apply_proposal(c, bid, identity, s["actors"][0], command.id, etag("draft", draft, 2))
    assert error.value.code == "AUTHORING_CONTEXT_CHANGED"
    card = s["api"].get(f"/api/v1/assistant/actions/{identity}", headers=s["headers"]())
    stale = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert stale.status_code == 409 and stale.json()["state"] == "stale"


def test_archived_project_keeps_readonly_answer_but_rejects_action_and_generation(
    assistant, project_engine
):
    s = assistant
    bid = s["env"]["origin"]
    from packages.authoring.repository import create_draft
    from packages.domain.runtime import etag

    with connection() as c:
        draft, _ = create_draft(c, bid, s["actors"][0], "归档前合成正文")
        section = c.execute("SELECT id FROM sections WHERE draft_id=%s", (draft,)).fetchone()["id"]
        c.execute(
            "UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",
            (bid,),
        )
    generation = s["api"].post(
        f"/api/v1/bids/{bid}/authoring/generations",
        json={
            "section_id": str(section),
            "base_section_revision": 1,
            "instruction": "关闭工程不得新生成",
            "source_version_ids": [],
        },
        headers=s["headers"](tag=etag("draft", draft, 1)),
    )
    assert generation.status_code == 409 and generation.json()["code"] == "PROJECT_WORK_CLOSED"
    s["response"]["tool_proposals"] = [
        {"tool": "review.request", "target_bid_id": str(bid), "arguments": {}}
    ]
    _, accepted = project_turn(s, bid)
    result = execute_project(s, project_engine, accepted)
    assert result["publication"]["state"] == "published"
    with connection() as c:
        parts = c.execute(
            "SELECT parts FROM messages WHERE id=%s",
            (UUID(result["publication"]["project_message_id"]),),
        ).fetchone()["parts"]
        assert parts[1]["action_ids"] == [] and parts[1]["rejected_proposals"]
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_action_links WHERE job_id=%s",
                (UUID(result["id"]),),
            ).fetchone()["n"]
            == 0
        )
    assert len(s["calls"]) == 1


def test_authoring_actual_source_allowlist_survives_into_applied_revision_citation(
    assistant, project_engine
):
    from packages.domain.runtime import sha

    s = assistant
    ref = s["env"]["body"]["source_refs"][0]
    bid = s["env"]["origin"]
    with connection() as c:
        material = uuid4()
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
            (material, bid, UUID(ref["document_version_id"])),
        )
        c.execute("UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s", (material, bid))
    bid, draft, section, before, body, accepted = authoring_request(
        s, source_versions=[ref["document_version_id"]], evidence_ids=[ref["evidence_id"]]
    )
    result = execute_project(s, project_engine, accepted)
    assert result["publication"]["state"] == "published", result
    identity = UUID(result["publication"]["action_id"])
    card = s["api"].get(f"/api/v1/assistant/actions/{identity}", headers=s["headers"]())
    validate_actual(s, "AssistantAction", card.json())
    assert (
        card.json()["preview"]["after"]["attrs"]["source_refs"][0]["evidence_id"]
        == ref["evidence_id"]
    )
    applied = s["api"].post(
        f"/api/v1/assistant/actions/{identity}/resolve",
        json={"decision": "confirm"},
        headers=s["headers"](tag=card.headers["etag"]),
    )
    assert applied.status_code == 200, applied.json()
    validate_actual(s, "AssistantAction", applied.json())
    with connection() as c:
        refs = c.execute(
            "SELECT r.content FROM sections s JOIN section_revisions r ON r.section_id=s.id AND r.revision=s.head_revision WHERE s.id=%s",
            (section["section_id"],),
        ).fetchone()["content"]["attrs"]["source_refs"]
        assert (
            refs[0]["evidence_id"] == ref["evidence_id"]
            and refs[0]["source_id"] == ref["source_id"]
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s AND evidence_id=%s AND purpose='authoring'",
                (bid, UUID(ref["evidence_id"])),
            ).fetchone()["n"]
            >= 1
        )


def test_failed_physical_page_is_unselectable_and_never_queued(assistant):
    from packages.domain.runtime import sha

    s = assistant
    doc = s["env"]["body"]["source_refs"][0]["document_version_id"]
    with connection() as c:
        parse = uuid4()
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial','[]')",
            (parse, s["env"]["origin"], UUID(doc), sha("explicit-failed-page"), sha({})),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'failed','[]')",
            (s["env"]["origin"], parse),
        )
    unavailable = s["api"].get(
        "/api/v1/assistant/sources",
        params={"kind": "document", "bid_id": str(s["env"]["origin"]), "physical_page": 1},
        headers=s["headers"](),
    )
    assert unavailable.status_code == 200
    item = next(row for row in unavailable.json()["items"] if row["title"] == "合成工法原文.pdf")
    assert (
        not item["available"]
        and item["reference"] is None
        and item["reasons"] == ["ASSISTANT_SOURCE_NOT_READY"]
    )
    assert not s["calls"]


def test_concurrent_members_opposite_action_only_one_receipt_effect(assistant):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s = assistant
    identity, card = published_card(s)
    with connection() as c:
        before = c.execute("SELECT count(*) AS n FROM retrieval.knowledge_items").fetchone()["n"]
    barrier = Barrier(2)

    def resolve(actor):
        barrier.wait()
        return s["api"].post(
            f"/api/v1/assistant/actions/{identity}/resolve",
            json={"decision": "confirm" if actor else "reject"},
            headers=s["headers"](actor=actor, tag=card.headers["etag"]),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, [0, 1]))
    assert all(r.status_code == 200 for r in results) and results[0].json() == results[1].json()
    state = results[0].json()["state"]
    assert state in ["executed", "rejected"]
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )
        assert c.execute("SELECT count(*) AS n FROM retrieval.knowledge_items").fetchone()[
            "n"
        ] == before + (state == "executed")


def test_global_paid_result_late_fence_reuses_exact_blob_without_second_call(assistant):
    s = assistant
    _, _, accepted, message = global_turn(s)
    lease1 = global_runtime.acquire(message)
    outcome1 = global_runtime.compute(message, lease1)
    with connection(True) as c:
        c.execute(
            "UPDATE assistant_global_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (UUID(accepted.json()["id"]),),
        )
    lease2 = global_runtime.acquire(message)
    assert lease2["fence"] == lease1["fence"] + 1
    assert not global_runtime.finish(message, lease1, outcome1)
    outcome2 = global_runtime.compute(message, lease2)
    assert outcome2["call_id"] == outcome1["call_id"] and outcome2["payload"] == outcome1["payload"]
    assert global_runtime.finish(message, lease2, outcome2)
    assert coordinator.publish(UUID(accepted.json()["id"]))["publication"]["state"] == "published"
    assert len(s["calls"]) == 1


def test_project_cancel_during_model_does_not_publish_or_fake_terminal(
    assistant, project_engine, monkeypatch
):
    s = assistant
    _, accepted = project_turn(s, s["env"]["origin"])
    original = global_runtime.provider()._transport

    def cancel(request):
        with connection(True) as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'"
                ).fetchone()["n"]
                == 0
            )
        result = s["api"].post(
            f"/api/v1/assistant/jobs/{accepted.json()['id']}/cancel",
            json={},
            headers=s["headers"](actor=1),
        )
        assert result.status_code == 202 and not result.json()["cancellation_terminal"]
        return original.handle_request(request)

    client = DeepSeekClient(api_key="explicit-offline-only", transport=httpx.MockTransport(cancel))
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: client))
    with connection() as c:
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s",
            (UUID(accepted.json()["operation"]["id"]),),
        ).fetchone()["payload"]
    lease = project_engine.acquire(message)
    outcome = project_engine.run_compute(message, lease)
    assert not project_engine.finish(message, lease, outcome)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert (
        result["operation"]["state"] == "cancel_requested"
        and not result["cancellation_terminal"]
        and result["publication"] is None
    )
    assert result["model_call"]["state"] == "succeeded" and len(s["calls"]) == 1
    # The ordinary core reconciler will finalize actual cancellation; this test never sweeps unrelated jobs.
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM assistant_outputs WHERE job_id=%s", (UUID(result["id"]),)
            ).fetchone()["n"]
            == 0
        )


def test_restricted_roles_keep_chat_business_identity_and_result_columns_separate(assistant):
    from psycopg.errors import InsufficientPrivilege

    for worker in (False, True):
        with connection(worker) as c:
            role = c.execute(
                "SELECT r.rolsuper,r.rolbypassrls,r.rolcanlogin FROM pg_roles r WHERE r.rolname=current_user"
            ).fetchone()
            assert not role["rolsuper"] and not role["rolbypassrls"] and role["rolcanlogin"]
            assert not c.execute(
                "SELECT relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owner FROM pg_class WHERE oid='design.assistant_jobs'::regclass"
            ).fetchone()["owner"]
    with connection(True) as c:
        for relation in (
            "commands",
            "bidders",
            "engineering_projects",
            "messages",
            "assistant_global_messages",
            "assistant_publications",
            "assistant_action_links",
        ):
            assert not c.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT') AS allowed",
                ("design." + relation,),
            ).fetchone()["allowed"]
        with pytest.raises(InsufficientPrivilege), c.transaction():
            c.execute("SELECT * FROM assistant_global_messages LIMIT 0")
    with connection() as c:
        assert not c.execute(
            "SELECT has_column_privilege(current_user,'design.assistant_global_runs','fence','UPDATE') AS allowed"
        ).fetchone()["allowed"]
        assert not c.execute(
            "SELECT has_table_privilege(current_user,'design.assistant_outputs','INSERT') AS allowed"
        ).fetchone()["allowed"]
        with pytest.raises(InsufficientPrivilege), c.transaction():
            c.execute("UPDATE assistant_global_runs SET fence=fence+1 WHERE false")


def test_response_object_persist_failure_reports_known_usage_and_never_auto_reissues(
    assistant, monkeypatch
):
    s = assistant
    main, body, accepted, message = global_turn(s)

    def unavailable(*args):
        raise RuntimeError("explicit in-memory object fault, no real storage network")

    monkeypatch.setattr(storage, "put", unavailable)
    assert global_runtime.handle(message)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert result["operation"]["state"] == "failed" and result["model_call"]["state"] == "failed"
    assert (
        result["model_call"]["billing_state"] == "usage_reported" and result["publication"] is None
    )
    assert not global_runtime.handle(message)
    replay = s["api"].post(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/turns",
        json=body,
        headers=s["headers"](tag=main.headers["etag"]),
    )
    assert replay.status_code == 202 and replay.json()["id"] == accepted.json()["id"]
    assert len(s["calls"]) == 1


def test_reported_response_storage_failure_stays_failed_replay_never_reissues(
    assistant, monkeypatch
):
    s = assistant
    main, body, accepted, message = global_turn(s, text="已返回结果但持久化失败")

    def storage_failure(*args):
        raise OSError("explicit-memory-object-failure")

    monkeypatch.setattr(storage, "put", storage_failure)
    assert global_runtime.handle(message)
    result = coordinator.publish(UUID(accepted.json()["id"]))
    assert (
        result["operation"]["state"] == "failed"
        and result["model_call"]["state"] == "failed"
        and result["model_call"]["billing_state"] == "usage_reported"
        and result["publication"] is None
    )
    assert not global_runtime.handle(message)
    replay = s["api"].post(
        f"/api/v1/assistant/global-conversations/{main.json()['id']}/turns",
        json=body,
        headers=s["headers"](tag=main.headers["etag"]),
    )
    assert (
        replay.status_code == 202
        and replay.json()["id"] == accepted.json()["id"]
        and len(s["calls"]) == 1
    )
