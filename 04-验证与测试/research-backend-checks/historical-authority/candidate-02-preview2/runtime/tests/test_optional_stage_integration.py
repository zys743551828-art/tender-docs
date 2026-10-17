"""可选阶段失败的真实PG计划恢复，不运行Kafka或收费生成。"""

import os

import pytest
from test_model_ledger_integration import executions

from apps.worker import engine
from packages.domain import model_calls
from packages.domain.runtime import DomainError, connection, enqueue_plan, sha, uid

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OPTIONAL_STAGE_INTEGRATION") != "1", reason="需要专用真实PG"
)


def plan(base):
    with connection() as c:
        command = c.execute(
            "SELECT command_id FROM operations WHERE id=%s", (base["operation_id"],)
        ).fetchone()["command_id"]
        op = enqueue_plan(
            c,
            base["bid_id"],
            command,
            "optional-stage-mechanism",
            {"id": uid()},
            stages=[
                {"key": "optional-one", "required": False, "depends_on": []},
                {"key": "optional-two", "required": False, "depends_on": []},
                {"key": "finalizer", "depends_on": ["optional-one", "optional-two"]},
            ],
            handler_version="document/1",
            topic="tender.task.document.v1",
        )
        messages = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY payload->>'step_key'",
            (op["id"],),
        ).fetchall()
        return op["id"], [row["payload"] for row in messages]


@pytest.mark.parametrize("failure", ["explicit_failure", "lost_attempt_budget"])
def test_optional_failure_keeps_sibling_budget_and_reaches_required_finalizer(monkeypatch, failure):
    monkeypatch.setattr(
        engine, "publish", lambda *_: {"result_ref": None, "mechanism": "synthetic"}
    )
    with executions() as ((base, _),):
        operation, (left, right) = plan(base)
        first, second = engine.acquire(left), engine.acquire(right)
        assert first and second
        calls = []
        for message, lease in [(left, first), (right, second)]:
            call, created = model_calls.reserve(
                message,
                lease,
                logical_key=message["step_key"],
                request_digest=sha(message["step_key"]),
                profile_digest=sha("optional-mechanism"),
                max_tokens=32,
            )
            assert created
            calls.append(call["id"])
        if failure == "explicit_failure":
            assert engine.finish(
                left, first, None, DomainError("SYNTHETIC_DETECTOR_FAILED", "合成不可重试失败", 409)
            )
        else:
            with connection(True) as c:
                c.execute(
                    "UPDATE step_receipts SET attempt_count=3,lease_until=now()-interval '1 second' WHERE operation_id=%s AND step_key=%s",
                    (operation, left["step_key"]),
                )
            assert engine.acquire(left) is None
            assert engine.renew(left, first) is False
            assert engine.finish(left, first, {}) is False
        with connection(True) as c:
            assert (
                c.execute("SELECT state FROM operations WHERE id=%s", (operation,)).fetchone()[
                    "state"
                ]
                == "running"
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND payload->>'step_key'='finalizer'",
                    (operation,),
                ).fetchone()["n"]
                == 0
            )
            assert (
                c.execute("SELECT state FROM model_calls WHERE id=%s", (calls[1],)).fetchone()[
                    "state"
                ]
                == "reserved"
            )
            if failure == "lost_attempt_budget":
                assert (
                    c.execute("SELECT state FROM model_calls WHERE id=%s", (calls[0],)).fetchone()[
                        "state"
                    ]
                    == "unknown"
                )
        assert engine.finish(right, second, {})
        with connection(True) as c:
            final = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s AND payload->>'step_key'='finalizer'",
                (operation,),
            ).fetchone()["payload"]
        final_lease = engine.acquire(final)
        assert final_lease and engine.finish(final, final_lease, {})
        with connection(True) as c:
            assert (
                c.execute("SELECT state FROM operations WHERE id=%s", (operation,)).fetchone()[
                    "state"
                ]
                == "succeeded"
            )
            assert (
                c.execute(
                    "SELECT state FROM step_receipts WHERE operation_id=%s AND step_key=%s",
                    (operation, left["step_key"]),
                ).fetchone()["state"]
                == "failed"
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s", (operation,)
                ).fetchone()["n"]
                == 3
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND topic='tender.task.dead-letter.v1'",
                    (operation,),
                ).fetchone()["n"]
                == 1
            )
