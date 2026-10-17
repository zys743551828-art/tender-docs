"""真实PG的HTTP取消处理器（ASGI）；使用独立PG机制库，不发送Kafka合成任务。"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import password_hash
from apps.api.main import app
from apps.worker import engine
from packages.domain.runtime import Command, connection, enqueue, required, uid, validate

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CANCEL_INTEGRATION") != "1", reason="需要独立真实PG机制库"
)


def member():
    login = "cancel-test-" + uuid4().hex
    password = uuid4().hex
    identity = uid()
    with connection() as c:
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (identity, "合成取消条件自测")
        )
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (identity, login, password_hash(password)),
        )
    return identity, login, password


def pending(caller, actor, headers):
    created = caller.post("/api/v1/bids", json={"title": "合成取消条件项目"}, headers=headers())
    assert created.status_code == 201
    bid = created.json()["id"]
    with connection() as c:
        cmd = Command(c, actor, "probe_cancel", bid, uuid4().hex, {})
        op = enqueue(c, bid, cmd.id, "probe-cancel", {"id": uid()})
        cmd.finish({})
        c.execute("UPDATE outbox SET published_at=now() WHERE operation_id=%s", (op["id"],))
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s", (op["id"],)
        ).fetchone()["payload"]
    return op, message


def test_cancel_requires_latest_etag_and_replays_original_effect():
    actor, login, password = member()
    origin = required("APP_ORIGIN")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        csrf = response.json()["csrf_token"]

        def headers(key=None, tag=None):
            return {
                "Origin": origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key or uuid4().hex,
            } | ({"If-Match": tag} if tag else {})

        op, message = pending(client, actor, headers)
        url = f"/api/v1/operations/{op['id']}"
        body = {"reason": "合成取消恢复自测", "cancel_pending_same_input": False}
        initial = client.get(url)
        assert initial.status_code == 200
        queued_tag = initial.headers["etag"]
        assert queued_tag.startswith('"operation:') and not queued_tag.startswith("W/")
        missing = client.post(url + "/cancel", json=body, headers=headers())
        assert missing.status_code == 428
        assert client.get(url).json()["state"] == "queued"
        lease = engine.acquire(message)
        assert lease is not None
        running = client.get(url)
        running_tag = running.headers["etag"]
        assert running_tag != queued_tag
        assert engine.renew(message, lease)
        assert client.get(url).headers["etag"] == running_tag
        stale = client.post(url + "/cancel", json=body, headers=headers(tag=queued_tag))
        assert stale.status_code == 412
        assert client.get(url).json()["state"] == "running"
        key = uuid4().hex
        cancelled = client.post(url + "/cancel", json=body, headers=headers(key, running_tag))
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancel_requested"
        original_body = cancelled.json()
        original_tag = cancelled.headers["etag"]
        assert original_tag != running_tag
        engine.reconcile(once=True)
        terminal = client.get(url)
        assert terminal.json()["state"] == "cancelled" and terminal.headers["etag"] != original_tag
        replay = client.post(url + "/cancel", json=body, headers=headers(key, running_tag))
        assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json() == original_body and replay.headers["etag"] == original_tag
        different = client.post(
            url + "/cancel", json=body, headers=headers(key, terminal.headers["etag"])
        )
        assert different.status_code == 409
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='operation.cancel_requested'",
                    (op["bid_id"],),
                ).fetchone()["n"]
                == 1
            )
        validate("Operation", original_body)
        validate("Error", missing.json())
        validate("Error", stale.json())


def test_generation_changes_etag_even_if_visible_operation_is_unchanged():
    actor, login, password = member()
    origin = required("APP_ORIGIN")
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        csrf = logged.json()["csrf_token"]

        def headers():
            return {"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex}

        op, message = pending(client, actor, headers)
        engine.acquire(message)
        url = f"/api/v1/operations/{op['id']}"
        first = client.get(url)
        with connection(True) as c:
            c.execute("SELECT design.lock_worker_bid(%s)", (op["bid_id"],))
            c.execute(
                "UPDATE operation_steps SET dispatch_generation=dispatch_generation+1 WHERE operation_id=%s",
                (op["id"],),
            )
        second = client.get(url)
        assert first.json() == second.json() and first.headers["etag"] != second.headers["etag"]
        refused = client.post(
            url + "/cancel",
            json={"reason": "旧代次拒绝", "cancel_pending_same_input": False},
            headers=headers() | {"If-Match": first.headers["etag"]},
        )
        assert refused.status_code == 412
        okay = client.post(
            url + "/cancel",
            json={"reason": "取消合成代次", "cancel_pending_same_input": False},
            headers=headers() | {"If-Match": second.headers["etag"]},
        )
        assert okay.status_code == 200
        engine.reconcile(once=True)
