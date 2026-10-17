"""Real isolated nonowner PG + ASGI trigger mechanics, no model/OSS/consumer execution."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from packages.domain.runtime import Command, audit, connection, enqueue, sha
from packages.tasks import coordinator, inputs

pytest_plugins = ["tests.test_task_templates_integration"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TASK_RUNS") != "1", reason="Explicit nonowner PG opt-in; no model/OSS calls"
)


@pytest.fixture(autouse=True)
def reviewed_control_plane_registration(monkeypatch):
    # Explicit mechanism-fixture registration; no shared-main integration or consumers claimed.
    from packages.domain import reviews

    monkeypatch.setattr(reviews, "TASK_INPUT_GUARD", inputs.review_guard, raising=False)


def project(c, bid, actor):
    material, body, tender = uuid4(), uuid4(), uuid4()
    for doc, kind in ((body, "bid_body"), (tender, "source_original")):
        blob = uuid4()
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (blob, sha(str(blob)), "synthetic-tasks/" + str(blob)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'synthetic.pdf',clock_timestamp(),%s,%s)",
            (doc, bid, blob, kind, actor),
        )
    c.execute(
        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
        (material, bid, sha(str(material))),
    )
    for index, (doc, role) in enumerate(((body, "body"), (tender, "tender"))):
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,%s)",
            (material, bid, doc, role, index),
        )
    c.execute(
        "UPDATE bid_profiles SET current_body_id=%s,current_material_id=%s WHERE bid_id=%s",
        (body, material, bid),
    )
    c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
    command = Command(c, actor, "synthetic-parse-fixture", bid, str(uuid4()), {})
    op = enqueue(c, bid, command.id, "parse-materials", {"id": str(material)})
    command.finish(op)
    return material, body, tender, UUID(op["id"])


def run(client, bid, template, headers, rerun=False, key=None):
    return client.post(
        f"/api/v1/bids/{bid}/task-definitions/{template['id']}/runs",
        json={"rerun": rerun},
        headers=headers(tag=f'"task-template:{template["id"]}:{template["revision"]}"', key=key),
    )


def test_manual_denial_lost_ack_reuses_and_parse_success_is_not_review_success(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        _, _, _, op = project(c, bids[0], actors[0])
    template = create(client).json()
    key = str(uuid4())
    first = run(client, bids[0], template, headers, key=key)
    assert first.status_code == 202, first.text
    value = first.json()
    assert value["stage"] == "preparation" and value["state"] == "queued"
    replay = run(client, bids[0], template, headers, key=key)
    assert replay.json()["id"] == value["id"] and replay.headers["Idempotent-Replayed"] == "true"
    other = run(client, bids[0], template, headers)
    assert other.json()["id"] == value["id"] and other.headers["Task-Run-Reused"] == "true"
    with connection() as c:
        c.execute(
            "UPDATE operations SET state='succeeded',finished_at=clock_timestamp() WHERE id=%s",
            (op,),
        )
    observed = client.get(
        f"/api/v1/bids/{bids[0]}/task-runs/{value['id']}", headers=headers()
    ).json()
    assert observed["state"] == "awaiting_review" and observed["final_operation_id"] is None
    assert observed["result"] is None
    assert (
        client.get(f"/api/v1/bids/{bids[1]}/task-runs/{value['id']}", headers=headers()).status_code
        == 404
    )


def test_scoring_preflight_does_not_create_fake_success_or_runs(world):
    client, bids, actors, headers, create = world
    template = create(
        client,
        {"name": "完整评分", "task_kind": "scoring_review", "instructions": "不能用模板绕资格"},
    ).json()
    response = run(client, bids[0], template, headers)
    assert response.status_code == 409 and response.json()["code"] == "TASK_SCORING_NOT_QUALIFIED"
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM task_runs WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 0
        )


def test_registered_impact_is_explicitly_partial_and_notifications_merge_without_poll_writes(world):
    client, bids, actors, headers, create = world
    template = create(
        client,
        {
            "name": "已登记影响",
            "task_kind": "supplement_impact",
            "instructions": "不得自动决定效力",
        },
    ).json()
    response = run(client, bids[0], template, headers)
    assert response.status_code == 202, response.text
    value = response.json()
    assert value["state"] == "succeeded"
    assert (
        value["result"]["semantic_impact_coverage"] == "unknown"
        and not value["formal_decisions_created"]
    )
    for _ in range(3):
        client.get(f"/api/v1/bids/{bids[0]}/task-runs/{value['id']}", headers=headers())
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='task.notification'",
                (bids[0],),
            ).fetchone()["n"]
            == 0
        )
        assert coordinator.reconcile_notifications(c, bid=bids[0])["created"] == 1
        assert coordinator.reconcile_notifications(c, bid=bids[0])["created"] == 0
    notified = client.get(f"/api/v1/bids/{bids[0]}/task-notifications", headers=headers()).json()
    assert len(notified["items"]) == 1
    assert (
        client.get(f"/api/v1/bids/{bids[0]}/task-notifications", headers=headers(1)).json()["items"]
        == []
    )


def test_pause_stops_only_new_triggers_not_existing_operations(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    created = create(client)
    template = created.json()
    value = run(client, bids[0], template, headers).json()
    url = f"/api/v1/bids/{bids[0]}/task-definitions/{template['id']}"
    paused = client.post(
        url + "/pause", json={"paused": True}, headers=headers(tag=created.headers["etag"])
    )
    assert paused.status_code == 200
    assert run(client, bids[0], paused.json(), headers).status_code == 409
    with connection() as c:
        assert (
            c.execute(
                "SELECT state FROM operations WHERE id=%s", (value["root_operation_id"],)
            ).fetchone()["state"]
            == "queued"
        )


def test_material_event_dedup_same_input_and_old_event_does_not_run_current(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        material, _, _, _ = project(c, bids[0], actors[0])
    create(
        client,
        {
            "name": "事件影响",
            "task_kind": "supplement_impact",
            "instructions": "候选",
            "trigger": {"mode": "material_updated"},
        },
    ).json()
    events = []
    with connection() as c:
        for _ in range(2):
            audit(c, actors[0], bids[0], "materials.committed", {"snapshot_id": str(material)})
        events = [
            row["id"]
            for row in c.execute(
                "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type='materials.committed' ORDER BY id",
                (bids[0],),
            ).fetchall()
        ]

    def deliver(event):
        with connection() as c:
            return coordinator.material_committed(c, bid=bids[0], event_id=event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicates = list(pool.map(deliver, [events[0], events[0]]))
    assert duplicates[0][0]["run_id"] == duplicates[1][0]["run_id"]
    second = deliver(events[1])
    assert second[0]["run_id"] == duplicates[0][0]["run_id"] and second[0]["reused"]
    with connection() as c:
        replacement = uuid4()
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,2,'{}',%s)",
            (replacement, bids[0], sha(str(replacement))),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s", (replacement, bids[0])
        )
        audit(c, actors[0], bids[0], "materials.committed", {"snapshot_id": str(material)})
        stale = c.execute(
            "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type='materials.committed' ORDER BY created_at DESC,id DESC LIMIT 1",
            (bids[0],),
        ).fetchone()["id"]
    assert deliver(stale)[0]["state"] == "stale"
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM task_runs WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 1
        )


def test_schedule_skips_unbounded_history_and_current_slot_has_one_receipt(world):
    client, bids, actors, headers, create = world
    created = create(
        client,
        {
            "name": "分钟计划",
            "task_kind": "supplement_impact",
            "instructions": "不补发历史",
            "trigger": {"mode": "schedule", "cron": "* * * * *"},
        },
    )
    with connection() as c:
        c.execute(
            "UPDATE schedules SET next_fire_at=clock_timestamp()-interval '400 days' WHERE definition_id=%s",
            (created.json()["id"],),
        )
        missed = coordinator.tick(c, bid=bids[0])
        assert missed[0]["automatic_catchup_runs"] == 0 and missed[0]["missed"]["count"] is None
        assert coordinator.tick(c, bid=bids[0]) == []
        assert (
            c.execute("SELECT count(*) AS n FROM task_runs WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 0
        )
        c.execute(
            "UPDATE schedules SET next_fire_at=clock_timestamp()-interval '5 seconds' WHERE definition_id=%s",
            (created.json()["id"],),
        )

    def tick():
        with connection() as c:
            return coordinator.tick(c, bid=bids[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: tick(), range(2)))
    assert sorted(map(len, responses)) == [0, 1]
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM schedule_receipts r JOIN schedules s ON s.id=r.schedule_id WHERE s.bid_id=%s",
                (bids[0],),
            ).fetchone()["n"]
            == 1
        )
    recovery = client.get(
        f"/api/v1/bids/{bids[0]}/task-schedule-recoveries", headers=headers()
    ).json()
    assert len(recovery["items"]) == 1
    current = run(client, bids[0], created.json(), headers)
    assert current.status_code == 202


def test_unknown_cost_blocks_new_key_and_explicit_rerun_and_changed_template(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    template = create(client).json()
    first = run(client, bids[0], template, headers).json()
    with connection() as c:
        c.execute(
            "INSERT INTO model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state) VALUES(%s,%s,'synthetic-no-provider',1,%s,%s,'unknown')",
            (uuid4(), first["root_operation_id"], "a" * 64, "b" * 64),
        )
    for rerun in (False, True):
        denied = run(client, bids[0], template, headers, rerun=rerun)
        assert (
            denied.status_code == 409
            and denied.json()["code"] == "MODEL_CALL_REQUIRES_RECONCILIATION"
        )
    assert (
        client.get(f"/api/v1/bids/{bids[0]}/task-runs/{first['id']}", headers=headers()).json()[
            "state"
        ]
        == "reconciliation_required"
    )


def test_review_guard_marks_stale_before_auto_review_rebase(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    first = run(client, bids[0], create(client).json(), headers).json()
    with connection() as c:
        intent = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bids[0],)
        ).fetchone()["pending_intent"]
        assert inputs.review_guard(c, bids[0], intent)
        c.execute(
            "UPDATE bids SET material_revision=material_revision+1,settings=%s WHERE id=%s",
            (Jsonb({"auto_review": True}), bids[0]),
        )
        assert not inputs.review_guard(c, bids[0], intent)
        assert intent["state"] == "superseded" and intent["error_code"] == "TASK_INPUT_SUPERSEDED"
        c.execute(
            "UPDATE review_slots SET pending_intent=%s WHERE bid_id=%s", (Jsonb(intent), bids[0])
        )
    old = client.get(f"/api/v1/bids/{bids[0]}/task-runs/{first['id']}", headers=headers()).json()
    assert (
        old["state"] == "superseded" and old["frozen_manifest"]["context"]["material_revision"] == 1
    )


def test_chapter_task_queues_real_existing_authoring_plan_without_applying_or_model(world):
    from packages.authoring.repository import create_draft

    client, bids, actors, headers, create = world
    with connection() as c:
        draft, _ = create_draft(c, bids[0], actors[0], "合成未应用章节")
        section = c.execute("SELECT id FROM sections WHERE draft_id=%s", (draft,)).fetchone()["id"]
        before = c.execute(
            "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (draft,)
        ).fetchone()["n"]
    template = create(
        client,
        {
            "name": "章节候选",
            "task_kind": "chapter_suggestion",
            "section_id": str(section),
            "instructions": "只给有据建议，不应用",
        },
    ).json()
    response = run(client, bids[0], template, headers)
    assert response.status_code == 202, response.text
    value = response.json()
    assert value["state"] == "queued" and value["frozen_manifest"]["assistant_job_id"]
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (draft,)
            ).fetchone()["n"]
            == before
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s",
                (value["root_operation_id"],),
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND topic='tender.task.assistant.v1'",
                (value["root_operation_id"],),
            ).fetchone()["n"]
            == 1
        )
    repeat = run(client, bids[0], template, headers)
    assert repeat.json()["id"] == value["id"]


def test_unknown_fee_cannot_be_bypassed_by_new_template_version(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    created = create(client)
    template = created.json()
    first = run(client, bids[0], template, headers).json()
    with connection() as c:
        c.execute(
            "INSERT INTO model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state) VALUES(%s,%s,'synthetic-no-provider',1,%s,%s,'reserved')",
            (uuid4(), first["root_operation_id"], "a" * 64, "b" * 64),
        )
    changed = client.put(
        f"/api/v1/bids/{bids[0]}/task-definitions/{template['id']}",
        json=template["configuration"] | {"instructions": "换指令不能绕过未知费用"},
        headers=headers(tag=created.headers["etag"]),
    )
    assert changed.status_code == 200
    blocked = run(client, bids[0], changed.json(), headers, rerun=True)
    assert (
        blocked.status_code == 409
        and blocked.json()["code"] == "MODEL_CALL_REQUIRES_RECONCILIATION"
    )


def test_untrusted_event_identity_or_cross_project_event_is_not_accepted(world):
    from packages.domain.runtime import DomainError

    client, bids, actors, headers, create = world
    with connection() as c:
        audit(c, actors[0], bids[0], "not-material-event", {})
        wrong = c.execute(
            "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type='not-material-event'",
            (bids[0],),
        ).fetchone()["id"]
    for bid, event in ((bids[0], wrong), (bids[1], wrong), (bids[0], uuid4())):
        with pytest.raises(DomainError) as error:
            with connection() as c:
                coordinator.material_committed(c, bid=bid, event_id=event)
        assert error.value.code == "NOT_FOUND"


def test_run_write_is_blocked_after_archive_but_old_receipt_and_read_survive(world):
    client, bids, actors, headers, create = world
    template = create(
        client,
        {"name": "归档前影响", "task_kind": "supplement_impact", "instructions": "真实部分检查"},
    ).json()
    key = str(uuid4())
    first = run(client, bids[0], template, headers, key=key)
    with connection() as c:
        c.execute(
            "UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",
            (bids[0],),
        )
    replay = run(client, bids[0], template, headers, key=key)
    assert replay.json()["id"] == first.json()["id"]
    assert run(client, bids[0], template, headers).status_code == 409
    assert (
        client.get(
            f"/api/v1/bids/{bids[0]}/task-runs/{first.json()['id']}", headers=headers()
        ).status_code
        == 200
    )


def test_saving_event_template_does_not_retroactively_run_old_material_event(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        material, _, _, _ = project(c, bids[0], actors[0])
        audit(c, actors[0], bids[0], "materials.committed", {"snapshot_id": str(material)})
        event = c.execute(
            "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type='materials.committed'",
            (bids[0],),
        ).fetchone()["id"]
    create(
        client,
        {
            "name": "不追溯事件",
            "task_kind": "supplement_impact",
            "instructions": "只认保存后事件",
            "trigger": {"mode": "material_updated"},
        },
    )
    with connection() as c:
        received = coordinator.material_committed(c, bid=bids[0], event_id=event)
        assert received[0]["state"] == "ignored_before_activation"
        assert (
            c.execute("SELECT count(*) AS n FROM task_runs WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 0
        )
        assert coordinator.pending_events(c, bid=bids[0]) == []


def test_unknown_preparation_cost_is_detected_after_root_parse_success(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        _, _, _, root = project(c, bids[0], actors[0])
    template = create(client).json()
    first = run(client, bids[0], template, headers).json()
    with connection() as c:
        op = uuid4()
        request = first["frozen_manifest"]["review_request_id"]
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'prepare-review',%s,'queued','prepare/1')",
            (op, bids[0], request, "c" * 64),
        )
        c.execute(
            "INSERT INTO model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state) VALUES(%s,%s,'synthetic-preparation-no-provider',1,%s,%s,'unknown')",
            (uuid4(), op, "a" * 64, "b" * 64),
        )
        c.execute(
            "UPDATE operations SET state='succeeded',finished_at=clock_timestamp() WHERE id=%s",
            (root,),
        )
    view = client.get(f"/api/v1/bids/{bids[0]}/task-runs/{first['id']}", headers=headers()).json()
    assert view["state"] == "reconciliation_required"
    assert run(client, bids[0], template, headers, rerun=True).status_code == 409


@pytest.mark.parametrize("invalid", [None, "wrong_context", "duplicate_event", "missing_context"])
def test_final_review_link_requires_unique_bound_context_not_current_review_guess(world, invalid):
    client, bids, actors, headers, create = world
    with connection() as c:
        material, body, _, _ = project(c, bids[0], actors[0])
        docs = c.execute(
            "SELECT document_version_id FROM material_assignments WHERE snapshot_id=%s", (material,)
        ).fetchall()
        for row in docs:
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
                (uuid4(), bids[0], row["document_version_id"], "a" * 64, "b" * 64),
            )
        rules = uuid4()
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rules, bids[0], material, sha(str(rules))),
        )
        c.execute(
            "UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (rules, bids[0])
        )
    first = run(client, bids[0], create(client).json(), headers).json()
    with connection() as c:
        intent = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bids[0],)
        ).fetchone()["pending_intent"]
        if invalid != "missing_context":
            assert inputs.review_guard(c, bids[0], intent)
        context = first["frozen_manifest"]["context"]
        manifest = {
            "body_version_id": str(body),
            "material_snapshot_id": str(material),
            "material_revision": context["material_revision"]
            + (1 if invalid == "wrong_context" else 0),
            "rule_set_id": str(rules),
            "fact_set_id": None,
            "method_digest": context["method_digest"],
            "content_checks": context["content_checks"],
            "parse_run_ids": [d["parse_id"] for d in context["documents"]],
        }
        op, review, inp, blob = uuid4(), uuid4(), uuid4(), uuid4()
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,0,'application/json')",
            (blob, sha(str(blob)), "synthetic-tasks/" + str(blob)),
        )
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'review',%s,'succeeded','synthetic-task-review-fixture/1',clock_timestamp())",
            (op, bids[0], first["frozen_manifest"]["review_request_id"], sha(str(op))),
        )
        c.execute(
            "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s,clock_timestamp(),%s)",
            (
                inp,
                bids[0],
                material,
                body,
                sha(str(inp)),
                context["method_digest"],
                Jsonb(manifest),
                blob,
                sha(manifest),
                "c" * 64,
            ),
        )
        c.execute(
            "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (review, bids[0], inp, op),
        )
        audit(
            c,
            None,
            bids[0],
            "review.frozen",
            {"review_id": str(review), "request_id": first["frozen_manifest"]["review_request_id"]},
        )
        if invalid == "duplicate_event":
            audit(
                c,
                None,
                bids[0],
                "review.frozen",
                {
                    "review_id": str(review),
                    "request_id": first["frozen_manifest"]["review_request_id"],
                },
            )
    observed = client.get(
        f"/api/v1/bids/{bids[0]}/task-runs/{first['id']}", headers=headers()
    ).json()
    if invalid:
        assert observed["state"] == "unknown" and observed["final_operation_id"] is None
    else:
        assert (
            observed["stage"] == "review"
            and observed["state"] == "succeeded"
            and observed["final_operation_id"] == str(op)
        )
        assert observed["root_operation_id"] != observed["final_operation_id"]


def test_equivalent_inflight_review_is_reused_across_template_revision(world):
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    created = create(client)
    template = created.json()
    first = run(client, bids[0], template, headers).json()
    changed = client.put(
        f"/api/v1/bids/{bids[0]}/task-definitions/{template['id']}",
        json=template["configuration"] | {"name": "同范围改名"},
        headers=headers(tag=created.headers["etag"]),
    )
    assert changed.status_code == 200
    reused = run(client, bids[0], changed.json(), headers, rerun=True)
    assert (
        reused.status_code == 202
        and reused.json()["id"] == first["id"]
        and reused.headers["Task-Run-Reused"] == "true"
    )
    with connection() as c:
        intent = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bids[0],)
        ).fetchone()["pending_intent"]
        assert intent["request_id"] == first["frozen_manifest"]["review_request_id"]


def test_unwired_review_executor_fails_closed_without_creating_task_run(world, monkeypatch):
    from packages.domain import reviews

    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    template = create(client).json()
    monkeypatch.delattr(reviews, "TASK_INPUT_GUARD", raising=False)
    response = run(client, bids[0], template, headers)
    assert (
        response.status_code == 503 and response.json()["code"] == "TASK_REVIEW_EXECUTOR_NOT_READY"
    )
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM task_runs WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 0
        )
