"""实际非owner PG账本＋显式离线模型传输，不进行外部付费。"""

import json
import os
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from apps.worker import engine
from packages.domain import model_calls, storage
from packages.domain.providers.deepseek import DeepSeekClient, ProviderError
from packages.domain.runtime import DomainError, connection, enqueue, enqueue_plan, sha, uid

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_LEDGER_INTEGRATION") != "1", reason="需要独立实际PG机制库"
)
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}


@contextmanager
def executions(number=1):
    ids = []
    with connection() as c:
        actor, bid, command = uid(), uid(), uid()
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (actor, "合成模型账本机制自测")
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{}',%s)",
            (bid, "模型账本机制自测", actor),
        )
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'model_ledger_probe',%s,%s,%s)",
            (command, actor, bid, uuid4().hex, sha("probe")),
        )
        for i in range(number):
            op = enqueue(c, bid, command, "model-ledger-probe", {"id": uid(), "ordinal": i})
            ids.append(op["id"])
    try:
        items = []
        for op in ids:
            with connection(True) as c:
                message = c.execute(
                    "SELECT payload FROM outbox WHERE operation_id=%s", (op,)
                ).fetchone()["payload"]
            items.append((message, engine.acquire(message)))
        yield items
    finally:
        with connection(True) as c:
            c.execute(
                "UPDATE operations SET state='cancelled',cancel_requested_at=now(),finished_at=now() WHERE id=ANY(%s::uuid[]) AND state IN ('queued','running','cancel_requested')",
                (ids,),
            )
            c.execute(
                "UPDATE step_receipts SET state='cancelled',fence=fence+1,lease_until=NULL,owner_token=NULL WHERE operation_id=ANY(%s::uuid[]) AND state='running'",
                (ids,),
            )


def reserve(message, lease, key="one", max_tokens=32):
    return model_calls.reserve(
        message,
        lease,
        logical_key=key,
        request_digest=sha("request"),
        profile_digest=sha("profile"),
        max_tokens=max_tokens,
    )


def test_atomic_reservation_duplicate_identity_and_cancel():
    with executions() as ((message, lease),):
        first, created = reserve(message, lease)
        again, recreated = reserve(message, lease)
        assert created and not recreated and first["id"] == again["id"]
        assert first["research_run_id"] is None and first["reserved_amount"] is None
        with pytest.raises(DomainError) as error:
            model_calls.reserve(
                message,
                lease,
                logical_key="one",
                request_digest=sha("different"),
                profile_digest=sha("profile"),
                max_tokens=32,
            )
        assert error.value.code == "MODEL_CALL_IDENTITY_CONFLICT"
        with connection(True) as c:
            c.execute(
                "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                (message["operation_id"],),
            )
        with pytest.raises(DomainError):
            reserve(message, lease, "after-cancel")
        with connection(True) as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s",
                    (message["operation_id"],),
                ).fetchone()["n"]
                == 1
            )


def test_prepare_and_review_share_command_budget_and_rollback():
    with executions(2) as ((one, lease_one), (two, lease_two)):
        for i in range(10):
            reserve(one if i % 2 else two, lease_one if i % 2 else lease_two, str(i))
        with pytest.raises(DomainError) as error:
            reserve(two, lease_two, "eleventh")
        assert error.value.code == "MODEL_BUDGET_EXHAUSTED"
        with connection(True) as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM model_calls WHERE operation_id IN (%s,%s)",
                    (one["operation_id"], two["operation_id"]),
                ).fetchone()["n"]
                == 10
            )
    with executions(2) as ((one, lease_one), (two, lease_two)):
        for i in range(4):
            reserve(one, lease_one, str(i), 8192)
        with pytest.raises(DomainError):
            reserve(two, lease_two, "token-overflow", 1)


def test_stale_generation_fence_and_expired_lease_cannot_reserve():
    with executions() as ((message, lease),):
        for altered, altered_lease in [
            (message | {"generation": message["generation"] + 1}, lease),
            (message, lease | {"fence": lease["fence"] + 1}),
            (message, lease | {"owner": uid()}),
        ]:
            with pytest.raises(DomainError):
                reserve(altered, altered_lease)
        with connection(True) as c:
            c.execute(
                "UPDATE step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id=%s",
                (message["operation_id"],),
            )
        with pytest.raises(DomainError):
            reserve(message, lease)
        with connection(True) as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s",
                    (message["operation_id"],),
                ).fetchone()["n"]
                == 0
            )


def install_transport(monkeypatch, handler):
    client = DeepSeekClient(
        api_key="synthetic-never-external", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: client))


def test_result_reuse_verifies_sha_and_never_calls_twice(monkeypatch):
    seen = []
    objects: dict[str, bytes] = {}

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "response-" + uuid4().hex,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
            },
        )

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    with executions() as ((message, lease),):
        arguments = {
            "logical_key": "judgment",
            "messages": [{"role": "user", "content": "Return JSON"}],
            "json_schema": SCHEMA,
        }
        value, call = model_calls.generate(message, lease, **arguments)
        again, same_call = model_calls.generate(message, lease, **arguments)
        assert call == same_call and value == again and len(seen) == 1
        assert value["data"] == {"ok": True}
        with connection(True) as c:
            row = c.execute("SELECT * FROM model_calls WHERE id=%s", (call,)).fetchone()
        assert row["state"] == "succeeded" and row["usage"]["actual"]["total_tokens"] == 6
        objects[next(iter(objects))] = b"corrupted"
        with pytest.raises(DomainError) as error:
            model_calls.generate(message, lease, **arguments)
        assert error.value.code == "MODEL_RESULT_CORRUPTED" and len(seen) == 1


def test_unknown_billing_is_persistent_and_does_not_resend(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout("synthetic")

    install_transport(monkeypatch, handler)
    with executions() as ((message, lease),):
        arguments = {
            "logical_key": "judgment",
            "messages": [{"role": "user", "content": "Return JSON"}],
            "json_schema": SCHEMA,
        }
        with pytest.raises(ProviderError):
            model_calls.generate(message, lease, **arguments)
        with pytest.raises(DomainError) as error:
            model_calls.generate(message, lease, **arguments)
        assert error.value.code == "MODEL_CALL_REQUIRES_RECONCILIATION" and len(seen) == 1
        with connection(True) as c:
            row = c.execute(
                "SELECT * FROM model_calls WHERE operation_id=%s", (message["operation_id"],)
            ).fetchone()
        assert row["state"] == "unknown" and row["usage"]["billing_state"] == "unknown"
        assert json.dumps(row["usage"]).find("synthetic") == -1


def test_multi_stage_requires_every_stage_and_atomically_dispatches_next(monkeypatch):
    with executions() as ((base, unused_lease),):
        with connection() as c:
            parent = c.execute(
                "SELECT command_id FROM operations WHERE id=%s", (base["operation_id"],)
            ).fetchone()
            op = enqueue_plan(
                c,
                base["bid_id"],
                parent["command_id"],
                "multi-stage-probe",
                {"id": uid()},
                stages=[
                    {"key": "first", "depends_on": []},
                    {"key": "last", "depends_on": ["first"]},
                ],
                handler_version="document/1",
                topic="tender.task.document.v1",
            )
            message = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s", (op["id"],)
            ).fetchone()["payload"]
        monkeypatch.setattr(engine, "publish", lambda *args: {"result_ref": None})
        lease = engine.acquire(message)
        assert lease and engine.finish(message, lease, {})
        with connection(True) as c:
            assert (
                c.execute("SELECT state FROM operations WHERE id=%s", (op["id"],)).fetchone()[
                    "state"
                ]
                == "running"
            )
            events = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY payload->>'step_key'",
                (op["id"],),
            ).fetchall()
            assert len(events) == 2
            last = events[1]["payload"]
            assert last["step_key"] == "last"
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s", (op["id"],)
                ).fetchone()["n"]
                == 1
            )
        next_lease = engine.acquire(last)
        assert next_lease and engine.finish(last, next_lease, {})
        with connection(True) as c:
            assert (
                c.execute("SELECT state FROM operations WHERE id=%s", (op["id"],)).fetchone()[
                    "state"
                ]
                == "succeeded"
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s", (op["id"],)
                ).fetchone()["n"]
                == 2
            )
