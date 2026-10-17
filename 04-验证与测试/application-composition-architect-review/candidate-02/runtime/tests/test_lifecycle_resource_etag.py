"""Real limited PG/full API lifecycle CAS; synthetic queued work, no remote execution."""

import os
from uuid import uuid4

import pytest

from packages.domain.runtime import Command, connection, enqueue, fetch
from tests.test_application_composition_integration import headers, lifecycle

pytest_plugins = ["tests.test_application_composition_integration"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_APPLICATION_COMPOSITION") != "1", reason="Explicit isolated nonowner DB"
)


def test_unfinished_batch_changes_lifecycle_tag_and_old_tag_fails_without_losing_archive_guard(
    application,
):
    w = application
    web = w["client"]
    bid = w["bid"]
    path = f"/api/v1/bids/{bid}/lifecycle"
    first = web.get(path)
    batch = web.post(f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers(w))
    assert batch.status_code == 201
    later = web.get(path)
    assert first.json()["unfinished_batches"] == [] and len(later.json()["unfinished_batches"]) == 1
    assert first.headers["etag"] != later.headers["etag"]
    rejected = web.post(
        path,
        json={"action": "terminate", "reason": "过时表示不得改变状态"},
        headers=headers(w, first.headers["etag"]),
    )
    assert rejected.status_code == 412 and rejected.json()["code"] == "REVISION_CONFLICT"
    h = headers(w, later.headers["etag"])
    body = {"action": "terminate", "reason": "当前表示显式终止"}
    terminated = web.post(path, json=body, headers=h)
    assert terminated.status_code == 200
    assert terminated.headers["etag"] == web.get(path).headers["etag"]
    blocked = web.post(
        path,
        json={"action": "archive", "reason": "仍须显式结束批次"},
        headers=headers(w, terminated.headers["etag"]),
    )
    assert (
        blocked.status_code == 409 and blocked.json()["code"] == "PROJECT_UPLOAD_INTENTS_UNFINISHED"
    )
    close_path = f"/api/v1/bids/{bid}/upload-batches/{batch.json()['id']}/closure-state"
    state = web.get(close_path)
    closed = web.post(
        close_path.removesuffix("closure-state") + "close",
        json={"reason": "保留暂存明确结束"},
        headers=headers(w, state.headers["etag"]),
    )
    assert closed.status_code == 200
    updated = web.get(path)
    assert updated.headers["etag"] != terminated.headers["etag"]
    assert (
        web.post(
            path,
            json={"action": "archive", "reason": "旧表示不能归档"},
            headers=headers(w, terminated.headers["etag"]),
        ).status_code
        == 412
    )
    archived = lifecycle(w, "archive")
    assert archived.json()["archived_at"] is not None
    # Replay the real earlier success after representation changed. It keeps the original ACK/tag.
    replay = web.post(path, json=body, headers=h)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert (
        replay.json() == terminated.json() and replay.headers["etag"] == terminated.headers["etag"]
    )
    assert web.get(path).json()["archived_at"] is not None


def test_inflight_operation_and_cancel_state_change_tag_and_current_tag_keeps_archive_blocked(
    application,
):
    w = application
    web = w["client"]
    bid = w["bid"]
    path = f"/api/v1/bids/{bid}/lifecycle"
    before = web.get(path)
    # Explicit synthetic pending intent only; no claim of file/model success and no consumer.
    with connection() as c:
        cmd = Command(c, w["actor"], "synthetic-lifecycle-pending-operation", bid, uuid4().hex, {})
        op = enqueue(c, bid, cmd.id, "verify-upload", {"id": str(uuid4()), "digest": "a" * 64})
        cmd.finish({"synthetic_pending": True})
    queued = web.get(path)
    assert (
        len(queued.json()["active_operations"]) == 1
        and queued.headers["etag"] != before.headers["etag"]
    )
    assert (
        web.post(
            path,
            json={"action": "terminate", "reason": "旧快照"},
            headers=headers(w, before.headers["etag"]),
        ).status_code
        == 412
    )
    terminated = lifecycle(w, "terminate")
    blocked = web.post(
        path,
        json={"action": "archive", "reason": "不能暗取消后台"},
        headers=headers(w, terminated.headers["etag"]),
    )
    assert blocked.status_code == 409 and blocked.json()["code"] == "PROJECT_OPERATIONS_IN_FLIGHT"
    operation = web.get(f"/api/v1/operations/{op['id']}")
    cancelled = web.post(
        f"/api/v1/operations/{op['id']}/cancel",
        json={"reason": "显式请求取消合成未执行任务", "cancel_pending_same_input": False},
        headers=headers(w, operation.headers["etag"]),
    )
    assert cancelled.status_code == 200
    requested = web.get(path)
    assert requested.headers["etag"] != terminated.headers["etag"]
    assert requested.json()["active_operations"][0]["state"] == "cancel_requested"
    stale = web.post(
        path,
        json={"action": "archive", "reason": "取消前的旧快照"},
        headers=headers(w, terminated.headers["etag"]),
    )
    assert stale.status_code == 412
    still = web.post(
        path,
        json={"action": "archive", "reason": "等任务终态再归档"},
        headers=headers(w, requested.headers["etag"]),
    )
    assert still.status_code == 409 and still.json()["code"] == "PROJECT_OPERATIONS_IN_FLIGHT"
    with connection() as c:
        assert (
            fetch(c, "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s", (op["id"],))[
                "n"
            ]
            == 0
        )
