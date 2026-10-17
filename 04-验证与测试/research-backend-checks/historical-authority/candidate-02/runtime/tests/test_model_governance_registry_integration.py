"""Fresh release-extension PG only. All source/model/object data explicitly synthetic."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest

from apps.api.model_registry_routes import router
from packages.domain import storage
from packages.domain.runtime import connection
from packages.model_governance import purpose_profiles
from tests.test_model_governance_integration import (
    client as governance_client,  # noqa: F401 -- fixture dependency
)
from tests.test_model_governance_integration import (
    queued_training,
    research_client,  # noqa: F401 -- fixture dependency
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_RELEASE_INTEGRATION") != "1",
    reason="Requires NEW reviewed complete release-extension DB; never applies DDL",
)


@contextmanager
def purpose(role):
    from psycopg.rows import dict_row

    environment = purpose_profiles.read_profile(role, f".local/model_release01-model_{role}.env")
    key = purpose_profiles.ROLES[role][0]
    with psycopg.connect(environment[key], row_factory=dict_row) as c:
        identity = c.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not identity["rolsuper"] and not identity["rolbypassrls"]
        yield c


@pytest.fixture
def client(governance_client):  # noqa: F811 -- pytest resolves imported fixture by name.
    governance_client[0].app.include_router(router)
    with connection() as c:
        assert c.execute("SELECT to_regnamespace('model_release') IS NOT NULL AS value").fetchone()[
            "value"
        ]
    return governance_client


def test_registry_actual_bytes_shadow_cas_restore_permissions_and_invalidation(client, monkeypatch):
    web, actor, headers, objects = client
    job, _, items = queued_training(client)
    completed = purpose_profiles.launch(
        "train", job.json()["id"], profile=".local/model_release01-model_train.env"
    )
    assert completed.returncode == 0, completed.stdout
    preview = web.get(f"/api/v1/model-registry/training-jobs/{job.json()['id']}/model-preview")
    assert preview.status_code == 200, preview.text
    put = storage.put
    get = storage.get
    io_checks = []

    def inspect_idle():
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                ).fetchone()["n"]
                == 0
            )
        io_checks.append(True)

    def checked_put(*args):
        inspect_idle()
        return put(*args)

    def checked_get(*args):
        inspect_idle()
        return get(*args)

    monkeypatch.setattr(storage, "put", checked_put)
    monkeypatch.setattr(storage, "get", checked_get)
    registration_headers = headers(preview.headers["etag"])
    request = {"training_job_id": job.json()["id"], "reason": "合成软件候选登记"}
    registered = web.post(
        "/api/v1/model-registry/models", json=request, headers=registration_headers
    )
    assert registered.status_code == 201, registered.text
    candidate = registered.json()
    assert candidate["origin"] == "synthetic" and candidate["research_only"]
    assert candidate["formal_eligibility"] == "unavailable"
    count = len(objects)
    replay = web.post("/api/v1/model-registry/models", json=request, headers=registration_headers)
    assert replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true"
    assert len(objects) == count
    with connection() as c:
        stored = c.execute(
            "SELECT b.* FROM model_release.registrations r JOIN model_artifacts m ON m.id=r.model_id JOIN blobs b ON b.id=m.artifact_blob_id WHERE r.model_id=%s",
            (candidate["model_id"],),
        ).fetchone()
    raw = objects[stored["object_key"]]
    objects[stored["object_key"]] += b" "
    bad = web.get("/api/v1/model-registry/models/" + candidate["model_id"])
    assert bad.status_code == 409 and bad.json()["code"] == "ARTIFACT_BYTES"
    objects[stored["object_key"]] = raw
    assert web.get("/api/v1/model-registry/models/" + candidate["model_id"]).status_code == 200
    shadow_path = "/api/v1/model-registry/routes/shadow/" + candidate["route_key"]
    initial = web.get(shadow_path)
    base_revision = initial.json()["revision"]
    if base_revision == 0:
        assert initial.json()["model_id"] is None
    publish_headers = headers(initial.headers["etag"])
    command = {
        "action": "publish",
        "model_id": candidate["model_id"],
        "reason": "合成shadow，不改业务",
    }
    published = web.post(shadow_path + "/changes", json=command, headers=publish_headers)
    assert published.status_code == 200, published.text
    assert published.json()["revision"] == base_revision + 1
    assert published.json()["rollback_model_id"] == initial.json()["model_id"]
    same = web.post(shadow_path + "/changes", json=command, headers=publish_headers)
    assert same.status_code == 200 and same.headers["idempotency-replayed"] == "true"
    conflict = web.post(
        shadow_path + "/changes", json=command, headers=headers(initial.headers["etag"])
    )
    assert conflict.status_code == 412
    suspend = web.post(
        shadow_path + "/changes",
        json={"action": "suspend", "reason": "合成暂停并保留恢复目标"},
        headers=headers(published.headers["etag"]),
    )
    assert suspend.status_code == 200, suspend.text
    assert (
        suspend.json()["model_id"] is None
        and suspend.json()["rollback_model_id"] == candidate["model_id"]
    )
    restore = web.post(
        shadow_path + "/changes",
        json={
            "action": "rollback",
            "model_id": candidate["model_id"],
            "reason": "合成恢复，重新核验来源",
        },
        headers=headers(suspend.headers["etag"]),
    )
    assert restore.status_code == 200 and restore.json()["revision"] == base_revision + 3, (
        restore.text
    )
    assert (
        web.post(shadow_path + "/changes", json=command, headers=publish_headers).status_code == 412
    )
    business_path = "/api/v1/model-registry/routes/business/" + candidate["route_key"]
    business = web.get(business_path)
    unavailable = web.post(
        business_path + "/changes", json=command, headers=headers(business.headers["etag"])
    )
    assert (
        unavailable.status_code == 409
        and unavailable.json()["code"] == "MODEL_AUTHORITY_UNAVAILABLE"
    )
    with connection() as c:
        assert not c.execute(
            "SELECT 1 FROM model_routes WHERE route_key=%s AND model_id IS NOT NULL",
            (candidate["route_key"],),
        ).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO model_release.certifications(id,model_id,acceptance_id,scope_digest,report_digest,protocol_digest) VALUES(%s,%s,%s,%s,%s,%s)",
                (uuid4(), candidate["model_id"], uuid4(), "a" * 64, "b" * 64, "c" * 64),
            )
    for role in ("train", "evaluate"):
        with purpose(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT * FROM model_release.registrations")
        with purpose(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "SELECT model_release.registration_document(%s,%s)",
                    (job.json()["id"], candidate["model_id"]),
                )
    business_now = web.get(business_path)
    null_route = web.post(
        business_path + "/changes",
        json={"action": "suspend", "reason": "合成关闭正式预测，无需伪造accepted对象"},
        headers=headers(business_now.headers["etag"]),
    )
    assert null_route.status_code == 200 and null_route.json()["model_id"] is None, null_route.text
    with connection() as c:
        off = c.execute(
            "SELECT model_id,acceptance_id,revision FROM model_routes WHERE route_key=%s",
            (candidate["route_key"],),
        ).fetchone()
        assert off["model_id"] is None and off["acceptance_id"] is None
        assert off["revision"] == null_route.json()["revision"]
    assert io_checks
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id IN (SELECT id FROM retrieval.source_nodes WHERE document_version_id=%s)",
            (items[0]["body"],),
        )
    historical = web.get("/api/v1/model-registry/models/" + candidate["model_id"])
    assert historical.status_code == 200 and not historical.json()["current"]
    stale_replay = web.post(
        "/api/v1/model-registry/models", json=request, headers=registration_headers
    )
    assert stale_replay.status_code == 409
    latest = web.get(shadow_path)
    rejected = web.post(
        shadow_path + "/changes", json=command, headers=headers(latest.headers["etag"])
    )
    assert rejected.status_code == 409
    assert len(objects) == count


def register_trained(client, job):
    web, _, headers, _ = client
    completed = purpose_profiles.launch(
        "train", job.json()["id"], profile=".local/model_release01-model_train.env"
    )
    assert completed.returncode == 0, completed.stdout
    preview = web.get(f"/api/v1/model-registry/training-jobs/{job.json()['id']}/model-preview")
    assert preview.status_code == 200, preview.text
    result = web.post(
        "/api/v1/model-registry/models",
        json={"training_job_id": job.json()["id"], "reason": "显式合成候选"},
        headers=headers(preview.headers["etag"]),
    )
    assert result.status_code == 201, result.text
    return result.json(), preview.json()["document"]


def test_concurrent_route_cas_and_db_envelope_or_isolation_refusal(client):
    from concurrent.futures import ThreadPoolExecutor

    web, actor, headers, _ = client
    first_job, cohort, _ = queued_training(client)
    first, document = register_trained(client, first_job)
    current = web.get("/api/v1/model-governance/cohorts/" + cohort.json()["id"])
    second_job = web.post(
        "/api/v1/model-governance/training-jobs",
        json={"cohort_id": cohort.json()["id"], "reason": "独立CPU任务，同一合成训练源"},
        headers=headers(current.headers["etag"]),
    )
    assert second_job.status_code == 201
    second, _ = register_trained(client, second_job)
    path = "/api/v1/model-registry/routes/shadow/" + first["route_key"]
    initial = web.get(path)
    base_revision = initial.json()["revision"]
    active = web.post(
        path + "/changes",
        json={"action": "publish", "model_id": first["model_id"], "reason": "合成初版shadow"},
        headers=headers(initial.headers["etag"]),
    )
    assert active.status_code == 200, active.text
    actions = [
        {"action": "publish", "model_id": second["model_id"], "reason": "合成并发升级"},
        {"action": "suspend", "reason": "合成并发暂停"},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                web.post, path + "/changes", json=body, headers=headers(active.headers["etag"])
            )
            for body in actions
        ]
        responses = [future.result(timeout=30) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 412], [
        response.text for response in responses
    ]
    latest = web.get(path)
    assert (
        latest.json()["revision"] == base_revision + 2
        and latest.json()["rollback_model_id"] == first["model_id"]
    )
    rollback = web.post(
        path + "/changes",
        json={"action": "rollback", "model_id": first["model_id"], "reason": "合成回退旧合格候选"},
        headers=headers(latest.headers["etag"]),
    )
    assert rollback.status_code == 200 and rollback.json()["revision"] == base_revision + 3, (
        rollback.text
    )
    assert rollback.json()["model_id"] == first["model_id"]
    altered = document | {"model_id": str(uuid4()), "model": {"script": "never execute"}}
    import json

    with connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "SELECT model_release.register_artifact(%s,%s,%s,%s,%s)",
                (altered["model_id"], first_job.json()["id"], uuid4(), json.dumps(altered), actor),
            )
    with connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "SELECT model_release.change_route(%s,'shadow',%s,0,NULL,'publish',%s,'wrong scope',%s)",
                ("f" * 64, first["model_id"], actor, uuid4()),
            )
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            c.execute(
                "SELECT model_release.change_route(%s,'shadow',%s,%s,NULL,'publish',%s,'wrong isolation',%s)",
                (first["route_key"], first["model_id"], base_revision + 3, actor, uuid4()),
            )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_release.route_events WHERE route_key=%s AND lane='shadow' AND actor_id=%s",
                (first["route_key"], actor),
            ).fetchone()["n"]
            == 3
        )
        assert not c.execute(
            "SELECT 1 FROM model_routes WHERE route_key=%s AND model_id IS NOT NULL",
            (first["route_key"],),
        ).fetchone()


def test_union_scope_waits_for_other_candidate_identity_then_rejects_new_use(client):
    from threading import Event, Thread

    web, _, headers, _ = client
    first_job, _, _ = queued_training(client)
    first, _ = register_trained(client, first_job)
    second_job, _, other_items = queued_training(client)
    second, _ = register_trained(client, second_job)
    path = "/api/v1/model-registry/routes/shadow/" + first["route_key"]
    initial = web.get(path)
    active = web.post(
        path + "/changes",
        json={"action": "publish", "model_id": first["model_id"], "reason": "合成当前候选"},
        headers=headers(initial.headers["etag"]),
    )
    assert active.status_code == 200, active.text
    finished = Event()
    observed = {}

    def change():
        try:
            observed["response"] = web.post(
                path + "/changes",
                json={
                    "action": "publish",
                    "model_id": second["model_id"],
                    "reason": "合成另cohort候选身份并发",
                },
                headers=headers(active.headers["etag"]),
            )
        finally:
            finished.set()

    with connection() as editor:
        pid = editor.execute("SELECT pg_backend_pid() AS value").fetchone()["value"]
        editor.execute("SELECT design.lock_worker_bid(%s)", (other_items[0]["bid"],))
        thread = Thread(target=change, daemon=True)
        thread.start()
        blocked = False
        for _ in range(100):
            with connection() as monitor:
                blocked = monitor.execute(
                    "SELECT EXISTS(SELECT FROM pg_stat_activity process WHERE process.datname=current_database() AND process.pid<>pg_backend_pid() AND process.query LIKE '%%model_release.lock_scope%%' AND %s=ANY(pg_blocking_pids(process.pid))) AS value",
                    (pid,),
                ).fetchone()["value"]
            if blocked or finished.wait(0.05):
                break
        assert blocked and not finished.is_set(), (
            "Must observe actual lock wait on the OTHER candidate bid"
        )
        editor.execute(
            "UPDATE bidders SET name=name || ' 合成有据纠正' WHERE id=%s",
            (other_items[0]["identity"]["bidder_id"],),
        )
    assert finished.wait(10)
    thread.join(timeout=1)
    response = observed["response"]
    assert (
        response.status_code == 409 and response.json()["code"] == "MODEL_AUTHORITY_UNAVAILABLE"
    ), response.text
    latest = web.get(path)
    assert latest.json()["revision"] == active.json()["revision"]
    assert latest.json()["model_id"] == first["model_id"]
    assert web.get("/api/v1/model-registry/models/" + second["model_id"]).json()["current"] is False
