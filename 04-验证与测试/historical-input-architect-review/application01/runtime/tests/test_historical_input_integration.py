"""Fresh reviewed input authority; actual nonowner PG/ASGI, explicit synthetic fixtures.

No DDL, paid models, Kafka consumers, or native bucket operations. The primary fixtures
are mechanism fixtures, NOT evidence of the ordinary upload product path.
"""

import os
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.historical_input_routes import router
from packages.domain import storage
from packages.domain.runtime import clean, connection
from packages.model_governance import historical_input as inputs
from packages.model_governance.historical_input_contracts import extend_openapi
from tests.test_model_governance_historical_integration import (
    client as proof_client,  # noqa: F401 -- fixture dependency
)
from tests.test_model_governance_historical_integration import (
    context as proof_context,
)
from tests.test_model_governance_historical_integration import (
    fixture,
    save_subject,
)
from tests.test_research_integration import (
    client as research_client,  # noqa: F401 -- fixture dependency
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_INPUT_INTEGRATION") != "1",
    reason="Requires PM NEW fresh reviewed historical input SQL; never patches an existing DB",
)


@pytest.fixture
def client(proof_client):  # noqa: F811 -- fixture dependency
    proof_client[0].app.include_router(router)
    return proof_client


def validate(web, response, name):
    actual = extend_openapi(web.app.openapi())
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + name, "components": actual["components"]},
        format_checker=FormatChecker(),
    ).validate(response.json())


def prepared(client, *, missing_known=False):
    web, _, headers, _ = client
    item, body_proof, projection = fixture(client)
    subject = save_subject(client, item, body_proof, projection)
    assert subject.status_code == 201, subject.text
    confirmed = web.post(
        f"/api/v1/historical-proof/subjects/{subject.json()['id']}/confirm",
        json={"reason": "显式合成有据历史身份"},
        headers=headers(subject.headers["etag"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    claims = []
    for document in (item["body"], item["tender"]):
        with connection() as c:
            proof = clean(
                c.execute(
                    "SELECT d.id AS document_version_id,b.digest AS blob_digest,e.parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN evidence_blocks e ON e.parse_id=p.id WHERE d.id=%s",
                    (document,),
                ).fetchone()
            )
        proof["explanation"] = "显式合成证明，不宣称真实性已验收"
        facts = [
            {
                "fact_kind": kind,
                "claimed_at": None
                if missing_known and kind == "known_to_bidder"
                else "2019-01-01T00:00:00Z",
                "proof_refs": [] if missing_known and kind == "known_to_bidder" else [proof],
                "explanation": "合成独立历史事实",
            }
            for kind in (
                ["content_existed", "known_to_bidder"]
                + (["effective"] if document != item["body"] else [])
            )
        ]
        claim = web.post(
            "/api/v1/historical-proof/source-claims",
            json={
                "submission_id": item["submission"],
                "document_version_id": document,
                "historical_subject_authority_id": subject.json()["id"],
                "facts": facts,
                "declared_data_origin": "synthetic",
                "reason": "显式合成来源历史事实",
            },
            headers=headers(proof_context(web, item).headers["etag"]),
        )
        assert claim.status_code == 201, claim.text
        confirm = web.post(
            f"/api/v1/historical-proof/source-claims/{claim.json()['id']}/confirm",
            json={"reason": "显式合成事实核验"},
            headers=headers(claim.headers["etag"]),
        )
        assert confirm.status_code == 200, confirm.text
        claims.append(claim.json()["id"])
    with connection() as c:
        ruleset = c.execute("SELECT id FROM rule_sets WHERE bid_id=%s", (item["bid"],)).fetchone()[
            "id"
        ]
    value = {
        "submission_id": item["submission"],
        "historical_subject_authority_id": subject.json()["id"],
        "source_claim_ids": claims,
        "material_version_ids": [item["tender"]],
        "rule_set_id": str(ruleset),
        "reason": "当前创建的合成历史重建，非2019年预测",
    }
    context = web.get(f"/api/v1/historical-inputs/submissions/{item['submission']}/context")
    assert context.status_code == 200, context.text
    validate(web, context, "HistoricalInputContext")
    return item, value, headers(context.headers["etag"])


def test_offline_without_old_review_now_known_can_freeze_full_input_and_replay(client):
    web, _, _, objects = client
    item, value, headers = prepared(client)
    with connection() as c:
        original = c.execute(
            "SELECT review_id,evidence,submitted_at,created_at FROM submission_records WHERE id=%s",
            (item["submission"],),
        ).fetchone()
        sources = c.execute(
            "SELECT id,known_at FROM retrieval.source_nodes WHERE document_version_id=ANY(%s::uuid[]) ORDER BY id",
            ([item["body"], item["tender"]],),
        ).fetchall()
    assert original["review_id"] is None and all(
        x["known_at"] > original["submitted_at"] for x in sources
    )
    response = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert response.status_code == 201, response.text
    validate(web, response, "HistoricalInputView")
    assert response.json()["current"] and response.json()["rule_count"] == 1
    assert response.json()["origin"] == "synthetic" and response.json()["research_only"]
    assert response.json()["formal_eligibility"] == "unavailable"
    assert response.json()["historical_as_of"].startswith("2019-") and not response.json()[
        "created_at"
    ].startswith("2019-")
    before = len(objects)
    replay = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true"
    assert len(objects) == before
    read = web.get(f"/api/v1/historical-inputs/{response.json()['id']}")
    assert read.status_code == 200 and read.json() == response.json()
    with connection() as c:
        assert (
            c.execute(
                "SELECT review_id,evidence,submitted_at,created_at FROM submission_records WHERE id=%s",
                (item["submission"],),
            ).fetchone()
            == original
        )
        assert (
            c.execute(
                "SELECT id,known_at FROM retrieval.source_nodes WHERE document_version_id=ANY(%s::uuid[]) ORDER BY id",
                ([item["body"], item["tender"]],),
            ).fetchall()
            == sources
        )
        calls = c.execute(
            "SELECT count(*) AS n FROM model_calls call JOIN operations op ON op.id=call.operation_id WHERE op.bid_id=%s",
            (item["bid"],),
        ).fetchone()["n"]
        assert calls == 0


def test_missing_known_to_bidder_rejected_before_any_put(client):
    web, _, _, objects = client
    item, value, headers = prepared(client, missing_known=True)
    before = len(objects)
    response = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert (
        response.status_code == 409 and response.json()["code"] == "HISTORICAL_INPUT_UNAVAILABLE"
    ), response.text
    assert len(objects) == before
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.inputs WHERE bid_id=%s",
                (item["bid"],),
            ).fetchone()["n"]
            == 0
        )


def test_remote_put_has_no_capture_transaction_and_change_during_put_blocks_publish(
    client, monkeypatch
):
    web, _, _, _ = client
    item, value, headers = prepared(client)
    original_capture, original_put = inputs.capture, storage.put
    pids = []

    def capture(c, *args, **kwargs):
        pids.append(c.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
        return original_capture(c, *args, **kwargs)

    altered = []

    def put(key, raw, media):
        with connection() as c:
            assert not c.execute(
                "SELECT pid FROM pg_stat_activity WHERE pid=ANY(%s::int[]) AND state='idle in transaction'",
                (pids,),
            ).fetchall()
            if not altered:
                from packages.rag.indexing import original_source_id

                c.execute("SELECT pg_advisory_xact_lock(90127,1)")
                c.execute(
                    "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                    (original_source_id(item["body"]),),
                )
                altered.append(True)
        return original_put(key, raw, media)

    monkeypatch.setattr(inputs, "capture", capture)
    monkeypatch.setattr(storage, "put", put)
    response = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert response.status_code == 409, response.text
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.inputs WHERE bid_id=%s",
                (item["bid"],),
            ).fetchone()["n"]
            == 0
        )


def test_withdrawn_or_reparsed_input_readonly_stale_and_replay_denied(client):
    web, _, _, _ = client
    item, value, headers = prepared(client)
    saved = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert saved.status_code == 201, saved.text
    with connection() as c:
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps,created_at) SELECT %s,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps,clock_timestamp() FROM parse_runs WHERE id=%s",
            (uuid4(), item["body_parse_id"]),
        )
    read = web.get(f"/api/v1/historical-inputs/{saved.json()['id']}")
    assert read.status_code == 200 and not read.json()["current"]
    replay = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert replay.status_code == 409


def test_sealed_inputs_reject_late_children_and_repeated_read_old_snapshot(client):
    import psycopg

    from packages.domain.runtime import DomainError

    web, _, _, _ = client
    item, value, headers = prepared(client)
    saved = web.post("/api/v1/historical-inputs", json=value, headers=headers)
    assert saved.status_code == 201, saved.text
    identity = saved.json()["id"]
    with pytest.raises(psycopg.Error) as error, connection() as c:
        c.execute(
            "INSERT INTO historical_backtest.input_claims SELECT input_id,claim_id,decision_id,purpose FROM historical_backtest.input_claims WHERE input_id=%s LIMIT 1",
            (identity,),
        )
    assert error.value.sqlstate == "55000"
    with pytest.raises(DomainError) as error, connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        inputs.view(c, identity)
    assert error.value.code == "HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED"


@pytest.mark.parametrize(
    "dsn_key", ["WORKER_DATABASE_DSN", "RESEARCH_WORKER_DATABASE_DSN", "SCORE_READ_DATABASE_DSN"]
)
def test_non_api_roles_cannot_read_or_seal_historical_inputs(dsn_key):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(os.environ[dsn_key], row_factory=dict_row) as c:
        role = c.execute(
            "SELECT r.rolsuper,has_schema_privilege(current_user,'historical_input','USAGE') AS allowed FROM pg_roles r WHERE r.rolname=current_user"
        ).fetchone()
        assert not role["rolsuper"] and not role["allowed"]
        with pytest.raises(psycopg.Error) as error:
            c.execute("SELECT raw_content FROM historical_input.chapter_payloads")
        assert error.value.sqlstate == "42501"
