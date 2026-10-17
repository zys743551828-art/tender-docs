"""Real nonowner DB/ASGI intent boundaries; synthetic rows, no object or paid calls."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError, connection, now
from packages.lifecycle import service
from packages.lifecycle.contracts import SCHEMAS
from tests.test_review_integration import pdf_bytes

pytest_plugins = ["tests.test_project_lifecycle", "tests.test_work_conversations"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIFECYCLE_CLOSURES") != "1", reason="Explicit fresh closure schema opt-in"
)


def batch_fixture(project, state="open", upload_state=None):
    _, bids, actors, _ = project
    batch = uuid4()
    with connection() as c:
        c.execute(
            "INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,%s,%s)",
            (batch, bids[0], state, actors[0]),
        )
        upload = None
        if upload_state:
            upload = uuid4()
            version = None
            retention = None
            declared = "a" * 64
            byte_size = 1
            if upload_state == "verified":
                raw = pdf_bytes("Controlled synthetic retained original")
                declared = hashlib.sha256(raw).hexdigest()
                byte_size = len(raw)
                version, blob = uuid4(), uuid4()
                retention = now() + timedelta(days=1)
                c.execute(
                    "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",
                    (blob, declared, "closure-test-original/" + str(blob), byte_size),
                )
                c.execute(
                    "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'controlled-synthetic.pdf',%s,'bid_body',%s)",
                    (version, bids[0], blob, now(), actors[0]),
                )
            c.execute(
                "INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at,verified_version_id,verified_retain_until) VALUES(%s,%s,%s,'controlled-synthetic.pdf','body',%s,%s,%s,%s,%s,%s,%s)",
                (
                    upload,
                    bids[0],
                    batch,
                    declared,
                    byte_size,
                    "closure-test/" + str(upload),
                    upload_state,
                    now() + timedelta(hours=1),
                    version,
                    retention,
                ),
            )
    return batch, upload


def assert_schema(name, value):
    Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).validate(value)


def test_no_operation_open_batch_blocks_archive_until_explicit_retained_closure(project):
    client, bids, _, headers = project
    batch, _ = batch_fixture(project)
    lifecycle = f"/api/v1/bids/{bids[0]}/lifecycle"
    initial = client.get(lifecycle, headers=headers())
    ended = client.post(
        lifecycle,
        json={"action": "terminate", "reason": "合成取消招标"},
        headers=headers(tag=initial.headers["etag"]),
    )
    assert ended.status_code == 200
    assert ended.json()["active_operations"] == []
    assert ended.json()["archive_blockers"] == ["BATCHES_UNFINISHED"]
    denied = client.post(
        lifecycle,
        json={"action": "archive", "reason": "未结束批次不能被隐藏"},
        headers=headers(tag=ended.headers["etag"]),
    )
    assert (
        denied.status_code == 409 and denied.json()["code"] == "PROJECT_UPLOAD_INTENTS_UNFINISHED"
    )
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    state = client.get(root + "/closure-state", headers=headers())
    assert state.status_code == 200
    assert_schema("BatchClosureView", state.json())
    h = headers(tag=state.headers["etag"])
    closed = client.post(root + "/close", json={"reason": "结束批次并保留暂存资料"}, headers=h)
    assert closed.status_code == 200 and closed.json()["closure"]
    assert closed.json()["batch"]["state"] == "open"  # No fabricated failed/committed state.
    assert_schema("BatchClosureView", closed.json())
    replay = client.post(root + "/close", json={"reason": "结束批次并保留暂存资料"}, headers=h)
    assert replay.json() == closed.json() and replay.headers["Idempotent-Replayed"] == "true"
    current = client.get(lifecycle, headers=headers())
    assert "archive" in current.json()["allowed_actions"]
    archived = client.post(
        lifecycle,
        json={"action": "archive", "reason": "所有意图已明确处理"},
        headers=headers(tag=current.headers["etag"]),
    )
    assert archived.status_code == 200 and archived.json()["archived_at"]
    reopened = client.post(
        lifecycle,
        json={"action": "reopen", "reason": "同轮继续处理"},
        headers=headers(tag=archived.headers["etag"]),
    )
    assert reopened.status_code == 200
    with connection() as c:
        with pytest.raises(DomainError) as failure:
            service.assert_batch_open(c, bids[0], batch)
        assert failure.value.code == "BATCH_EXPLICITLY_CLOSED"


def test_uncommitted_upload_without_operation_is_visible_and_stays_detached(project):
    client, bids, _, headers = project
    batch, upload = batch_fixture(project, upload_state="authorized")
    lifecycle = f"/api/v1/bids/{bids[0]}/lifecycle"
    with connection() as c:
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (bids[0],))
    state = client.get(lifecycle, headers=headers())
    assert_schema("ProjectLifecycleView", state.json())
    assert state.json()["unfinished_uploads"][0]["id"] == str(upload)
    assert state.json()["archive_blockers"] == ["UPLOADS_UNFINISHED", "BATCHES_UNFINISHED"]
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    before = client.get(root + "/closure-state", headers=headers())
    closed = client.post(
        root + "/close",
        json={"reason": "保留合成暂存意图"},
        headers=headers(tag=before.headers["etag"]),
    )
    assert closed.status_code == 200
    with connection() as c:
        assert (
            c.execute("SELECT state FROM uploads WHERE id=%s", (upload,)).fetchone()["state"]
            == "authorized"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM material_snapshots WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )
    state = client.get(lifecycle, headers=headers())
    assert state.json()["unfinished_uploads"] == [] and state.json()["unfinished_batches"] == []


def test_changed_upload_manifest_rejects_old_closure_and_cross_project_receipt(project):
    client, bids, _, headers = project
    batch, _ = batch_fixture(project)
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    original = client.get(root + "/closure-state", headers=headers())
    with connection() as c:
        c.execute("UPDATE upload_batches SET revision=revision+1 WHERE id=%s", (batch,))
    body = {"reason": "旧界面不能结束新意图"}
    h = headers(tag=original.headers["etag"])
    assert client.post(root + "/close", json=body, headers=h).status_code == 412
    current = client.get(root + "/closure-state", headers=headers())
    h = headers(tag=current.headers["etag"])
    assert client.post(root + "/close", json=body, headers=h).status_code == 200
    wrong = f"/api/v1/bids/{bids[1]}/upload-batches/{batch}/close"
    assert client.post(wrong, json=body, headers=h).status_code == 404


def test_verification_in_flight_is_not_cancelled_by_closure(project):
    client, bids, _, headers = project
    batch, upload = batch_fixture(project, upload_state="verifying")
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    original = client.get(root + "/closure-state", headers=headers())
    response = client.post(
        root + "/close",
        json={"reason": "不能暗中取消核验"},
        headers=headers(tag=original.headers["etag"]),
    )
    assert response.status_code == 409 and response.json()["code"] == "BATCH_VERIFICATION_IN_FLIGHT"
    assert not response.json()["details"]["tasks_cancelled"]
    with connection() as c:
        assert (
            c.execute("SELECT state FROM uploads WHERE id=%s", (upload,)).fetchone()["state"]
            == "verifying"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM upload_batch_closures WHERE batch_id=%s", (batch,)
            ).fetchone()["n"]
            == 0
        )


def test_partial_committed_batch_can_close_only_its_leftover_intent(project):
    client, bids, _, headers = project
    batch, upload = batch_fixture(project, state="committed", upload_state="verified")
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    original = client.get(root + "/closure-state", headers=headers())
    response = client.post(
        root + "/close",
        json={"reason": "保留部分接收后未选中的字节"},
        headers=headers(tag=original.headers["etag"]),
    )
    assert response.status_code == 200 and response.json()["batch"]["state"] == "committed"
    with connection() as c:
        assert (
            c.execute("SELECT state FROM uploads WHERE id=%s", (upload,)).fetchone()["state"]
            == "verified"
        )
        assert service.unfinished_intents(c, bids[0]) == ([], [])


def test_worker_role_guard_and_immutable_closure_receipt(project):
    client, bids, _, headers = project
    batch, _ = batch_fixture(project)
    root = f"/api/v1/bids/{bids[0]}/upload-batches/{batch}"
    state = client.get(root + "/closure-state", headers=headers())
    closed = client.post(
        root + "/close",
        json={"reason": "不可变合成收据"},
        headers=headers(tag=state.headers["etag"]),
    )
    assert closed.status_code == 200
    with connection(worker=True) as c:
        service.assert_work_writable(c, bids[0], worker=True)
        assert c.execute(
            "SELECT id FROM upload_batch_closures WHERE batch_id=%s", (batch,)
        ).fetchone()
        with pytest.raises(DomainError) as failure:
            service.assert_batch_open(c, bids[0], batch)
        assert failure.value.code == "BATCH_EXPLICITLY_CLOSED"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:
            c.execute(
                "UPDATE upload_batch_closures SET reason='changed' WHERE batch_id=%s", (batch,)
            )


def test_late_producer_waits_bid_lock_then_rejects_closed_batch(project):
    _, bids, actors, _ = project
    batch, _ = batch_fixture(project)
    entered = Event()

    def producer():
        with connection() as c:
            entered.set()
            service.lock(c, bids[0])
            try:
                service.assert_batch_open(c, bids[0], batch)
            except DomainError as error:
                return error.code
            return "unexpected_write"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection() as c:
            service.lock(c, bids[0])
            command = c.execute(
                "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(gen_random_uuid(),%s,'synthetic-batch-closure',%s,%s,%s) RETURNING id",
                (actors[0], batch, str(uuid4()), "b" * 64),
            ).fetchone()["id"]
            future = pool.submit(producer)
            assert entered.wait(5)
            service.close_batch(c, bids[0], batch, actors[0], "合成竞争边界", command)
        assert future.result(timeout=5) == "BATCH_EXPLICITLY_CLOSED"
