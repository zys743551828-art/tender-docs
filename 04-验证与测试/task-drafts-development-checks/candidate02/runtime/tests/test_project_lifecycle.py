"""Actual nonowner PG/ASGI lifecycle boundaries, not whole archive write-enforcement acceptance."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.lifecycle_routes import router
from packages.domain.runtime import DomainError, connection
from packages.lifecycle import service
from packages.lifecycle.contracts import SCHEMAS, extend_openapi
from packages.lifecycle.service import assert_work_writable
from tests.test_work_conversations import work as work

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PROJECT_LIFECYCLE") != "1", reason="Explicit isolated PG opt-in"
)


@pytest.fixture
def project(work):  # noqa: F811 - shared explicit pytest fixture, not a source redefinition
    client, bids, actors, headers = work
    client.app.include_router(router)
    with connection() as c:
        for bid in bids:
            engineering, bidder, round_id = uuid4(), uuid4(), uuid4()
            c.execute(
                "INSERT INTO engineering_projects(id,title) VALUES(%s,'生命周期合成工程')",
                (engineering,),
            )
            c.execute("INSERT INTO bidders(id,name) VALUES(%s,'待核占位主体')", (bidder,))
            c.execute(
                "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
                (round_id, engineering),
            )
            c.execute(
                "INSERT INTO bid_profiles(bid_id,bidder_id,round_id,phase) VALUES(%s,%s,%s,'preparation')",
                (bid, bidder, round_id),
            )
    return client, bids, actors, headers


def test_phase_is_not_a_review_or_submission_and_requires_current_state(project):
    client, bids, _, headers = project
    url = f"/api/v1/bids/{bids[0]}/lifecycle"
    original = client.get(url, headers=headers())
    assert original.status_code == 200
    Draft202012Validator(SCHEMAS["ProjectLifecycleView"], format_checker=FormatChecker()).validate(
        original.json()
    )
    tag = original.headers["etag"]
    assert original.json()["phase"] == "preparation" and original.json()["archived_at"] is None
    assert (
        client.post(
            url, json={"action": "start", "reason": "人工明确开始处理"}, headers=headers()
        ).status_code
        == 428
    )
    assert (
        client.post(
            url,
            json={"action": "submitted", "reason": "不能点阶段造提交"},
            headers=headers(tag=tag),
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"action": {}, "reason": "wrong type"}, headers=headers(tag=tag)
        ).status_code
        == 422
    )
    request = headers(tag=tag)
    opened = client.post(
        url, json={"action": "start", "reason": "人工明确开始处理"}, headers=request
    )
    assert opened.status_code == 200 and opened.json()["phase"] == "working"
    replay = client.post(
        url, json={"action": "start", "reason": "人工明确开始处理"}, headers=request
    )
    assert replay.json() == opened.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert (
        client.post(
            url,
            json={"action": "terminate", "reason": "旧卡不能终止新状态"},
            headers=headers(tag=tag),
        ).status_code
        == 412
    )
    assert (
        client.post(
            url,
            json={"action": "end", "reason": "无实交不能假已完成"},
            headers=headers(tag=opened.headers["etag"]),
        ).status_code
        == 409
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM submission_records WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM review_runs WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )


def test_termination_archive_waits_for_tasks_and_reopen_preserves_history(project):
    client, bids, actors, headers = project
    url = f"/api/v1/bids/{bids[0]}/lifecycle"
    state = client.get(url, headers=headers())
    conv = client.post(
        f"/api/v1/bids/{bids[0]}/conversations/main", json={}, headers=headers()
    ).json()["id"]
    operation = uuid4()
    with connection() as c:
        command = c.execute(
            "SELECT id FROM commands WHERE target_id=%s AND command_kind='ensure_main_conversation'",
            (bids[0],),
        ).fetchone()["id"]
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'synthetic-job',%s,'queued','synthetic/1')",
            (operation, bids[0], command, "b" * 64),
        )
    terminated = client.post(
        url,
        json={"action": "terminate", "reason": "招标取消，合成证据说明"},
        headers=headers(tag=state.headers["etag"]),
    )
    assert terminated.status_code == 200 and terminated.json()["phase"] == "terminated"
    denied = client.post(
        url,
        json={"action": "archive", "reason": "归档并保留任务入口"},
        headers=headers(tag=terminated.headers["etag"]),
    )
    assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_OPERATIONS_IN_FLIGHT"
    assert denied.json()["details"]["tasks_cancelled"] is False
    with connection() as c:
        assert (
            c.execute("SELECT state FROM operations WHERE id=%s", (operation,)).fetchone()["state"]
            == "queued"
        )
        assert (
            c.execute("SELECT state FROM conversations WHERE id=%s", (conv,)).fetchone()["state"]
            == "active"
        )
        # Fixture completes its own operation; this is not a cancellation API test.
        c.execute(
            "UPDATE operations SET state='succeeded',finished_at=clock_timestamp() WHERE id=%s",
            (operation,),
        )
    current = client.get(url, headers=headers())
    archived = client.post(
        url,
        json={"action": "archive", "reason": "后台任务已完成"},
        headers=headers(tag=current.headers["etag"]),
    )
    assert archived.status_code == 200 and archived.json()["archived_at"]
    with connection() as c:
        with pytest.raises(DomainError) as error:
            assert_work_writable(c, bids[0])
        assert error.value.code == "PROJECT_WORK_CLOSED"
        audit_count = c.execute(
            "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='project.lifecycle_changed'",
            (bids[0],),
        ).fetchone()["n"]
        assert audit_count == 2
    reopened = client.post(
        url,
        json={"action": "reopen", "reason": "同一轮次恢复工作，不延长截止时间"},
        headers=headers(tag=archived.headers["etag"]),
    )
    assert (
        reopened.status_code == 200
        and reopened.json()["phase"] == "working"
        and reopened.json()["archived_at"] is None
    )
    with connection() as c:
        assert_work_writable(c, bids[0])
        assert (
            c.execute("SELECT state FROM conversations WHERE id=%s", (conv,)).fetchone()["state"]
            == "active"
        )
        assert (
            c.execute("SELECT state FROM operations WHERE id=%s", (operation,)).fetchone()["state"]
            == "succeeded"
        )


def test_archive_serializes_a_guarded_producer_and_actual_contract(project):
    client, bids, actors, _ = project
    bid = bids[0]
    with connection() as c:
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (bid,))
    ready, release, producer_started = threading.Event(), threading.Event(), threading.Event()
    pid = []

    def archive():
        with connection() as c:
            service.change(c, bid, actors[0], "archive", "并发归档合成机制", None)
            ready.set()
            assert release.wait(10)

    def guarded_producer():
        assert ready.wait(10)
        try:
            with connection() as c:
                pid.append(c.info.backend_pid)
                producer_started.set()
                assert_work_writable(c, bid)
                c.execute("UPDATE bids SET title='不应发生的迟到写入' WHERE id=%s", (bid,))
        except DomainError as error:
            return error.code
        return "unexpected_write"

    with ThreadPoolExecutor(max_workers=2) as pool:
        archived = pool.submit(archive)
        producer = pool.submit(guarded_producer)
        try:
            assert producer_started.wait(5)
            deadline = time.monotonic() + 5
            waiting = False
            while time.monotonic() < deadline:
                with connection() as monitor:
                    state = monitor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid[0],)
                    ).fetchone()
                if state and state["wait_event_type"] == "Lock":
                    waiting = True
                    break
                time.sleep(0.01)
            assert waiting, "Producer must actually wait behind archive's project lock"
        finally:
            release.set()
        archived.result(timeout=10)
        assert producer.result(timeout=10) == "PROJECT_WORK_CLOSED"
    with connection() as c:
        assert (
            c.execute("SELECT title FROM bids WHERE id=%s", (bid,)).fetchone()["title"]
            == "会话合成项目"
        )
    actual = extend_openapi(client.app.openapi())
    path = actual["paths"]["/api/v1/bids/{bid_id}/lifecycle"]
    assert path["get"]["operationId"] == "getProjectLifecycle"
    assert path["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "ProjectLifecycleRequest"
    )
