"""真实受限PG的阶段故障注入；不将合成派发状态当成Kafka交付证明。"""

import os
from uuid import uuid4

import pytest

from apps.worker import engine
from packages.domain.runtime import connection, enqueue, sha, uid

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RECOVERY_INTEGRATION") != "1", reason="需要专用真实PG"
)


def fixture_message(published=True):
    with connection() as c:
        actor, bid, cmd = uid(), uid(), uid()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,%s)", (actor, "合成阶段故障自测"))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,%s,%s)",
            (bid, "合成PG阶段恢复自测", "{}", actor),
        )
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,%s,%s,%s,%s)",
            (cmd, actor, "recovery_probe", bid, uuid4().hex, sha("probe")),
        )
        op = enqueue(c, bid, cmd, "probe-recovery", {"id": uid()})
        if published:
            c.execute("UPDATE outbox SET published_at=now() WHERE operation_id=%s", (op["id"],))
        return c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s", (op["id"],)
        ).fetchone()["payload"]


def snapshot(message):
    with connection(True) as c:
        plan = c.execute(
            "SELECT * FROM operation_steps WHERE operation_id=%s", (message["operation_id"],)
        ).fetchone()
        receipt = c.execute(
            "SELECT * FROM step_receipts WHERE operation_id=%s", (message["operation_id"],)
        ).fetchone()
        outbox = c.execute(
            "SELECT * FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' ORDER BY available_at DESC",
            (message["operation_id"],),
        ).fetchall()
        return plan, receipt, outbox


def terminate_probe(message):
    with connection(True) as c:
        c.execute(
            "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s AND state IN ('queued','running')",
            (message["operation_id"],),
        )
    engine.reconcile(once=True)


def test_expired_owner_cannot_revive_and_new_owner_can_finish(monkeypatch):
    message = fixture_message()
    old = engine.acquire(message)
    assert engine.renew(message, old)
    with connection(True) as c:
        c.execute(
            "UPDATE step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id=%s",
            (message["operation_id"],),
        )
    assert engine.renew(message, old) is False
    engine.reconcile(once=True)
    plan, receipt, events = snapshot(message)
    assert plan["dispatch_generation"] == 2 and receipt["fence"] > old["fence"]
    assert receipt["owner_token"] is None and receipt["lease_until"] is None
    with connection(True) as c:
        direct = c.execute(
            "SELECT design.renew_step(%s,%s,%s,%s,%s) AS renewed",
            (
                message["operation_id"],
                message["step_key"],
                message["input_digest"],
                old["fence"],
                old["owner"],
            ),
        ).fetchone()["renewed"]
    assert direct is False
    assert engine.renew(message, old) is False
    assert engine.finish(message, old, {}) is False
    current = events[0]["payload"]
    lease = engine.acquire(current)
    assert lease is not None
    monkeypatch.setattr(engine, "publish", lambda *_: {"result_ref": None, "probe": "new_owner"})
    assert engine.finish(current, lease, {}) is True
    assert engine.finish(message, old, {}) is False
    with connection(True) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (message["operation_id"],),
            ).fetchone()["n"]
            == 1
        )


def test_future_retry_keeps_generation_and_backoff():
    message = fixture_message()
    lease = engine.acquire(message)
    assert engine.finish(
        message, lease, None, RuntimeError("synthetic transient dependency failure")
    )
    before, _, events = snapshot(message)
    assert before["dispatch_generation"] == 2 and len(events) == 2
    engine.reconcile(once=True)
    after, _, current = snapshot(message)
    assert after["dispatch_generation"] == 2 and len(current) == 2
    assert engine.renew(message, lease) is False and engine.finish(message, lease, {}) is False
    # 即使available_at已到，未发布的同代意图仍由relay负责，不能对账另建代次。
    with connection(True) as c:
        c.execute(
            "UPDATE outbox SET available_at=now()-interval '1 second' WHERE operation_id=%s AND published_at IS NULL",
            (message["operation_id"],),
        )
    engine.reconcile(once=True)
    assert snapshot(message)[0]["dispatch_generation"] == 2
    # 发布后尚未领取保留该代派发宽限，不把旧失败收据当新代失联。
    with connection(True) as c:
        c.execute(
            "UPDATE outbox SET published_at=now() WHERE operation_id=%s AND published_at IS NULL",
            (message["operation_id"],),
        )
    engine.reconcile(once=True)
    assert snapshot(message)[0]["dispatch_generation"] == 2
    terminate_probe(message)


def test_unpublished_initial_intent_is_not_duplicated():
    message = fixture_message(published=False)
    with connection(True) as c:
        c.execute(
            "UPDATE operations SET created_at=now()-interval '5 minutes' WHERE id=%s",
            (message["operation_id"],),
        )
    engine.reconcile(once=True)
    plan, _, events = snapshot(message)
    assert plan["dispatch_generation"] == 1 and len(events) == 1
    terminate_probe(message)


def test_three_lost_attempts_exhaust_without_fourth_execution():
    message = fixture_message()
    lease = engine.acquire(message)
    with connection(True) as c:
        c.execute(
            "UPDATE step_receipts SET attempt_count=3,lease_until=now()-interval '1 second' WHERE operation_id=%s",
            (message["operation_id"],),
        )
    engine.reconcile(once=True)
    assert engine.acquire(message) is None
    plan, receipt, _ = snapshot(message)
    assert (
        receipt["attempt_count"] == 3
        and receipt["state"] == "failed"
        and receipt["owner_token"] is None
    )
    assert engine.renew(message, lease) is False
    with connection(True) as c:
        assert (
            c.execute(
                "SELECT state FROM operations WHERE id=%s", (message["operation_id"],)
            ).fetchone()["state"]
            == "failed"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND topic='tender.task.dead-letter.v1'",
                (message["operation_id"],),
            ).fetchone()["n"]
            == 1
        )
    # 即使没有对账，领取本身也不能授予第四次执行权。
    other = fixture_message()
    engine.acquire(other)
    with connection(True) as c:
        c.execute(
            "UPDATE step_receipts SET attempt_count=3,lease_until=now()-interval '1 second' WHERE operation_id=%s",
            (other["operation_id"],),
        )
    assert engine.acquire(other) is None
    assert snapshot(other)[1]["attempt_count"] == 3
