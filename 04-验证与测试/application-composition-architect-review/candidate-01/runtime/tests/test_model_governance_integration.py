"""Fresh reviewed governance SQL only. Real nonowner PG/HTTP; explicitly synthetic 0-cost judgments."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from apps.api.model_governance_routes import router as governance_router
from packages.domain.runtime import connection
from packages.model_governance import purpose_runtime
from packages.research import execution
from packages.research.worker import process as research_process
from tests.test_model_governance_statistics import bundle
from tests.test_research_integration import (
    admission,
    register,
    seed,
    synthetic_native_transport,
    verify,
)
from tests.test_research_integration import (
    client as research_client,  # noqa: F401 -- pytest fixture
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_GOVERNANCE_INTEGRATION") != "1",
    reason="Needs fresh reviewed FR029 SQL and independent API/research/train/evaluate LOGINs; never owner business",
)


@pytest.fixture
def client(research_client):  # noqa: F811 -- pytest resolves the imported fixture by name.
    research_client[0].app.include_router(governance_router)
    return research_client


@contextmanager
def purpose(role):
    key = purpose_runtime.ROLES[role][0]
    with psycopg.connect(os.environ[key], row_factory=dict_row) as c:
        who = c.execute(
            "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not who["rolsuper"] and not who["rolbypassrls"]
        yield c


def complete_research(client, *, related_final=False, current_registration_windows=False):
    from packages.domain.runtime import sha
    from packages.rag.indexing import ensure_parse_sources
    from packages.research.profile import digest

    web, actor, headers, _ = client
    samples, items = [], []
    for year in (2024, 2024, 2024, 2024, 2025):
        item = seed(actor, year=year)
        with connection() as c:
            c.execute(
                "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'official_result',0)",
                (item["material"], item["bid"], item["source"]),
            )
            parse = c.execute(
                "SELECT id FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                (item["source"],),
            ).fetchone()["id"]
            ensure_parse_sources(c, item["bid"], parse, publish_readable=True)
        if current_registration_windows:
            register_historical_review_now(item)
        official, _ = register(web, headers, item)
        verify(web, headers, item, official)
        samples.append(admission(web, headers, item))
        prepared = web.post(
            f"/api/v1/research/reviews/{item['review']}/chapter-packages",
            json={},
            headers=headers(),
        )
        assert prepared.status_code == 201, prepared.text
        items.append(item)
    if related_final:
        from uuid import UUID

        extra = seed(
            actor, year=2025, project_id=UUID(items[-1]["identity"]["project_id"]), round_no=2
        )
        with connection() as c:
            c.execute(
                "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'official_result',0)",
                (extra["material"], extra["bid"], extra["source"]),
            )
            parse = c.execute(
                "SELECT id FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                (extra["source"],),
            ).fetchone()["id"]
            ensure_parse_sources(c, extra["bid"], parse, publish_readable=True)
        official, _ = register(web, headers, extra)
        verify(web, headers, extra, official)
        extra_sample = admission(web, headers, extra)
        grouped = web.get("/api/v1/research/samples/" + extra_sample["id"] + "/grouping")
        marked = web.post(
            "/api/v1/research/samples/" + extra_sample["id"] + "/grouping",
            json={
                "template_key": "explicit-synthetic-template-" + extra_sample["id"],
                "near_duplicate_key": None,
                "reviewed": True,
                "reason": "合成完整组曝光测试：未列入cohort但与final同项目关联",
            },
            headers=headers(grouped.headers["etag"]),
        )
        assert marked.status_code == 200, marked.text
        extra["sample_id"] = extra_sample["id"]
        items.append(extra)
    sample_ids = [s["id"] for s in samples]
    frozen = web.post(
        "/api/v1/research/datasets",
        json={
            "sample_ids": sample_ids,
            "split": dict(
                zip(sample_ids, ["train", "train", "train", "validation", "test"], strict=True)
            ),
            "cutoff": "2025-01-01T00:00:00Z",
        },
        headers=headers(),
    )
    assert frozen.status_code == 201, frozen.text
    frozen = frozen.json()
    preview = web.post(
        "/api/v1/research/previews",
        json={
            "dataset_manifest_blob_id": frozen["dataset_manifest_blob_id"],
            "split_manifest_blob_id": frozen["split_manifest_blob_id"],
            "protocol_digest": sha("explicit-synthetic-FR029"),
            "method_digest": digest(),
            "profile_digest": digest(),
            "sample_ids": sample_ids,
            "repeat_count": 1,
            "concurrency": 1,
            "fee_limit": "20",
            "currency": "USD",
        },
        headers=headers(),
    )
    assert preview.status_code == 201, preview.text
    p = preview.json()
    started = web.post(
        "/api/v1/research/runs",
        json={
            "preview_id": p["id"],
            "manifest_digest": p["manifest_digest"],
            "price_config_digest": p["price_config_digest"],
        },
        headers=headers(),
    )
    assert started.status_code == 202, started.text
    run = started.json()
    requests = []
    for _ in sample_ids:
        message = execution.schedule_one(run["id"])
        assert message is not None
        result = research_process(message, synthetic_native_transport(requests))
        assert result["state"] == "succeeded", result
    assert len(requests) == 15
    return run, samples, items


def frozen_cohort(client, *, related_final=False, current_registration_windows=False):
    web, _, headers, _ = client
    run, samples, items = complete_research(
        client,
        related_final=related_final,
        current_registration_windows=current_registration_windows,
    )
    protocol = web.post(
        "/api/v1/model-governance/training-protocols",
        json={"document": bundle()["protocol"], "reason": "显式合成软件协议，永不授正式资格"},
        headers=headers(),
    )
    if protocol.status_code == 409 and protocol.json().get("code") == "TRAINING_PROTOCOL_EXISTS":
        existing = protocol.json()["details"]["protocol_id"]
        protocol = web.get("/api/v1/model-governance/training-protocols/" + str(existing))
    assert protocol.status_code in {200, 201}, protocol.text
    body = {
        "research_run_id": run["id"],
        "protocol_id": protocol.json()["id"],
        "repeat_index": 1,
        "calibration_group_ids": [samples[2]["source_group_id"]],
        "reason": "显式合成隔离导出软件测试",
    }
    preview = web.post("/api/v1/model-governance/cohort-previews", json=body, headers=headers())
    assert preview.status_code == 200, preview.text
    frozen = web.post(
        "/api/v1/model-governance/cohorts", json=body, headers=headers(preview.headers["etag"])
    )
    assert frozen.status_code == 201, frozen.text
    return frozen, items


def queued_training(client, *, related_final=False):
    web, _, headers, _ = client
    cohort, items = frozen_cohort(client, related_final=related_final)
    job = web.post(
        "/api/v1/model-governance/training-jobs",
        json={"cohort_id": cohort.json()["id"], "reason": "合成CPU训练，不调用模型"},
        headers=headers(cohort.headers["etag"]),
    )
    assert job.status_code == 201, job.text
    return job, cohort, items


def queued_evaluation(client, *, related_final=False):
    web, _, headers, _ = client
    job, cohort, items = queued_training(client, related_final=related_final)
    result = purpose_runtime.launch("train", job.json()["id"])
    assert result.returncode == 0, result.stdout
    trained = web.get("/api/v1/model-governance/jobs/" + job.json()["id"])
    assert trained.json()["state"] == "succeeded", trained.text
    evaluation = web.post(
        "/api/v1/model-governance/evaluations",
        json={"training_job_id": job.json()["id"], "reason": "先冻结最终预测后打开标签"},
        headers=headers(trained.headers["etag"]),
    )
    assert evaluation.status_code == 201, evaluation.text
    return evaluation, cohort, items


def test_real_purpose_child_training_then_committed_exposure_evaluation_remains_synthetic(
    client, monkeypatch
):
    passed_keys = []
    original_run = purpose_runtime.subprocess.run

    def isolated_run(*args, **kwargs):
        keys = set(kwargs["env"])
        assert keys in [
            {"MODEL_TRAIN_DATABASE_DSN", "LANG", "LC_ALL", "TZ"},
            {"MODEL_EVALUATE_DATABASE_DSN", "LANG", "LC_ALL", "TZ"},
        ]
        passed_keys.append(sorted(keys))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(purpose_runtime.subprocess, "run", isolated_run)
    web, _, _, _ = client
    job, cohort, _ = queued_evaluation(client)
    result = purpose_runtime.launch("evaluate", job.json()["id"])
    assert result.returncode == 0, result.stdout
    done = web.get("/api/v1/model-governance/jobs/" + job.json()["id"])
    assert done.status_code == 200 and done.json()["state"] == "succeeded"
    payload = done.json()["result"]["payload"]
    assert payload["research_only"] is True and payload["formal_eligibility"] == "unavailable"
    assert payload["report"]["project_mae"] == 0
    assert payload["report"]["denominator"] == 1
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_governance.exposures WHERE job_id=%s",
                (job.json()["id"],),
            ).fetchone()["n"]
            == 1
        )
        exports = c.execute(
            "SELECT purpose,payload FROM model_governance.exports WHERE cohort_id=%s",
            (cohort.json()["id"],),
        ).fetchall()
        training_export = next(r["payload"] for r in exports if r["purpose"] == "training")
        final_ids = {r["id"] for r in training_export["members"] if r["purpose"] == "final"}
        assert not final_ids & {r["id"] for r in training_export["rows"]}
        assert c.execute("SELECT count(*) AS n FROM model_routes").fetchone()["n"] == 0
    # Succeeded work is not run again; final exposure remains exactly one.
    replay = purpose_runtime.launch("evaluate", job.json()["id"])
    assert replay.returncode == 0 and "not_acquired" in replay.stdout
    assert len(passed_keys) == 3


def test_purpose_permissions_and_exposure_must_commit_before_final_label_read(client):
    job, _, _ = queued_evaluation(client)
    target, token = job.json()["id"], uuid4()
    with purpose("evaluate") as c:
        lease = c.execute(
            "SELECT model_governance.claim_job(%s,%s) AS value", (target, token)
        ).fetchone()["value"]
    with purpose("evaluate") as c:
        c.execute(
            "SELECT model_governance.open_final(%s,%s,%s,%s)",
            (target, token, lease["fence"], uuid4()),
        )
        with pytest.raises(psycopg.Error, match="committed_exposure_required"), c.transaction():
            c.execute(
                "SELECT model_governance.read_final(%s,%s,%s)", (target, token, lease["fence"])
            )
    with purpose("evaluate") as c:
        bound = c.execute(
            "SELECT model_governance.read_final(%s,%s,%s) AS value", (target, token, lease["fence"])
        ).fetchone()["value"]
        assert bound["labels"]["rows"][0]["target"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("SELECT * FROM model_governance.exports")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("SELECT * FROM design.outcomes")
    with purpose("train") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(
                "SELECT model_governance.read_final(%s,%s,%s)", (target, token, lease["fence"])
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(
                "INSERT INTO design.model_routes(route_key,revision) VALUES('not-authorized',1)"
            )


@pytest.mark.parametrize(
    "change", ["latest_parse", "same_uuid_name", "candidate_source", "withdrawn_source"]
)
def test_fresh_source_or_identity_change_blocks_cpu_claim_without_result(client, change):
    from uuid import UUID

    from packages.domain.runtime import sha, uid
    from packages.rag.indexing import original_source_id

    web, _, _, _ = client
    job, cohort, items = queued_training(client)
    item = items[0]
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(91143,1)")
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        if change == "latest_parse":
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
                (
                    uid(),
                    item["bid"],
                    item["body"],
                    sha("new-explicit-synthetic-parser"),
                    sha("new-config"),
                ),
            )
        elif change == "same_uuid_name":
            c.execute(
                "UPDATE bidders SET name='合成同ID更名' WHERE id=(SELECT bidder_id FROM bid_profiles WHERE bid_id=%s)",
                (item["bid"],),
            )
        else:
            c.execute(
                "UPDATE retrieval.source_heads SET status=%s,revision=revision+1 WHERE source_id=%s",
                (
                    "candidate" if change == "candidate_source" else "withdrawn",
                    original_source_id(UUID(item["body"])),
                ),
            )
    current = web.get("/api/v1/model-governance/cohorts/" + cohort.json()["id"])
    assert current.status_code == 200 and current.json()["current"] is False
    with purpose("train") as c:
        with pytest.raises(psycopg.Error, match="cohort_stale"):
            c.execute("SELECT model_governance.claim_job(%s,%s)", (job.json()["id"], uuid4()))
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_governance.results WHERE job_id=%s",
                (job.json()["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_second_manifest_job_cannot_reopen_same_final_source_closure(client):
    web, _, headers, _ = client
    first, _, _ = queued_evaluation(client)
    completed = purpose_runtime.launch("evaluate", first.json()["id"])
    assert completed.returncode == 0, completed.stdout
    with connection() as c:
        training_id = c.execute(
            "SELECT training_job_id FROM model_governance.evaluations WHERE job_id=%s",
            (first.json()["id"],),
        ).fetchone()["training_job_id"]
    trained = web.get("/api/v1/model-governance/jobs/" + str(training_id))
    second = web.post(
        "/api/v1/model-governance/evaluations",
        json={"training_job_id": str(training_id), "reason": "合成重复曝光反例，不是新的独立测试"},
        headers=headers(trained.headers["etag"]),
    )
    assert second.status_code == 201, second.text
    token = uuid4()
    with purpose("evaluate") as c:
        lease = c.execute(
            "SELECT model_governance.claim_job(%s,%s) AS value", (second.json()["id"], token)
        ).fetchone()["value"]
    with purpose("evaluate") as c:
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "SELECT model_governance.open_final(%s,%s,%s,%s)",
                (second.json()["id"], token, lease["fence"], uuid4()),
            )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_governance.exposures WHERE job_id=%s",
                (first.json()["id"],),
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_governance.exposures WHERE job_id=%s",
                (second.json()["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_generation_changed_while_waiting_for_graph_lock_rejects_old_renew_and_finish(client):
    from threading import Event, Thread

    from psycopg.types.json import Jsonb

    job, _, _ = queued_training(client)
    target, token = job.json()["id"], uuid4()
    with purpose("train") as c:
        lease = c.execute(
            "SELECT model_governance.claim_job(%s,%s) AS value", (target, token)
        ).fetchone()["value"]
    ready, finished, observed = Event(), Event(), {}

    def blocked_renew():
        try:
            with purpose("train") as c:
                observed["pid"] = c.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                ready.set()
                c.execute(
                    "SELECT model_governance.renew_job(%s,%s,%s)", (target, token, lease["fence"])
                )
            observed["accepted"] = True
        except psycopg.Error as error:
            observed["code"] = error.sqlstate
            observed["lease_invalid"] = "lease_invalid" in str(error)
        finally:
            finished.set()

    with connection() as holder:
        holder.execute("SELECT pg_advisory_xact_lock(90127,1)")
        thread = Thread(target=blocked_renew, daemon=True)
        thread.start()
        assert ready.wait(3)
        blocked = False
        for _ in range(50):
            with connection() as monitor:
                blocked = monitor.execute(
                    "SELECT EXISTS(SELECT FROM pg_locks WHERE pid=%s AND locktype='advisory' AND classid=90127 AND objid=1 AND NOT granted) AS value",
                    (observed["pid"],),
                ).fetchone()["value"]
            if blocked:
                break
            finished.wait(0.05)
        assert blocked and not finished.is_set()
        holder.execute(
            "UPDATE model_governance.jobs SET generation=generation+1 WHERE id=%s", (target,)
        )
    assert finished.wait(5)
    thread.join(timeout=1)
    assert observed.get("lease_invalid") is True and "accepted" not in observed
    with purpose("train") as c:
        with pytest.raises(psycopg.Error, match="lease_invalid"):
            c.execute(
                "SELECT model_governance.finish_job(%s,%s,%s,%s,%s)",
                (
                    target,
                    token,
                    lease["fence"],
                    "0" * 64,
                    Jsonb(
                        {
                            "schema": "frozen-training-result/1",
                            "origin": "synthetic",
                            "research_only": True,
                            "formal_eligibility": "unavailable",
                        }
                    ),
                ),
            )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_governance.results WHERE job_id=%s", (target,)
            ).fetchone()["n"]
            == 0
        )


def test_exposure_expands_unselected_group_members_and_history_registered_now_is_eligible(client):
    job, cohort, items = queued_evaluation(client, related_final=True)
    extra = items[-1]
    token = uuid4()
    with purpose("evaluate") as c:
        lease = c.execute(
            "SELECT model_governance.claim_job(%s,%s) AS value", (job.json()["id"], token)
        ).fetchone()["value"]
        assert (
            c.execute(
                "SELECT model_governance.renew_job(%s,%s,%s) AS value",
                (job.json()["id"], token, lease["fence"]),
            ).fetchone()["value"]
            is True
        )
    with purpose("evaluate") as c:
        exposure = c.execute(
            "SELECT model_governance.open_final(%s,%s,%s,%s) AS value",
            (job.json()["id"], token, lease["fence"], uuid4()),
        ).fetchone()["value"]
    with connection() as c:
        assert not c.execute(
            "SELECT EXISTS(SELECT FROM model_governance.members WHERE cohort_id=%s AND sample_id=%s) AS value",
            (cohort.json()["id"], extra["sample_id"]),
        ).fetchone()["value"]
        closure = c.execute(
            "SELECT kind,identity FROM model_governance.exposure_members WHERE exposure_id=%s",
            (exposure,),
        ).fetchall()
        assert {"kind": "sample", "identity": extra["sample_id"]} in closure
        assert {
            "kind": "template",
            "identity": "explicit-synthetic-template-" + extra["sample_id"],
        } in closure
        body_digest = c.execute(
            "SELECT b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s",
            (extra["body"],),
        ).fetchone()["digest"]
        assert {"kind": "body", "identity": body_digest} in closure
        # Registered now, proven known before historical submission: not a migrated source.
        assert (
            c.execute(
                "SELECT d.created_at>s.submitted_at AS value FROM submission_records s JOIN document_versions d ON d.id=s.body_version_id WHERE s.id=%s",
                (items[0]["submission"],),
            ).fetchone()["value"]
            is True
        )
        assert (
            c.execute(
                "SELECT model_governance.current_cohort(%s) AS value", (cohort.json()["id"],)
            ).fetchone()["value"]
            is True
        )


def test_repeatable_read_purpose_qualification_fails_closed(client):
    job, _, _ = queued_training(client)
    with psycopg.connect(os.environ["MODEL_TRAIN_DATABASE_DSN"]) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with pytest.raises(psycopg.Error, match="cohort_stale"):
            c.execute("SELECT model_governance.claim_job(%s,%s)", (job.json()["id"], uuid4()))


def test_remote_reads_and_real_purpose_cpu_begin_without_idle_transactions(client, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from packages.domain import storage

    reads = []
    previous_get = storage.get

    def checked_get(key):
        with connection() as c:
            idle = c.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'"
            ).fetchone()["n"]
        reads.append(idle)
        assert idle == 0, "Remote manifest read held another API transaction"
        return previous_get(key)

    monkeypatch.setattr(storage, "get", checked_get)
    job, _, _ = queued_training(client)
    assert reads and all(value == 0 for value in reads)
    # Explicit test bootstrap injects only a read-only probe at the real CPU entry;
    # production launch never accepts code or an alternate module from requests.
    code = """import sys,json
sys.path.insert(0,sys.argv[1])
from uuid import UUID
from packages.model_governance import purpose_runtime as runtime
original=runtime.training.run
probes=[]
def checked_run(bundle):
    with runtime.connection('train') as c:
        count=c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchone()['n']
    probes.append(count)
    assert count==0
    return original(bundle)
runtime.training.run=checked_run
result=runtime.process('train',UUID(sys.argv[2]))
print(json.dumps({'state':result['state'],'cpu_idle_transaction_counts':probes}))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            str(Path(__file__).resolve().parents[1]),
            job.json()["id"],
        ],
        env=purpose_runtime.child_environment("train", os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert child.returncode == 0, child.stdout
    assert json.loads(child.stdout) == {"state": "succeeded", "cpu_idle_transaction_counts": [0]}


def test_even_complete_matching_frozen_heads_cannot_authorize_future_known_prebid_ancestor(client):
    from datetime import timedelta
    from uuid import UUID

    from packages.domain.runtime import uid
    from packages.model_governance import repository
    from packages.rag.indexing import original_source_id

    _, cohort, items = queued_training(client)
    item = items[0]
    new_cohort, future = uid(), uid()
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(91143,1)")
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        source = c.execute(
            "SELECT n.*,s.submitted_at FROM retrieval.source_nodes n JOIN submission_records s ON s.body_version_id=n.document_version_id WHERE n.id=%s AND s.id=%s",
            (original_source_id(UUID(item["body"])), item["submission"]),
        ).fetchone()
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'document','prebid_content',%s,%s)",
            (future, item["body"], source["submitted_at"] + timedelta(days=1), source["digest"]),
        )
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'published',%s)",
            (future, source["known_at"]),
        )
        c.execute(
            "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
            (original_source_id(UUID(item["body"])), future),
        )
        old = c.execute(
            "SELECT * FROM model_governance.cohorts WHERE id=%s", (cohort.json()["id"],)
        ).fetchone()
        repository._insert(c, "cohorts", {**old, "id": new_cohort}, set())
        for table in (
            "members",
            "source_bindings",
            "chapter_bindings",
            "parse_bindings",
            "evidence_bindings",
        ):
            from psycopg import sql

            rows = c.execute(
                sql.SQL("SELECT * FROM model_governance.{} WHERE cohort_id=%s").format(
                    sql.Identifier(table)
                ),
                (cohort.json()["id"],),
            ).fetchall()
            for row in rows:
                repository._insert(
                    c,
                    table,
                    {**row, "cohort_id": new_cohort},
                    {"identity_snapshot"} if table == "members" else set(),
                )
        member = c.execute(
            "SELECT sample_id FROM model_governance.members WHERE cohort_id=%s AND bid_id=%s",
            (new_cohort, item["bid"]),
        ).fetchone()["sample_id"]
        selected = c.execute(
            "SELECT DISTINCT source_id FROM model_governance.source_bindings WHERE cohort_id=%s AND sample_id=%s AND EXISTS(SELECT FROM retrieval.source_ancestors a WHERE a.source_id=model_governance.source_bindings.source_id AND a.ancestor_id=%s)",
            (new_cohort, member, future),
        ).fetchall()
        for row in selected:
            c.execute(
                "INSERT INTO model_governance.source_bindings(cohort_id,sample_id,source_id,ancestor_id,revision,digest,policy_class,known_at,valid_from) VALUES(%s,%s,%s,%s,1,%s,'prebid_content',%s,%s)",
                (
                    new_cohort,
                    member,
                    row["source_id"],
                    future,
                    source["digest"],
                    source["submitted_at"] + timedelta(days=1),
                    source["known_at"],
                ),
            )
        assert selected
        # Matching frozen heads and complete ancestor IDs still fail the typed knowledge-time gate.
        assert (
            c.execute(
                "SELECT model_governance.current_cohort(%s) AS value", (new_cohort,)
            ).fetchone()["value"]
            is False
        )
        assert (
            c.execute(
                "SELECT h.status='published' AND n.known_at>m.submitted_at AS value FROM retrieval.source_nodes n JOIN retrieval.source_heads h ON h.source_id=n.id CROSS JOIN model_governance.members m WHERE n.id=%s AND m.cohort_id=%s AND m.sample_id=%s",
                (future, new_cohort, member),
            ).fetchone()["value"]
            is True
        )


def register_historical_review_now(item):
    """Explicit offline synthetic new review/record, preserving historical known/submitted facts."""
    from psycopg import sql

    from packages.domain.runtime import sha, uid

    def insert(c, table, row):
        c.execute(
            sql.SQL("INSERT INTO design.{} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, row)),
                sql.SQL(",").join(sql.Placeholder() for _ in row),
            ),
            [Jsonb(value) if isinstance(value, (dict, list)) else value for value in row.values()],
        )

    from psycopg.types.json import Jsonb

    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads h SET valid_from=clock_timestamp(),revision=revision+1 FROM retrieval.source_nodes n WHERE n.id=h.source_id AND n.document_version_id=ANY(%s::uuid[])",
            ([item["body"], item["tender"], item["source"]],),
        )
        moment = c.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
        old_review = c.execute(
            "SELECT * FROM review_runs WHERE id=%s", (item["review"],)
        ).fetchone()
        old_input = c.execute(
            "SELECT * FROM review_inputs WHERE id=%s", (old_review["input_id"],)
        ).fetchone()
        new_input, new_review, new_operation, new_command, new_submission = [
            uid() for _ in range(5)
        ]
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) SELECT %s,created_by,'synthetic-historical-review-now',id,%s,%s FROM bids WHERE id=%s",
            (new_command, uuid4().hex, sha(str(new_input)), item["bid"]),
        )
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'synthetic-review',%s,'succeeded','synthetic/1',clock_timestamp())",
            (new_operation, item["bid"], new_command, sha(str(new_review))),
        )
        insert(
            c,
            "review_inputs",
            {
                **old_input,
                "id": new_input,
                "input_digest": sha(str(new_input)),
                "frozen_at": moment,
                "as_of": moment,
            },
        )
        insert(
            c,
            "review_runs",
            {
                **old_review,
                "id": new_review,
                "input_id": new_input,
                "operation_id": new_operation,
                "created_at": moment,
            },
        )
        old_submission = c.execute(
            "SELECT * FROM submission_records WHERE id=%s", (item["submission"],)
        ).fetchone()
        insert(
            c,
            "submission_records",
            {**old_submission, "id": new_submission, "review_id": new_review, "created_at": moment},
        )
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (new_submission, item["bid"]),
        )
    item["review"], item["submission"] = str(new_review), str(new_submission)


def test_historical_known_before_submission_with_current_registration_head_window_is_supported(
    client,
):
    cohort, items = frozen_cohort(client, current_registration_windows=True)
    assert cohort.status_code == 201 and cohort.json()["current"] is True
    with connection() as c:
        assert (
            c.execute(
                "SELECT bool_and(n.known_at<=s.submitted_at AND h.valid_from>s.submitted_at AND h.valid_from<=i.as_of) AS value FROM submission_records s JOIN review_runs r ON r.id=s.review_id JOIN review_inputs i ON i.id=r.input_id JOIN retrieval.source_nodes n ON n.document_version_id=s.body_version_id JOIN retrieval.source_heads h ON h.source_id=n.id WHERE s.id=ANY(%s::uuid[])",
                ([item["submission"] for item in items],),
            ).fetchone()["value"]
            is True
        )
