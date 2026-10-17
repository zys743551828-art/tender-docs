"""New reviewed SQL only; real nonowner PG and full app ASGI, no models/consumers."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.main import app
from packages.domain.runtime import connection, sha
from tests.test_word_intake_integration import world as world

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_APPLICATION_COMPOSITION") != "1",
    reason="Explicit fresh reviewed application DB only",
)


@pytest.fixture
def application(world):  # noqa: F811 -- reused explicit synthetic nonowner fixture
    with connection() as c:
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (world["bid"],))
        who = c.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not who["rolsuper"] and not who["rolbypassrls"]
    with TestClient(app) as web:
        web.cookies.update(world["client"].cookies)
        yield world | {"client": web}


def headers(w, tag=None, key=None):
    return {
        "Origin": "http://word-intake.test",
        "X-CSRF-Token": w["csrf"],
        "Idempotency-Key": key or uuid4().hex,
    } | ({"If-Match": tag} if tag else {})


def validate(response, path, method="get"):
    assert response.status_code in (200, 201), (response.status_code, response.json().get("code"))
    actual = app.openapi()
    schema = actual["paths"][path][method]["responses"][str(response.status_code)]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator(
        {"allOf": [schema], "components": actual["components"]}, format_checker=FormatChecker()
    ).validate(response.json())


def lifecycle(w, action):
    path = f"/api/v1/bids/{w['bid']}/lifecycle"
    current = w["client"].get(path)
    validate(current, "/api/v1/bids/{bid_id}/lifecycle")
    result = w["client"].post(
        path,
        json={"action": action, "reason": "显式合成集成机制"},
        headers=headers(w, current.headers["etag"]),
    )
    validate(result, "/api/v1/bids/{bid_id}/lifecycle", "post")
    return result


def test_actual_capabilities_login_schema_and_no_formal_or_paid_upgrade(application):
    w = application
    web = w["client"]
    paths = [
        "/api/v1/model-governance/capabilities",
        "/api/v1/historical-proof/capabilities",
        "/api/v1/historical-inputs/capabilities",
        "/api/v1/model-registry/capabilities",
        "/api/v1/scoring-capabilities",
    ]
    for path in paths:
        result = web.get(path)
        validate(result, path)
        assert result.json()["schema_configured"] is True
        assert result.json()["paid_execution_enabled"] is False
    context = web.get(f"/api/v1/bids/{w['bid']}/score-context")
    validate(context, "/api/v1/bids/{bid_id}/score-context")
    assert context.json()["formal_eligibility"] == "unavailable"
    web.cookies.clear()
    for path in paths:
        assert web.get(path).status_code == 401


def test_lifecycle_closure_typed_task_replay_and_ordinary_new_work_guard(application):
    w = application
    web = w["client"]
    bid = w["bid"]
    task = f"/api/v1/bids/{bid}/task-definition-drafts"
    current = web.get(f"/api/v1/bids/{bid}")
    original_headers = headers(w, current.headers["etag"])
    created = web.post(task, json={"request": "核对当前否决项"}, headers=original_headers)
    validate(created, "/api/v1/bids/{bid_id}/task-definition-drafts", "post")
    batch = web.post(f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers(w))
    assert batch.status_code == 201
    batch_id = batch.json()["id"]
    closure = f"/api/v1/bids/{bid}/upload-batches/{batch_id}/closure-state"
    lifecycle(w, "terminate")
    blocked = web.post(
        f"/api/v1/bids/{bid}/lifecycle",
        json={"action": "archive", "reason": "不能暗结束批次"},
        headers=headers(w, web.get(f"/api/v1/bids/{bid}").headers["etag"]),
    )
    assert (
        blocked.status_code == 409 and blocked.json()["code"] == "PROJECT_UPLOAD_INTENTS_UNFINISHED"
    )
    state = web.get(closure)
    validate(state, "/api/v1/bids/{bid_id}/upload-batches/{batch_id}/closure-state")
    closed = web.post(
        closure.removesuffix("closure-state") + "close",
        json={"reason": "显式保留暂存结束批次"},
        headers=headers(w, state.headers["etag"]),
    )
    validate(closed, "/api/v1/bids/{bid_id}/upload-batches/{batch_id}/close", "post")
    archived = lifecycle(w, "archive")
    assert archived.json()["archived_at"] is not None
    replay = web.post(task, json={"request": "核对当前否决项"}, headers=original_headers)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == created.json()
    denied = web.post(
        task,
        json={"request": "归档后新工作"},
        headers=headers(w, web.get(f"/api/v1/bids/{bid}").headers["etag"]),
    )
    assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_WORK_CLOSED"
    reread = web.get(task + "/" + created.json()["id"])
    validate(reread, "/api/v1/bids/{bid_id}/task-definition-drafts/{draft_id}")
    lifecycle(w, "reopen")
    assert web.get(closure).json()["closure"]["id"] == closed.json()["closure"]["id"]
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bid,)).fetchone()[
                "n"
            ]
            == 0
        )


def test_archived_historical_pending_subject_is_explicit_exception_not_current_identity(
    application,
):
    w = application
    web = w["client"]
    version, blob, submission = uuid4(), uuid4(), uuid4()
    # Synthetic immutable source/offline fact fixture, no review/model success is manufactured.
    with connection() as c:
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (blob, sha(str(version)), "synthetic-composition/" + str(blob)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'synthetic.pdf',clock_timestamp(),'bid_body',%s)",
            (version, w["bid"], blob, w["actor"]),
        )
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES(%s,%s,%s,'offline_fact','{}','2019-01-01',%s)",
            (submission, w["bid"], version, w["actor"]),
        )
        c.execute(
            "INSERT INTO submission_heads(bid_id,submission_id,revision) VALUES(%s,%s,1)",
            (w["bid"], submission),
        )
    lifecycle(w, "terminate")
    lifecycle(w, "archive")
    path = f"/api/v1/historical-proof/submissions/{submission}/context"
    current = web.get(path)
    validate(current, "/api/v1/historical-proof/submissions/{submission_id}/context")
    saved = web.post(
        "/api/v1/historical-proof/subjects",
        json={
            "submission_id": str(submission),
            "proposed_identity": None,
            "proof_refs": [],
            "declared_data_origin": "synthetic",
            "supersedes_id": None,
            "reason": "未知历史身份保持待确认",
        },
        headers=headers(w, current.headers["etag"]),
    )
    validate(saved, "/api/v1/historical-proof/subjects", "post")
    assert (
        saved.json()["manual_decision"] == "pending" and saved.json()["identity_snapshot"] is None
    )
    inputs = web.get(f"/api/v1/historical-inputs/submissions/{submission}/context")
    validate(inputs, "/api/v1/historical-inputs/submissions/{submission_id}/context")
    phase = web.get(f"/api/v1/bids/{w['bid']}/lifecycle").json()
    assert phase["phase"] == "terminated" and phase["archived_at"] is not None


def test_stage_a_protocol_immutable_replay_and_registry_do_not_promote_formal_models(application):
    from tests.test_model_governance_statistics import bundle

    w = application
    web = w["client"]
    path = "/api/v1/model-governance/training-protocols"
    protocol = bundle()["protocol"]
    protocol["seed"] = uuid4().int % 2147483647
    body = {"document": protocol, "reason": "显式合成软件协议，非正式评分模型"}
    h = headers(w)
    first = web.post(path, json=body, headers=h)
    validate(first, path, "post")
    assert first.json()["formal_eligibility"] == "unavailable"
    read = web.get(path + "/" + first.json()["id"])
    validate(read, "/api/v1/model-governance/training-protocols/{protocol_id}")
    assert read.json() == first.json()
    repeated = web.post(path, json=body, headers=h)
    assert repeated.json() == first.json() and repeated.headers["Idempotency-Replayed"] == "true"
    rejected = web.post(path, json=body | {"accepted": True}, headers=headers(w))
    assert rejected.status_code == 422
    with connection() as c:
        for table in ("operations", "model_calls", "outbox"):
            if table == "operations":
                count = c.execute(
                    "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (w["bid"],)
                ).fetchone()["n"]
            else:
                count = c.execute(
                    f"SELECT count(*) AS n FROM {table} t JOIN operations o ON o.id=t.operation_id WHERE o.bid_id=%s",
                    (w["bid"],),
                ).fetchone()["n"]
            assert count == 0


def test_architect_lifecycle_body_changes_without_tag(application):
    from pathlib import Path
    import json
    w=application
    url=f"/api/v1/bids/{w['bid']}/lifecycle"
    first=w['client'].get(url)
    assert first.status_code==200
    batch=w['client'].post(f"/api/v1/bids/{w['bid']}/upload-batches",json={},headers=headers(w))
    assert batch.status_code==201,batch.text
    later=w['client'].get(url)
    assert later.status_code==200
    assert first.json()!=later.json()
    assert first.headers['etag']==later.headers['etag']
    Path(os.environ['ARCHITECT_LIFECYCLE_RESULT']).write_text(json.dumps({'first':first.json(),'later':later.json(),'first_etag':first.headers['etag'],'later_etag':later.headers['etag'],'batch_id':batch.json()['id'],'scope':'actual authenticated ASGI + nonowner PG; no archive attempted'},ensure_ascii=False,indent=2)+'\n')
