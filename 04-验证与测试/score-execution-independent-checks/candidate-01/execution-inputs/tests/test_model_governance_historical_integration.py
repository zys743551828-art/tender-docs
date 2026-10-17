"""Fresh reviewed historical E, real nonowner/API, explicit synthetic proof mechanism only."""

import os
from uuid import uuid4

import psycopg
import pytest

from apps.api.historical_routes import router
from packages.domain.runtime import connection
from tests.test_research_integration import client as research_client  # noqa: F401 -- fixture
from tests.test_research_integration import seed

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_PROOF_INTEGRATION") != "1",
    reason="Requires PM fresh historical E; no DDL or external calls",
)


@pytest.fixture
def client(research_client):  # noqa: F811 -- fixture
    research_client[0].app.include_router(router)
    return research_client


def validate_response(web, response, schema):
    from jsonschema import Draft202012Validator, FormatChecker

    from packages.model_governance.historical_contracts import extend_openapi

    actual = extend_openapi(web.app.openapi())
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + schema, "components": actual["components"]},
        format_checker=FormatChecker(),
    ).validate(response.json())


def fixture(client):
    _, actor, _, _ = client
    item = seed(actor, year=2024)
    # Append an explicit synthetic offline fact lacking old review/identity. Never modify an
    # original submission or claim that this SQL fixture is an actual ordinary-upload path.
    with connection() as c:
        record = uuid4()
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by,supersedes_id) VALUES(%s,%s,%s,'offline_fact','{}','2019-02-02T00:00:00Z',%s,%s)",
            (record, item["bid"], item["body"], actor, item["submission"]),
        )
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (record, item["bid"]),
        )
        item["submission"] = str(record)
        proof = c.execute(
            "SELECT d.id AS document_version_id,b.digest AS blob_digest,e.parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN evidence_blocks e ON e.parse_id=p.id WHERE d.id=%s",
            (item["body"],),
        ).fetchone()
        projection = c.execute(
            "SELECT bidder.id AS bidder_id,bidder.name AS bidder_name,r.id AS round_id,p.id AS engineering_project_id,p.title AS engineering_title,p.region,p.engineering_type,r.lot,r.round_no FROM bid_profiles b JOIN bidders bidder ON bidder.id=b.bidder_id JOIN bid_rounds r ON r.id=b.round_id JOIN engineering_projects p ON p.id=r.engineering_project_id WHERE b.bid_id=%s",
            (item["bid"],),
        ).fetchone()
        from packages.domain.runtime import clean

        proof, projection = clean(proof), clean(projection)
    proof["explanation"] = "显式合成历史证明机制，非真实性或精度证据"
    projection["bidder_name"] = "2019合成历史公司名"
    projection["engineering_title"] = "2019合成历史工程名"
    return item, proof, projection


def context(web, item):
    result = web.get(f"/api/v1/historical-proof/submissions/{item['submission']}/context")
    assert result.status_code == 200, result.text
    assert result.json()["original_review_id"] is None
    assert result.json()["historical_input_available"] is False
    validate_response(web, result, "HistoricalProofContext")
    return result


def save_subject(client, item, proof, projection, *, supersedes=None):
    web, _, headers, _ = client
    response = web.post(
        "/api/v1/historical-proof/subjects",
        json={
            "submission_id": item["submission"],
            "proposed_identity": projection,
            "proof_refs": [proof] if proof else [],
            "declared_data_origin": "synthetic",
            "supersedes_id": supersedes,
            "reason": "显式合成历史subject，不用current默认补",
        },
        headers=headers(context(web, item).headers["etag"]),
    )

    if response.status_code == 201:
        validate_response(web, response, "HistoricalSubjectAuthorityView")
    return response


def test_pending_and_exact_subject_claim_confirmation_preserve_original_facts(client):
    web, _, headers, _ = client
    item, proof, projection = fixture(client)
    with connection() as c:
        before = c.execute(
            "SELECT review_id,evidence,created_at,submitted_at FROM submission_records WHERE id=%s",
            (item["submission"],),
        ).fetchone()
        source_before = c.execute(
            "SELECT obtained_at FROM document_versions WHERE id=%s", (item["body"],)
        ).fetchone()
    pending = save_subject(client, item, None, None)
    assert pending.status_code == 201 and pending.json()["identity_snapshot"] is None, pending.text
    assert pending.json()["manual_decision"] == "pending"
    denied = web.post(
        f"/api/v1/historical-proof/subjects/{pending.json()['id']}/confirm",
        json={"reason": "不能无证据确认"},
        headers=headers(pending.headers["etag"]),
    )
    assert denied.status_code == 409
    subject = save_subject(client, item, proof, projection, supersedes=pending.json()["id"])
    assert subject.status_code == 201, subject.text
    assert subject.json()["manual_decision"] == "pending" and subject.json()["proofs_current"]
    confirm_headers = headers(subject.headers["etag"])
    path = f"/api/v1/historical-proof/subjects/{subject.json()['id']}/confirm"
    confirmed = web.post(path, json={"reason": "合成人工逐页确认历史关系"}, headers=confirm_headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["manual_decision"] == "confirmed"
    assert confirmed.json()["identity_snapshot"]["bidder_name"] != "合成公司"
    replay = web.post(path, json={"reason": "合成人工逐页确认历史关系"}, headers=confirm_headers)
    assert replay.status_code == 200 and replay.headers["idempotency-replayed"] == "true"
    claim_body = {
        "submission_id": item["submission"],
        "document_version_id": item["body"],
        "historical_subject_authority_id": subject.json()["id"],
        "declared_data_origin": "synthetic",
        "reason": "分开确认公布，不推出本方知晓",
        "facts": [
            {
                "fact_kind": "published",
                "claimed_at": "2019-01-01T00:00:00Z",
                "proof_refs": [proof],
                "explanation": "合成原页支持公开",
            },
            {
                "fact_kind": "known_to_bidder",
                "claimed_at": None,
                "proof_refs": [],
                "explanation": "本方知晓仍未知",
            },
        ],
    }
    claim = web.post(
        "/api/v1/historical-proof/source-claims",
        json=claim_body,
        headers=headers(context(web, item).headers["etag"]),
    )
    assert claim.status_code == 201, claim.text
    claim_confirm = web.post(
        f"/api/v1/historical-proof/source-claims/{claim.json()['id']}/confirm",
        json={"reason": "只确认有据的公开事实，其余仍未知"},
        headers=headers(claim.headers["etag"]),
    )
    assert claim_confirm.status_code == 200, claim_confirm.text
    validate_response(web, claim_confirm, "HistorySourceClaimView")
    assert (
        next(f for f in claim_confirm.json()["facts"] if f["fact_kind"] == "known_to_bidder")[
            "claimed_at"
        ]
        is None
    )
    assert claim_confirm.json()["historical_input_available"] is False
    assert claim_confirm.json()["formal_eligibility"] == "unavailable"
    old = web.get(f"/api/v1/historical-proof/subjects/{pending.json()['id']}")
    assert old.status_code == 200 and not old.json()["is_current_head"]
    assert old.json()["last_own_decision"]["decision"] == "pending"
    with connection() as c:
        assert (
            c.execute(
                "SELECT review_id,evidence,created_at,submitted_at FROM submission_records WHERE id=%s",
                (item["submission"],),
            ).fetchone()
            == before
        )
        assert (
            c.execute(
                "SELECT obtained_at FROM document_versions WHERE id=%s", (item["body"],)
            ).fetchone()
            == source_before
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls call JOIN operations operation ON operation.id=call.operation_id WHERE operation.bid_id=%s",
                (item["bid"],),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("change", ["other_project", "wrong_lot", "string_round"])
def test_subject_relationship_cannot_be_confirmed_by_arbitrary_proof(client, change):
    web, actor, headers, _ = client
    item, proof, projection = fixture(client)
    if change == "other_project":
        other = seed(actor, year=2024)
        projection["engineering_project_id"] = other["identity"]["project_id"]
    elif change == "wrong_lot":
        projection["lot"] = "不匹配的合成标段"
    else:
        projection["round_no"] = "1"
    if change == "string_round":
        rejected = save_subject(client, item, proof, projection)
        assert rejected.status_code == 422
        return
    saved = save_subject(client, item, proof, projection)
    assert saved.status_code == 201, saved.text
    response = web.post(
        f"/api/v1/historical-proof/subjects/{saved.json()['id']}/confirm",
        json={"reason": "合成错配关系不能被人工引用覆盖"},
        headers=headers(saved.headers["etag"]),
    )
    assert response.status_code == 409
    current = web.get(f"/api/v1/historical-proof/subjects/{saved.json()['id']}")
    assert current.json()["manual_decision"] == "pending" and current.json()["head_revision"] == 1


def test_invalid_quote_atomic_and_latest_parse_invalidates_confirm_replay(client):
    web, _, headers, _ = client
    item, proof, projection = fixture(client)
    invalid = save_subject(client, item, proof | {"quote": "原文不存在的合成引文"}, projection)
    assert invalid.status_code == 422
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.subjects WHERE submission_id=%s",
                (item["submission"],),
            ).fetchone()["n"]
            == 0
        )
    saved = save_subject(client, item, proof, projection)
    assert saved.status_code == 201, saved.text
    path = f"/api/v1/historical-proof/subjects/{saved.json()['id']}/confirm"
    confirm_headers = headers(saved.headers["etag"])
    confirmed = web.post(path, json={"reason": "合成有效证明"}, headers=confirm_headers)
    assert confirmed.status_code == 200
    with connection() as c:
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps,created_at) SELECT %s,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps,clock_timestamp() FROM parse_runs WHERE id=%s",
            (uuid4(), proof["parse_id"]),
        )
    stale = web.get(f"/api/v1/historical-proof/subjects/{saved.json()['id']}")
    assert stale.status_code == 200 and not stale.json()["proofs_current"]
    assert stale.json()["last_own_decision"]["decision"] == "confirmed"
    assert (
        web.post(path, json={"reason": "合成有效证明"}, headers=confirm_headers).status_code == 409
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.decisions WHERE object_id=%s AND decision='confirmed'",
                (saved.json()["id"],),
            ).fetchone()["n"]
            == 1
        )


def test_company_known_requires_current_historical_subject_and_replay_cannot_ignore_new_pending(
    client,
):
    web, _, headers, _ = client
    item, proof, projection = fixture(client)
    body = {
        "submission_id": item["submission"],
        "document_version_id": item["body"],
        "declared_data_origin": "synthetic",
        "reason": "合成本方历史知晓须显式subject",
        "facts": [
            {
                "fact_kind": "known_to_bidder",
                "claimed_at": "2019-01-01T00:00:00Z",
                "proof_refs": [proof],
                "explanation": "合成历史关系，不授input资格",
            }
        ],
    }
    pending = web.post(
        "/api/v1/historical-proof/source-claims",
        json=body,
        headers=headers(context(web, item).headers["etag"]),
    )
    assert pending.status_code == 201, pending.text
    assert (
        web.post(
            f"/api/v1/historical-proof/source-claims/{pending.json()['id']}/confirm",
            json={"reason": "无subject不得确认本方"},
            headers=headers(pending.headers["etag"]),
        ).status_code
        == 409
    )
    subject = save_subject(client, item, proof, projection)
    subject_confirmed = web.post(
        f"/api/v1/historical-proof/subjects/{subject.json()['id']}/confirm",
        json={"reason": "合成有据subject"},
        headers=headers(subject.headers["etag"]),
    )
    assert subject_confirmed.status_code == 200
    replacement = web.post(
        "/api/v1/historical-proof/source-claims",
        json=body
        | {
            "supersedes_id": pending.json()["id"],
            "historical_subject_authority_id": subject.json()["id"],
        },
        headers=headers(context(web, item).headers["etag"]),
    )
    assert replacement.status_code == 201, replacement.text
    path = f"/api/v1/historical-proof/source-claims/{replacement.json()['id']}/confirm"
    confirmation_headers = headers(replacement.headers["etag"])
    confirmed = web.post(path, json={"reason": "合成明确本方知晓"}, headers=confirmation_headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["related_subject_current"]
    changed = save_subject(client, item, None, None, supersedes=subject.json()["id"])
    assert changed.status_code == 201 and changed.json()["manual_decision"] == "pending"
    current = web.get(f"/api/v1/historical-proof/source-claims/{replacement.json()['id']}")
    assert (
        current.json()["manual_decision"] == "confirmed"
        and not current.json()["related_subject_current"]
    )
    assert (
        web.post(
            path, json={"reason": "合成明确本方知晓"}, headers=confirmation_headers
        ).status_code
        == 409
    )
    assert (
        web.post(
            path, json={"reason": "不能绕过新pending主体"}, headers=headers(current.headers["etag"])
        ).status_code
        == 409
    )


def test_first_decision_closes_child_set_even_when_append_started_before_commit(client):
    from threading import Event, Thread

    web, actor, _, _ = client
    item, _, _ = fixture(client)
    claim_id = uuid4()
    with connection() as c:
        digest = c.execute(
            "SELECT b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s",
            (item["body"],),
        ).fetchone()["digest"]
        c.execute(
            "INSERT INTO historical_backtest.claims(id,bid_id,submission_id,document_version_id,document_digest,declared_origin,reason,actor_id) VALUES(%s,%s,%s,%s,%s,'synthetic','合成待决定父记录',%s)",
            (claim_id, item["bid"], item["submission"], item["body"], digest, actor),
        )
        c.execute(
            "INSERT INTO historical_backtest.facts(claim_id,fact_kind,claimed_at,explanation) VALUES(%s,'published',NULL,'合成未知')",
            (claim_id,),
        )
    ready, finished = Event(), Event()
    observed = {}

    def append():
        try:
            with connection() as c:
                observed["pid"] = c.execute("SELECT pg_backend_pid() AS value").fetchone()["value"]
                ready.set()
                c.execute(
                    "INSERT INTO historical_backtest.facts(claim_id,fact_kind,claimed_at,explanation) VALUES(%s,'effective',NULL,'不准偷偷扩张已决定版本')",
                    (claim_id,),
                )
            observed["written"] = True
        except psycopg.Error as failure:
            observed["code"] = failure.sqlstate
        finally:
            finished.set()

    with connection() as decision:
        decision.execute("SELECT pg_advisory_xact_lock(91143,1)")
        thread = Thread(target=append, daemon=True)
        thread.start()
        assert ready.wait(3)
        blocked = False
        for _ in range(60):
            with connection() as monitor:
                blocked = monitor.execute(
                    "SELECT EXISTS(SELECT FROM pg_locks WHERE pid=%s AND locktype='advisory' AND classid=91143 AND objid=1 AND NOT granted) AS value",
                    (observed["pid"],),
                ).fetchone()["value"]
            if blocked or finished.wait(0.05):
                break
        assert blocked and not finished.is_set()
        decision.execute(
            "SELECT historical_backtest.decide_claim(%s,0,'pending','首个合成决定',%s,%s)",
            (claim_id, actor, uuid4()),
        )
    assert finished.wait(5)
    thread.join(timeout=1)
    assert observed.get("code") == "55000" and "written" not in observed
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.facts WHERE claim_id=%s", (claim_id,)
            ).fetchone()["n"]
            == 1
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "UPDATE historical_backtest.claim_heads SET revision=revision+1 WHERE claim_id=%s",
                (claim_id,),
            )
    read = web.get(f"/api/v1/historical-proof/source-claims/{claim_id}")
    assert read.status_code == 200 and read.json()["manual_decision"] == "pending"


def test_purpose_roles_cannot_read_historical_proof_and_rr_cannot_confirm(client):
    from psycopg.rows import dict_row

    from packages.model_governance.purpose_profiles import read_profile

    _, actor, _, _ = client
    item, proof, projection = fixture(client)
    saved = save_subject(client, item, proof, projection)
    assert saved.status_code == 201
    environments = [
        read_profile(role, f".local/delivery_protocols01-model_{role}.env")
        for role in ("train", "evaluate")
    ]
    dsns = [
        os.environ[key]
        for key in (
            "WORKER_DATABASE_DSN",
            "RESEARCH_WORKER_DATABASE_DSN",
            "SCORE_READ_DATABASE_DSN",
        )
    ]
    dsns += [
        next(value for key, value in env.items() if key.endswith("DATABASE_DSN"))
        for env in environments
    ]
    for dsn in dsns:
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            identity = c.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            assert not identity["rolsuper"] and not identity["rolbypassrls"]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT * FROM historical_backtest.proofs")
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            c.execute(
                "SELECT historical_backtest.decide_subject(%s,1,'confirmed','合成旧快照拒绝',%s,%s)",
                (saved.json()["id"], actor, uuid4()),
            )


def test_future_known_proof_ancestor_is_rejected_before_any_authority_and_old_submission_history_remains_readable(
    client,
):
    from packages.rag.indexing import parse_source_id

    web, actor, headers, _ = client
    item, proof, projection = fixture(client)
    saved = save_subject(client, item, proof, projection)
    assert saved.status_code == 201
    with connection() as c:
        next_fact = uuid4()
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by,supersedes_id) SELECT %s,bid_id,body_version_id,'offline_fact','{}',submitted_at,created_by,id FROM submission_records WHERE id=%s",
            (next_fact, item["submission"]),
        )
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (next_fact, item["bid"]),
        )
    old = web.get(f"/api/v1/historical-proof/subjects/{saved.json()['id']}")
    assert old.status_code == 200 and not old.json()["submission_current"]
    assert (
        web.post(
            f"/api/v1/historical-proof/subjects/{saved.json()['id']}/confirm",
            json={"reason": "旧实交不得重新确认"},
            headers=headers(old.headers["etag"]),
        ).status_code
        == 409
    )
    item["submission"] = str(next_fact)
    with connection() as c:
        ancestor = uuid4()
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'document','prebid_content','2099-01-01T00:00:00Z',%s)",
            (ancestor, item["body"], proof["blob_digest"]),
        )
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'published',clock_timestamp())",
            (ancestor,),
        )
        c.execute(
            "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
            (parse_source_id(proof["parse_id"]), ancestor),
        )
    rejected = save_subject(client, item, proof, projection)
    assert (
        rejected.status_code == 409
        and rejected.json()["code"] == "HISTORICAL_PROOF_SOURCE_UNAVAILABLE"
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.subjects WHERE submission_id=%s",
                (next_fact,),
            ).fetchone()["n"]
            == 0
        )


def test_actual_auth_csrf_idempotency_and_stale_context_leave_no_partial_authority(client):
    web, _, headers, _ = client
    item, _, _ = fixture(client)
    initial = context(web, item)
    body = {
        "submission_id": item["submission"],
        "proof_refs": [],
        "declared_data_origin": "uncertain",
        "reason": "保存真正未知主体",
    }
    denied = web.post(
        "/api/v1/historical-proof/subjects",
        json=body,
        headers={"If-Match": initial.headers["etag"], "Idempotency-Key": str(uuid4())},
    )
    assert denied.status_code == 403
    stable = headers(initial.headers["etag"])
    saved = web.post("/api/v1/historical-proof/subjects", json=body, headers=stable)
    assert saved.status_code == 201, saved.text
    replay = web.post("/api/v1/historical-proof/subjects", json=body, headers=stable)
    assert replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true"
    conflict = web.post(
        "/api/v1/historical-proof/subjects",
        json=body | {"reason": "相同幂等键改输入不允许"},
        headers=stable,
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    stale = web.post(
        "/api/v1/historical-proof/subjects",
        json=body | {"supersedes_id": saved.json()["id"]},
        headers=headers(initial.headers["etag"]),
    )
    assert stale.status_code == 412
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.subjects WHERE submission_id=%s",
                (item["submission"],),
            ).fetchone()["n"]
            == 1
        )
    cookie = web.cookies.get("session")
    web.cookies.clear()
    assert web.get("/api/v1/historical-proof/capabilities").status_code == 401
    web.cookies.set("session", cookie)
