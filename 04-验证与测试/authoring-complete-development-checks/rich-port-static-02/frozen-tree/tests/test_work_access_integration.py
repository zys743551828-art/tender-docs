"""07生命周期实际非owner事务守卫，不调用模型或消费者。"""

import os
from uuid import uuid4

import pytest

from packages.domain.runtime import DomainError, connection, enqueue, fetch, require_read_committed
from packages.domain.work_access import begin
from tests.test_project_lifecycle import project as project
from tests.test_work_conversations import work as work

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORK_ACCESS_INTEGRATION") != "1", reason="专用PG显式执行"
)


def test_new_work_rejected_and_prior_ack_replayed_after_termination(project):  # noqa: F811
    _, bids, actors, _ = project
    bid, actor, key = bids[0], actors[0], uuid4().hex
    with connection() as c:
        cmd = begin(c, actor, "synthetic-work", bid, key, {}, None, bid=bid)
        cmd.finish({"accepted_before_close": True})
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (bid,))
    with connection() as c:
        replay = begin(c, actor, "synthetic-work", bid, key, {}, None, bid=bid)
        assert replay.replay["body"] == {"accepted_before_close": True}
    with pytest.raises(DomainError, match="项目已结束") as error, connection() as c:
        begin(c, actor, "synthetic-work", bid, uuid4().hex, {}, None, bid=bid)
    assert error.value.code == "PROJECT_WORK_CLOSED"
    with connection() as c:
        recovery = begin(
            c, actor, "synthetic-cancel", bid, uuid4().hex, {}, None, bid=bid, work=False
        )
        recovery.finish({"cancelled": True})


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_old_snapshot_cannot_make_current_authority_write(isolation):
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
        c.execute("SELECT 1")
        with pytest.raises(DomainError) as error:
            require_read_committed(c)
        assert error.value.code == "CURRENT_AUTHORITY_REQUIRES_READ_COMMITTED"


def test_main_batch_command_uses_same_guard_and_replay(project):  # noqa: F811
    client, bids, _, headers = project
    from uuid import UUID

    from fastapi import Body, Request

    from apps.api.main import create_batch

    @client.app.post("/guard-bids/{bid}/batches")
    def actual_batch(bid: UUID, request: Request, body: dict = Body(...)):
        return create_batch(bid, request, body)

    url = f"/guard-bids/{bids[0]}/batches"
    h = headers()
    first = client.post(url, json={}, headers=h)
    assert first.status_code == 201
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bids[0],))
        c.execute("UPDATE bid_profiles SET phase='closed' WHERE bid_id=%s", (bids[0],))
    replay = client.post(url, json={}, headers=h)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    denied = client.post(url, json={}, headers=headers())
    assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_WORK_CLOSED"


@pytest.mark.parametrize("started", [False, True])
def test_worker_stops_closed_work_and_releases_execution_right(project, monkeypatch, started):  # noqa: F811
    from apps.worker import engine

    _, bids, actors, _ = project
    bid = bids[0]
    with connection() as c:
        cmd = begin(c, actors[0], "synthetic-queue", bid, uuid4().hex, {}, None, bid=bid)
        op = enqueue(c, bid, cmd.id, "parse-materials", {"id": uuid4()})
        cmd.finish(op)
        message = fetch(c, "SELECT payload FROM outbox WHERE operation_id=%s", (op["id"],))[
            "payload"
        ]
    lease = engine.acquire(message) if started else None
    if started:
        assert lease is not None
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (bid,))
    published = []
    monkeypatch.setattr(engine, "publish", lambda *args: published.append(True))
    if started:
        assert engine.finish(message, lease, {"late": "unpublished"})
    else:
        assert engine.acquire(message) is None
    assert published == []
    with connection(True) as c:
        state = fetch(c, "SELECT state FROM operations WHERE id=%s", (op["id"],))
        assert state["state"] == "failed"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM step_receipts WHERE operation_id=%s AND state='running'",
                (op["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_comparison_lane_refuses_paid_handler_before_acquire(monkeypatch):
    from apps.worker import engine

    called = []
    monkeypatch.setattr(engine, "validate", lambda *args: None)
    monkeypatch.setattr(engine, "acquire", lambda *args: called.append(True))
    for handler in ("review/1", "prepare/1", "assistant/1", "authoring-generate/1"):
        with pytest.raises(DomainError) as error:
            engine.handle_comparison({"handler_version": handler})
        assert error.value.code == "COMPARISON_LANE_REJECTED"
    assert called == []
    assert engine.task_topic("comparison/1") == "tender.task.comparison.v1"


def test_each_transport_lane_rejects_other_handler_before_any_execution(monkeypatch):
    from apps.worker import engine

    executed = []
    monkeypatch.setattr(engine, "validate", lambda *args: None)
    monkeypatch.setattr(engine, "handle", lambda message: executed.append(message))
    for lane, allowed in engine.LANE_HANDLERS.items():
        for handler in engine.ORDINARY_HANDLERS:
            message = {"handler_version": handler}
            before = len(executed)
            if handler in allowed:
                engine.handle_lane(lane, message)
                assert len(executed) == before + 1
            else:
                with pytest.raises(DomainError):
                    engine.handle_lane(lane, message)
                assert len(executed) == before


def test_closed_batch_verified_bytes_require_new_explicit_batch_and_preserve_original(project):  # noqa: F811
    from apps.api.retained_upload_routes import router
    from packages.domain import storage
    from packages.domain.runtime import Command, etag, sha, uid
    from tests.test_lifecycle_batch_closure_integration import batch_fixture
    from tests.test_review_integration import pdf_bytes

    client, bids, actors, headers = project
    bid, actor = bids[0], actors[0]
    client.app.include_router(router)
    old_batch, old_upload = batch_fixture(project, upload_state="verified")
    raw = pdf_bytes("Controlled synthetic retained original " + str(old_upload))
    with connection() as c:
        original = fetch(
            c,
            "SELECT u.*,b.object_key FROM uploads u JOIN document_versions v ON v.id=u.verified_version_id JOIN blobs b ON b.id=v.blob_id WHERE u.id=%s",
            (old_upload,),
        )
        assert sha(raw) == original["declared_digest"]
        cmd = Command(c, actor, "synthetic-retained-binding", bid, uuid4().hex, {})
        file, binding = uid(), uid()
        c.execute(
            "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,'body',1)",
            (file, bid),
        )
        c.execute(
            "INSERT INTO upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,reason,command_id,created_by) VALUES(%s,%s,%s,%s,1,'合成原目标',%s,%s)",
            (binding, bid, old_upload, file, cmd.id, actor),
        )
        c.execute(
            "INSERT INTO upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,part_size,part_count,revision,current_binding_id) VALUES(%s,%s,%s,1,'single',%s,1,1,%s)",
            (old_upload, bid, file, len(raw), binding),
        )
        new_batch = uid()
        c.execute(
            "INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,'open',%s)",
            (new_batch, bid, actor),
        )
        cmd.finish({"synthetic": True})
    storage.put(original["object_key"], raw, "application/pdf")
    try:
        base = f"/api/v1/bids/{bid}/upload-batches/{old_batch}"
        state = client.get(base + "/closure-state", headers=headers())
        closed = client.post(
            base + "/close",
            json={"reason": "保留核验字节，显式结束旧批次"},
            headers=headers(tag=state.headers["etag"]),
        )
        assert closed.status_code == 200
        body = {
            "source_upload_id": str(old_upload),
            "target": {"kind": "new_file"},
            "reason": "重开工作后明确选择该保留原件",
        }
        h = headers(tag=etag("upload", old_upload, 1))
        url = f"/api/v1/bids/{bid}/upload-batches/{new_batch}/retained-uploads"
        lifecycle = f"/api/v1/bids/{bid}/lifecycle"
        current = client.get(lifecycle, headers=headers())
        ended = client.post(
            lifecycle,
            json={"action": "terminate", "reason": "明确结束当前工作后不得复用旧意图"},
            headers=headers(tag=current.headers["etag"]),
        )
        assert ended.status_code == 200
        denied = client.post(url, json=body, headers=h)
        assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_WORK_CLOSED"
        reopened = client.post(
            lifecycle,
            json={"action": "reopen", "reason": "明确重开并重新选择保留资料"},
            headers=headers(tag=ended.headers["etag"]),
        )
        assert reopened.status_code == 200
        created = client.post(url, json=body, headers=h)
        assert created.status_code == 201
        assert created.json()["id"] != str(old_upload) and created.json()["state"] == "verified"
        assert created.json()["document_version_id"] != str(original["verified_version_id"])
        replay = client.post(url, json=body, headers=h)
        assert replay.json() == created.json() and replay.headers["Idempotent-Replayed"] == "true"
        with connection() as c:
            old = fetch(c, "SELECT * FROM uploads WHERE id=%s", (old_upload,))
            assert (
                old["batch_id"] == old_batch
                and old["verified_version_id"] == original["verified_version_id"]
            )
            versions = c.execute(
                "SELECT blob_id FROM document_versions WHERE id=ANY(%s::uuid[])",
                ([str(old["verified_version_id"]), created.json()["document_version_id"]],),
            ).fetchall()
            assert len(versions) == 2 and versions[0]["blob_id"] == versions[1]["blob_id"]
            assert (
                fetch(
                    c,
                    "SELECT count(*) AS n FROM upload_batch_closures WHERE batch_id=%s",
                    (old_batch,),
                )["n"]
                == 1
            )
    finally:
        storage.client().delete_object(Bucket=storage.bucket(), Key=original["object_key"])
