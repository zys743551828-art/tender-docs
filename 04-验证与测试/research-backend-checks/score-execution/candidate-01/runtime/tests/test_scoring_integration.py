"""NEW reviewed score SQL only, actual role/HTTP and isolated purpose MockTransport.

No DDL/owner business, paid models, object network, Kafka or general worker consumer.
"""

import json
import os
import subprocess  # nosec B404 -- fixed local child, no shell
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.scoring_routes import router
from packages.domain.reviews import tick
from packages.domain.runtime import connection
from packages.model_governance.scoring_contracts import extend_openapi
from packages.model_governance.scoring_profiles import environment
from tests.test_historical_input_upload_path import (
    test_actual_new_upload_then_historical_input_keeps_real_now_and_no_old_review as make_upload_path,
)
from tests.test_research_integration import (
    client as research_client,  # noqa: F401 -- fixture dependency
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCORE_EXECUTION_INTEGRATION") != "1",
    reason="Requires PM NEW statically approved fresh score DB + purpose-only score profile",
)
ROOT = Path(__file__).resolve().parents[1]


def child(mode, job_id=None, generation=1):
    command = [sys.executable, "-I", str(ROOT / "tests/test_scoring_purpose_helper.py"), mode]
    if job_id:
        command += [str(job_id), str(generation)]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment(os.environ["SCORE_CHECK_PROFILE"]),
        capture_output=True,
        text=True,
        timeout=45,
        check=True,
    )  # nosec B603 -- fixed test child
    return json.loads(result.stdout.splitlines()[-1])


def validate(web, response, schema):
    web.app.openapi_schema = None
    actual = extend_openapi(web.app.openapi())
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + schema, "components": actual["components"]},
        format_checker=FormatChecker(),
    ).validate(response.json())


@pytest.fixture
def project(research_client, monkeypatch):  # noqa: F811 -- imported fixture
    from apps.api.main import app

    previous_routes = list(app.router.routes)
    try:
        make_upload_path(research_client, monkeypatch)
        app.include_router(router)
        _, actor, headers, objects = research_client
        with connection() as c:
            bid = c.execute("SELECT id FROM bids WHERE created_by=%s", (actor,)).fetchone()["id"]
            history = c.execute(
                "SELECT id FROM historical_backtest.inputs WHERE bid_id=%s", (bid,)
            ).fetchone()["id"]
        tick(bid)  # now freeze current review root after explicit Mock rule extraction
        with connection() as c:
            review = c.execute(
                "SELECT current_review_id FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()["current_review_id"]
        assert review is not None
        with TestClient(app) as web:
            web.cookies.update(research_client[0].cookies)
            yield web, headers, str(bid), str(review), str(history), objects
    finally:
        app.router.routes[:] = previous_routes
        app.openapi_schema = None


def create_job(project, branch):
    web, headers, bid, review, history, _ = project
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    assert context.status_code == 200, context.text
    validate(web, context, "ScoreInputContext")
    frozen = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={
            "reason": "显式合成完整三判断",
            "review_id" if branch == "current" else "historical_input_id": review
            if branch == "current"
            else history,
        },
        headers=headers(context.headers["etag"]),
    )
    assert frozen.status_code == 201, frozen.text
    validate(web, frozen, "ScoreInputView")
    assert frozen.json()["technical_maximum"] is None and not frozen.json()["maximum_verified"]
    created = web.post(
        f"/api/v1/score-inputs/{frozen.json()['id']}/jobs",
        json={"budget": "1", "reason": "只执行显式合成Mock用途子进程"},
        headers=headers(frozen.headers["etag"]),
    )
    assert created.status_code == 201, created.text
    validate(web, created, "ScoreJobView")
    return frozen, created


@pytest.mark.parametrize("branch", ["current", "historical"])
def test_actual_complete_three_judgments_owned_worker_and_truthful_unavailable(project, branch):
    web, _, _, _, _, _ = project
    frozen, created = create_job(project, branch)
    output = child("success", created.json()["id"])
    assert output["ok"] and output["mock_calls"] == 3, output
    assert output["formal_eligibility"] == "unavailable" and output["prediction_value"] is None
    assert output["observations"] == ["http_without_open_score_transaction"] * 3
    read = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
    assert read.status_code == 200 and read.json()["state"] == "succeeded", read.text
    validate(web, read, "ScoreJobView")
    assert Decimal(read.json()["reserved_fee"]) == 0 and Decimal(read.json()["estimated_fee"]) > 0
    result = web.get(f"/api/v1/score-jobs/{created.json()['id']}/result")
    assert result.status_code == 200, result.text
    validate(web, result, "ScoreResultView")
    assert len(result.json()["items"]) == 1 and result.json()["items"][0]["valid_replicates"] == 3
    if not result.json()["prediction_hidden"]:
        assert result.json()["features"]["status"] == "unavailable"
        assert result.json()["prediction"]["value"] is None
    # Original ordinary prepare's one explicit Mock model call is a separate ledger.
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.calls WHERE job_id=%s",
                (created.json()["id"],),
            ).fetchone()["n"]
            == 3
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.jobs WHERE input_id=%s",
                (frozen.json()["id"],),
            ).fetchone()["n"]
            == 1
        )


def test_unknown_billing_keeps_reserve_pauses_and_rejects_resume_or_second_nonce(project):
    web, headers, _, _, _, _ = project
    frozen, created = create_job(project, "historical")
    output = child("timeout", created.json()["id"])
    assert not output["ok"] and output["mock_calls"] == 1, output
    read = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
    assert read.json()["state"] == "paused" and Decimal(read.json()["reserved_fee"]) > 0
    assert read.json()["calls"] == [{"state": "unknown", "count": 1}]
    resumed = web.post(
        f"/api/v1/score-jobs/{created.json()['id']}/state",
        json={"action": "resume", "reason": "未知费用不得重做"},
        headers=headers(read.headers["etag"]),
    )
    assert resumed.status_code == 409, resumed.text
    retry = child("success", created.json()["id"])
    assert not retry["ok"] and retry["mock_calls"] == 0
    fresh = web.get(f"/api/v1/score-inputs/{frozen.json()['id']}")
    second = web.post(
        f"/api/v1/score-inputs/{frozen.json()['id']}/jobs",
        json={"budget": "1", "reason": "新nonce不得洗未知调用"},
        headers=headers(fresh.headers["etag"]),
    )
    assert second.status_code == 409


def test_score_purpose_has_no_raw_label_proof_export_or_call_table_read():
    result = child("permissions")
    assert result["ok"] and result["checks"] == ["denied_42501"] * 6 and result["mock_calls"] == 0


def test_equivalent_new_input_success_reuses_actual_calls_and_charges_no_new_fee(project):
    web, _, _, _, _, _ = project
    first_input, first_job = create_job(project, "current")
    first = child("success", first_job.json()["id"])
    assert first["ok"] and first["mock_calls"] == 3, first
    second_input, second_job = create_job(project, "current")
    assert first_input.json()["id"] != second_input.json()["id"]
    assert first_input.json()["execution_key"] == second_input.json()["execution_key"]
    second = child("success", second_job.json()["id"])
    assert second["ok"] and second["mock_calls"] == 0, second
    read = web.get(f"/api/v1/score-jobs/{second_job.json()['id']}")
    validate(web, read, "ScoreJobView")
    assert read.json()["state"] == "succeeded" and read.json()["reused_calls"] == 3
    assert Decimal(read.json()["estimated_fee"]) == Decimal(read.json()["reserved_fee"]) == 0
    with connection() as c:
        first_ids = {
            row["call_id"]
            for row in c.execute(
                "SELECT call_id FROM score_execution.call_bindings WHERE job_id=%s",
                (first_job.json()["id"],),
            ).fetchall()
        }
        second_ids = {
            row["call_id"]
            for row in c.execute(
                "SELECT call_id FROM score_execution.call_bindings WHERE job_id=%s",
                (second_job.json()["id"],),
            ).fetchall()
        }
        assert len(first_ids) == 3 and first_ids == second_ids


def test_equivalent_new_input_cannot_wash_unknown_call_with_new_command(project):
    web, headers, bid, _, history, _ = project
    first_input, first_job = create_job(project, "historical")
    failed = child("timeout", first_job.json()["id"])
    assert not failed["ok"] and failed["mock_calls"] == 1, failed
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    second_input = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"historical_input_id": history, "reason": "新input/newnonce不得洗未知费用"},
        headers=headers(context.headers["etag"]),
    )
    assert second_input.status_code == 201, second_input.text
    assert second_input.json()["id"] != first_input.json()["id"]
    assert second_input.json()["execution_key"] == first_input.json()["execution_key"]
    second = web.post(
        f"/api/v1/score-inputs/{second_input.json()['id']}/jobs",
        json={"budget": "1", "reason": "未知不得重新发送"},
        headers=headers(second_input.headers["etag"]),
    )
    assert second.status_code == 409, second.text
    with connection() as c:
        calls = c.execute(
            "SELECT call.state,count(*) AS n FROM score_execution.calls call JOIN score_execution.jobs job ON job.id=call.job_id JOIN score_execution.inputs i ON i.id=job.input_id WHERE i.bid_id=%s GROUP BY call.state",
            (bid,),
        ).fetchall()
        assert calls == [{"state": "unknown", "n": 1}]


def test_appended_rule_cannot_expand_old_frozen_review_and_rejects_before_put(project):
    web, headers, bid, review, _, objects = project
    with connection() as c:
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,rule_set_id,kind,'explicit synthetic append after root freeze',conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE bid_id=%s AND kind='scoring' LIMIT 1",
            (bid,),
        )
    before = len(objects)
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    response = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"review_id": review, "reason": "旧review不能自动扩入新增必要项"},
        headers=headers(context.headers["etag"]),
    )
    assert response.status_code == 409, response.text
    assert len(objects) == before
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.inputs WHERE bid_id=%s", (bid,)
            ).fetchone()["n"]
            == 0
        )


def test_archived_history_research_does_not_reopen_project_or_enable_current_scoring(project):
    web, headers, bid, review, _, _ = project
    with connection() as c:
        c.execute(
            "UPDATE bid_profiles SET archived_at=clock_timestamp(),phase='closed' WHERE bid_id=%s",
            (bid,),
        )
    history_input, history_job = create_job(project, "historical")
    output = child("success", history_job.json()["id"])
    assert output["ok"] and output["mock_calls"] == 3, output
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    denied = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"review_id": review, "reason": "归档不得开启普通评分"},
        headers=headers(context.headers["etag"]),
    )
    assert denied.status_code == 409, denied.text
    with connection() as c:
        state = c.execute(
            "SELECT archived_at,phase,current_review_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()
        assert state["archived_at"] is not None and state["phase"] == "closed"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.inputs WHERE bid_id=%s AND review_id IS NOT NULL",
                (bid,),
            ).fetchone()["n"]
            == 0
        )
    assert history_input.json()["formal_eligibility"] == "unavailable"


def test_withdrawal_during_mock_http_records_usage_but_cannot_publish_old_result(project):
    web, _, bid, _, _, _ = project
    _, created = create_job(project, "historical")
    command = [
        sys.executable,
        "-I",
        str(ROOT / "tests/test_scoring_purpose_helper.py"),
        "hold",
        created.json()["id"],
        "1",
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment(os.environ["SCORE_CHECK_PROFILE"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )  # nosec B603 -- fixed isolated test child
    try:
        import select

        assert select.select([process.stdout], [], [], 25)[0], "Mock request did not start"
        event = json.loads(process.stdout.readline())
        assert event == {"event": "mock_request_started", "no_idle_score_transaction": True}
        with connection() as c:
            from packages.rag.indexing import original_source_id

            c.execute("SELECT pg_advisory_xact_lock(91143,1)")
            c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
            c.execute("SELECT pg_advisory_xact_lock(90127,1)")
            body = c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()["current_body_id"]
            assert (
                c.execute(
                    "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                    (original_source_id(body),),
                ).rowcount
                == 1
            )
        stdout, stderr = process.communicate(input="1", timeout=25)
        assert process.returncode == 0, stderr
        output = json.loads(stdout.splitlines()[-1])
        assert not output["ok"] and output["mock_calls"] == 1, output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    read = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
    assert read.json()["state"] == "paused", read.text
    assert Decimal(read.json()["estimated_fee"]) > 0 and Decimal(read.json()["reserved_fee"]) == 0
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.results WHERE job_id=%s",
                (created.json()["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_still_published_head_revision_cannot_recapture_an_old_review_as_current(project):
    from packages.rag.indexing import original_source_id

    web, headers, bid, review, _, objects = project
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(91143,1)")
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        body = c.execute(
            "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()["current_body_id"]
        assert (
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,valid_until=clock_timestamp()+interval '365 days' WHERE source_id=%s AND status='published'",
                (original_source_id(body),),
            ).rowcount
            == 1
        )
    before = len(objects)
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    response = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"review_id": review, "reason": "合法效力变更仍不能洗旧root闭包"},
        headers=headers(context.headers["etag"]),
    )
    assert response.status_code == 409, response.text
    assert len(objects) == before
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.inputs WHERE bid_id=%s", (bid,)
            ).fetchone()["n"]
            == 0
        )


def test_expanded_new_review_scope_still_cannot_resend_shared_unknown_chapter(project):
    web, headers, bid, review, _, _ = project
    first_input, first_job = create_job(project, "current")
    unknown = child("timeout", first_job.json()["id"])
    assert not unknown["ok"] and unknown["mock_calls"] == 1, unknown
    # Explicit synthetic SQL fixture: append a second necessary item, then freeze a
    # NEW ordinary review through its real HTTP request/server freeze. Never rewrite
    # the old root or claim the appended item was part of the old review.
    with connection() as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
        c.execute(
            "UPDATE operations SET state='cancelled',finished_at=clock_timestamp(),cancel_requested_at=clock_timestamp() WHERE id=(SELECT operation_id FROM review_runs WHERE id=%s)",
            (review,),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,rule_set_id,kind,'explicit synthetic additional required item',conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE bid_id=%s AND kind='scoring' LIMIT 1",
            (bid,),
        )
        current = c.execute(
            "SELECT p.current_body_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
            (bid,),
        ).fetchone()
    requested = web.post(
        f"/api/v1/bids/{bid}/reviews",
        json={
            "body_version_id": str(current["current_body_id"]),
            "material_revision": current["material_revision"],
            "trigger": "manual",
        },
        headers=headers(web.get(f"/api/v1/bids/{bid}").headers["etag"]),
    )
    assert requested.status_code == 202, requested.text
    tick(bid)
    with connection() as c:
        new_review = c.execute(
            "SELECT current_review_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()["current_review_id"]
    assert str(new_review) != review
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    expanded = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"review_id": str(new_review), "reason": "新实际root含原项与新增必要项"},
        headers=headers(context.headers["etag"]),
    )
    assert expanded.status_code == 201 and expanded.json()["rule_count"] == 2, expanded.text
    assert expanded.json()["execution_key"] != first_input.json()["execution_key"]
    blocked = web.post(
        f"/api/v1/score-inputs/{expanded.json()['id']}/jobs",
        json={"budget": "2", "reason": "扩大的完整集合也不能绕过重合项未知费用"},
        headers=headers(expanded.headers["etag"]),
    )
    assert blocked.status_code == 409, blocked.text
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.calls call JOIN score_execution.jobs j ON j.id=call.job_id JOIN score_execution.inputs i ON i.id=j.input_id WHERE i.bid_id=%s",
                (bid,),
            ).fetchone()["n"]
            == 1
        )


@pytest.mark.parametrize("coverage", ["partial", "conflict"])
def test_actual_partial_or_conflict_root_rejected_by_api_and_db_even_with_complete_known_chapters(
    project, coverage
):
    from uuid import uuid4

    import psycopg
    from psycopg.types.json import Jsonb

    from packages.domain.reviews import freeze_intent
    from packages.domain.runtime import clean, sha
    from packages.model_governance import scoring_repository as repo
    from packages.research import chapters
    from packages.research.batches import price_config
    from packages.research.profile import digest

    web, headers, bid, old_review, _, objects = project
    partial = uuid4()
    # Explicit synthetic incomplete extraction fixture; no updates to immutable rows,
    # no old-root rewrite and no claim of real completeness or model accuracy.
    with connection() as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
        c.execute(
            "UPDATE operations SET state='cancelled',finished_at=clock_timestamp(),cancel_requested_at=clock_timestamp() WHERE id=(SELECT operation_id FROM review_runs WHERE id=%s)",
            (old_review,),
        )
        old = c.execute(
            "SELECT p.current_rule_set_id,p.current_body_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
            (bid,),
        ).fetchone()
        c.execute(
            "INSERT INTO rule_sets SELECT %s,bid_id,material_id,revision+1,digest,%s FROM rule_sets WHERE id=%s",
            (partial, coverage, old["current_rule_set_id"]),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,%s,kind,statement,conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE rule_set_id=%s",
            (partial, old["current_rule_set_id"]),
        )
        c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (partial, bid))
    requested = web.post(
        f"/api/v1/bids/{bid}/reviews",
        json={
            "body_version_id": str(old["current_body_id"]),
            "material_revision": old["material_revision"],
            "trigger": "manual",
        },
        headers=headers(web.get(f"/api/v1/bids/{bid}").headers["etag"]),
    )
    assert requested.status_code == 202, requested.text
    with connection() as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
        c.execute(
            "UPDATE review_slots SET pending_intent=jsonb_set(pending_intent,'{prepared_rule_set_id}',to_jsonb(%s::text)) WHERE bid_id=%s",
            (str(partial), bid),
        )
    freeze_intent(bid)  # actual server root bytes, not a caller-supplied raw root
    with connection() as c:
        review = c.execute(
            "SELECT current_review_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()["current_review_id"]
        metadata = repo.root_metadata(c, bid, review)
    assert str(review) != old_review
    raw = objects[metadata["object_key"]]
    root = json.loads(raw)
    assert root["rule_set"]["coverage"] == coverage
    before = len(objects)
    context = web.get(f"/api/v1/bids/{bid}/score-context")
    refused = web.post(
        f"/api/v1/bids/{bid}/score-inputs",
        json={"review_id": str(review), "reason": "已识别项全读不等于全部要求已识别"},
        headers=headers(context.headers["etag"]),
    )
    assert refused.status_code == 409 and len(objects) == before, refused.text
    # Deliberate API-role SQL attack: even if it bypasses Python's partial guard and
    # constructs all known canonical capsules/clean chapters, DB seal must reject.
    with connection() as c:
        built = chapters.build(c, review_id=review)
    staged = chapters.stage(built)
    identity = uuid4()
    with pytest.raises(psycopg.Error) as failure:
        with connection() as c:
            repo.lock(c, bid)
            published = chapters.publish(
                c,
                actor=metadata["review_manifest"].get("actor_id")
                or c.execute("SELECT created_by FROM bids WHERE id=%s", (bid,)).fetchone()[
                    "created_by"
                ],
                prepared=staged,
                command=None,
            )
            actor = c.execute("SELECT created_by FROM bids WHERE id=%s", (bid,)).fetchone()[
                "created_by"
            ]
            c.execute(
                "INSERT INTO score_execution.inputs(id,bid_id,review_id,identity_snapshot,origin,method_digest,price_config,actor_id,review_root_content,review_root_digest) VALUES(%s,%s,%s,%s,'uncertain',%s,%s,%s,%s,%s)",
                (
                    identity,
                    bid,
                    review,
                    Jsonb(root["identity"]),
                    digest(),
                    Jsonb(price_config()),
                    actor,
                    raw.decode(),
                    sha(raw),
                ),
            )
            by_rule = {str(item["rule_id"]): item for item in staged["candidates"]}
            for package in published["packages"]:
                content = json.dumps(
                    clean(by_rule[package["rule_id"]]["package"]),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                c.execute(
                    "INSERT INTO score_execution.current_chapters VALUES(%s,%s,%s,%s)",
                    (identity, package["id"], content, sha(content.encode())),
                )
            assert not c.execute(
                "SELECT score_execution.current_input(%s) AS value", (identity,)
            ).fetchone()["value"]
            c.execute("SELECT score_execution.seal_input(%s,%s)", (identity, actor))
    assert failure.value.sqlstate == "55000"


def test_same_result_shadow_is_persisted_without_new_model_calls_and_route_change_keeps_history(
    project,
):
    from apps.api.model_governance_routes import router as governance_router
    from apps.api.model_registry_routes import router as registry_router
    from packages.model_governance import purpose_profiles
    from tests.test_model_governance_integration import queued_training

    web, headers, bid, _, _, objects = project
    web.app.include_router(governance_router)
    web.app.include_router(registry_router)
    with connection() as c:
        actor = c.execute("SELECT created_by FROM bids WHERE id=%s", (bid,)).fetchone()[
            "created_by"
        ]
    # Frozen StageA helper uses explicitly synthetic official labels/3-Mock-per-item
    # engineering data. This is NOT historical real-data/model-quality acceptance.
    training, _, _ = queued_training((web, actor, headers, objects))
    training_profile = Path(os.environ["SCORE_CHECK_PROFILE"]).with_name(
        Path(os.environ["SCORE_CHECK_PROFILE"]).name.replace("-model_score.env", "-model_train.env")
    )
    assert set(purpose_profiles.read_profile("train", training_profile)) == {
        "MODEL_TRAIN_DATABASE_DSN",
        "LANG",
        "LC_ALL",
        "TZ",
    }
    fitted = purpose_profiles.launch("train", training.json()["id"], profile=training_profile)
    assert fitted.returncode == 0, fitted.stdout
    preview = web.get(f"/api/v1/model-registry/training-jobs/{training.json()['id']}/model-preview")
    assert preview.status_code == 200, preview.text
    registered = web.post(
        "/api/v1/model-registry/models",
        json={"training_job_id": training.json()["id"], "reason": "显式合成影子持久化机制"},
        headers=headers(preview.headers["etag"]),
    )
    assert registered.status_code == 201, registered.text
    candidate = registered.json()
    assert candidate["research_only"] and candidate["formal_eligibility"] == "unavailable"
    route_path = "/api/v1/model-registry/routes/shadow/" + candidate["route_key"]
    route = web.get(route_path)
    published = web.post(
        route_path + "/changes",
        json={
            "action": "publish",
            "model_id": candidate["model_id"],
            "reason": "只有合成shadow用途",
        },
        headers=headers(route.headers["etag"]),
    )
    assert published.status_code == 200, published.text
    _, score_job = create_job(project, "current")
    result = child("success", score_job.json()["id"])
    assert result["ok"] and result["mock_calls"] == 3, result
    shadow_context_path = f"/api/v1/score-jobs/{score_job.json()['id']}/shadow-context?model_id={candidate['model_id']}"
    context = web.get(shadow_context_path)
    assert context.status_code == 200 and context.json()["additional_model_calls"] == 0, (
        context.text
    )
    validate(web, context, "ScoreShadowContext")
    save_headers = headers(context.headers["etag"])
    request = {
        "model_id": candidate["model_id"],
        "reason": "保存同一判断结果和JSON模型的独立影子快照",
    }
    path = f"/api/v1/score-jobs/{score_job.json()['id']}/shadows"
    with connection() as c:
        calls_before = c.execute("SELECT count(*) AS n FROM score_execution.calls").fetchone()["n"]
    saved = web.post(path, json=request, headers=save_headers)
    assert saved.status_code == 201, saved.text
    validate(web, saved, "ScoreShadowView")
    assert saved.json()["historical_snapshot"] and saved.json()["additional_model_calls"] == 0
    repeated = web.post(path, json=request, headers=save_headers)
    assert repeated.status_code == 201 and repeated.headers["idempotency-replayed"] == "true", (
        repeated.text
    )
    suspended = web.post(
        route_path + "/changes",
        json={"action": "suspend", "reason": "模型路由变化不能重算旧影子"},
        headers=headers(published.headers["etag"]),
    )
    assert suspended.status_code == 200, suspended.text
    refused = web.post(path, json=request, headers=save_headers)
    assert refused.status_code == 409, refused.text
    old = web.get("/api/v1/score-shadows/" + saved.json()["id"])
    assert old.status_code == 200 and old.json() == saved.json()
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM score_execution.calls").fetchone()["n"]
            == calls_before
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.shadows WHERE job_id=%s",
                (score_job.json()["id"],),
            ).fetchone()["n"]
            == 1
        )


def test_cancel_during_mock_http_preserves_late_usage_and_rejects_old_generation_publish(project):
    import select

    web, headers, _, _, _, _ = project
    _, created = create_job(project, "historical")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(ROOT / "tests/test_scoring_purpose_helper.py"),
            "hold",
            created.json()["id"],
            "1",
        ],
        cwd=ROOT,
        env=environment(os.environ["SCORE_CHECK_PROFILE"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )  # nosec B603 -- fixed isolated test child
    try:
        assert select.select([process.stdout], [], [], 25)[0]
        assert json.loads(process.stdout.readline())["event"] == "mock_request_started"
        state = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
        cancelled = web.post(
            f"/api/v1/score-jobs/{created.json()['id']}/state",
            json={"action": "cancel", "reason": "真实取消不伪造未发或归零已发送请求"},
            headers=headers(state.headers["etag"]),
        )
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled", (
            cancelled.text
        )
        stdout, stderr = process.communicate(input="1", timeout=25)
        assert process.returncode == 0, stderr
        output = json.loads(stdout.splitlines()[-1])
        assert not output["ok"] and output["mock_calls"] == 1, output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    state = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
    assert state.json()["state"] == "cancelled" and state.json()["generation"] == 2, state.text
    assert Decimal(state.json()["estimated_fee"]) > 0 and Decimal(state.json()["reserved_fee"]) == 0
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.results WHERE job_id=%s",
                (created.json()["id"],),
            ).fetchone()["n"]
            == 0
        )


def test_score_purpose_repeatable_read_cannot_claim_from_a_stale_snapshot(project):
    web, _, _, _, _, _ = project
    _, created = create_job(project, "historical")
    rejected = child("repeatable_read", created.json()["id"])
    assert rejected == {"ok": True, "checks": ["repeatable_read_refused"], "mock_calls": 0}
    state = web.get(f"/api/v1/score-jobs/{created.json()['id']}")
    assert state.json()["state"] == "queued" and Decimal(state.json()["reserved_fee"]) == 0


def test_api_role_cannot_seal_self_hashed_root_instead_of_actual_review_blob(project):
    from uuid import uuid4

    import psycopg

    from packages.domain.runtime import sha
    from packages.model_governance import scoring_repository as repo

    _, _, bid, _, _, _ = project
    original, _ = create_job(project, "current")
    forged = uuid4()
    with connection() as c:
        row = c.execute(
            "SELECT * FROM score_execution.inputs WHERE id=%s", (original.json()["id"],)
        ).fetchone()
    replacement = json.loads(row["review_root_content"])
    replacement["official_labels"] = ["explicit synthetic injected answer"]
    raw = json.dumps(replacement, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(psycopg.Error) as failure:
        with connection() as c:
            repo.lock(c, bid)
            c.execute(
                "INSERT INTO score_execution.inputs(id,bid_id,review_id,historical_input_id,identity_snapshot,origin,method_digest,price_config,actor_id,review_root_content,review_root_digest) SELECT %s,bid_id,review_id,historical_input_id,identity_snapshot,origin,method_digest,price_config,actor_id,%s,%s FROM score_execution.inputs WHERE id=%s",
                (forged, raw, sha(raw.encode()), row["id"]),
            )
            c.execute(
                "INSERT INTO score_execution.current_chapters SELECT %s,chapter_package_id,raw_content,byte_digest FROM score_execution.current_chapters WHERE input_id=%s",
                (forged, row["id"]),
            )
            assert c.execute(
                "SELECT score_execution.current_input(%s) AS value", (row["id"],)
            ).fetchone()["value"]
            assert not c.execute(
                "SELECT score_execution.current_input(%s) AS value", (forged,)
            ).fetchone()["value"]
            c.execute("SELECT score_execution.seal_input(%s,%s)", (forged, row["actor_id"]))
    assert failure.value.sqlstate == "55000"
