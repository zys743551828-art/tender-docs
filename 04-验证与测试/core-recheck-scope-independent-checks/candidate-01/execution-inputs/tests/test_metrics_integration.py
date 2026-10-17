"""Local PG/ASGI FR011 mechanics; all evidence identities are synthetic fixtures, zero model/OSS calls."""

import hashlib
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api.metrics_routes import router
from packages.domain.reviews import method_profile
from packages.domain.runtime import Command, DomainError, clean, connection, now, secret_hash, sha
from packages.identity import repository as identities
from packages.metrics import records, snapshots

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_METRICS_INTEGRATION") != "1", reason="需显式本地非owner PG环境；不发远端"
)


class SyntheticContext:
    def __init__(self, client, actor, headers):
        self.client, self.actor, self.headers = client, actor, headers

    def __iter__(self):
        return iter((self.client, self.actor, self.headers))

    def __repr__(self):
        return "<SyntheticFR011Context credentials=redacted>"


@pytest.fixture
def context():
    actor = uuid4()
    token = secrets.token_hex(32)
    csrf = secrets.token_hex(24)
    with connection() as c:
        who = c.execute(
            "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not who["rolsuper"] and not who["rolbypassrls"]
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (actor, "合成FR011开发机制成员")
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                actor,
                secret_hash(token),
                hashlib.sha256(csrf.encode()).hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "details": clean(exc.details)}, status_code=exc.status
        )

    with TestClient(app) as client:
        client.cookies.set("session", token)
        yield SyntheticContext(
            client, actor, {"Origin": os.environ["APP_ORIGIN"], "X-CSRF-Token": csrf}
        )


def make_project(c, actor, unit=None, visible=True):
    bid, body, official, tender = uuid4(), uuid4(), uuid4(), uuid4()
    if unit:
        engineering, round_id, bidder = unit
    else:
        engineering, round_id = uuid4(), uuid4()
        bidder = UUID(
            identities.register_bidder(c, actor, "合成投标主体 " + uuid4().hex, "机制夹具", None)[
                "id"
            ]
        )
        c.execute(
            "INSERT INTO engineering_projects(id,title) VALUES(%s,%s)",
            (engineering, "合成FR011工程"),
        )
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, engineering),
        )
    c.execute(
        "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,%s,%s)",
        (
            bid,
            "合成FR011机制项目",
            Jsonb({"show_prediction": visible, "consistency": True, "scope_residue": False}),
            actor,
        ),
    )
    c.execute(
        "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
        (bid, round_id, bidder),
    )
    c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
    evidence = {}
    for identity, kind in (
        (body, "bid_body"),
        (official, "source_original"),
        (tender, "source_original"),
    ):
        blob = uuid4()
        digest = sha(str(identity).encode())
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,32,'application/pdf')",
            (blob, digest, "synthetic-metrics/" + str(blob)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,clock_timestamp(),%s,%s)",
            (identity, bid, blob, "合成资料.pdf", kind, actor),
        )
        if kind == "source_original":
            parse, block = uuid4(), uuid4()
            text = (
                "Synthetic official awarded result page."
                if identity == official
                else "Synthetic mandatory original clause."
            )
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
                (parse, bid, identity, "a" * 64, "b" * 64),
            )
            c.execute(
                "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
                (bid, parse),
            )
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
                (block, bid, parse, text, sha(text.encode())),
            )
            evidence[str(identity)] = {
                "document_version_id": str(identity),
                "evidence_id": str(block),
                "physical_page": 1,
                "quote": text,
            }
    material = uuid4()
    c.execute(
        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
        (material, bid, "c" * 64),
    )
    for index, (doc, role) in enumerate(
        ((official, "official_result"), (tender, "tender"), (body, "body"))
    ):
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,%s)",
            (material, bid, doc, role, index),
        )
    c.execute(
        "UPDATE bid_profiles SET current_body_id=%s,current_material_id=%s WHERE bid_id=%s",
        (body, material, bid),
    )
    identity = identities.record_binding(c, bid, actor, None, reason="合成明确身份", confirmed=True)
    submitted = now() - timedelta(days=2)
    submission = uuid4()
    c.execute(
        "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES(%s,%s,%s,'offline_fact',%s,%s,%s)",
        (
            submission,
            bid,
            body,
            Jsonb(
                {
                    "identity": identity,
                    "gate_checked": False,
                    "evidence_ref": "Synthetic mechanical receipt",
                }
            ),
            submitted,
            actor,
        ),
    )
    c.execute(
        "INSERT INTO submission_heads(bid_id,submission_id,revision) VALUES(%s,%s,1)",
        (bid, submission),
    )
    return {
        "bid": bid,
        "body": body,
        "official": official,
        "tender": tender,
        "material": material,
        "submission": submission,
        "identity": identity,
        "unit": (engineering, round_id, bidder),
        "refs": evidence,
        "submitted_at": submitted,
    }


def payload(project, stage="candidate", official=False):
    return {
        "submission_id": str(project["submission"]),
        "stage": stage,
        "source_type": "official" if official else "user_reported",
        "source_version_id": str(project["official"]) if official else None,
        "evidence_refs": [project["refs"][str(project["official"])]] if official else [],
        "announced_at": (project["submitted_at"] + timedelta(hours=1)).isoformat()
        if official
        else None,
        "known_at": (project["submitted_at"] + timedelta(hours=2)).isoformat(),
        "reason": "合成业务结果记录",
    }


def hdr(headers, submission, revision=0, key=None):
    return headers | {
        "Idempotency-Key": key or uuid4().hex,
        "If-Match": f'"business-result-head:{submission}:{revision}"',
    }


def endpoint(project):
    return f"/api/v1/bids/{project['bid']}/submissions/{project['submission']}/business-results"


def test_api_stage_independent_from_score_correction_conflict_no_fallback(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        outcome = uuid4()
        c.execute(
            "INSERT INTO outcomes(id,bid_id,submission_id,score_kind,value,maximum,source_version_id,known_at,created_by) VALUES(%s,%s,%s,'technical_total',95,100,%s,clock_timestamp(),%s)",
            (outcome, p["bid"], p["submission"], p["official"], actor),
        )
        c.execute(
            "INSERT INTO outcome_heads(submission_id,bid_id,outcome_id,revision) VALUES(%s,%s,%s,1)",
            (p["submission"], p["bid"], outcome),
        )
    initial = client.get(endpoint(p))
    assert initial.status_code == 200 and initial.json()["current_result"] is None
    body = payload(p, "candidate", True)
    h = hdr(headers, p["submission"])
    created = client.post(endpoint(p), json=body, headers=h)
    assert created.status_code == 201, created.text
    assert created.json()["stage"] == "candidate" and created.json()["verification"] == "pending"
    assert client.post(endpoint(p), json=body, headers=h).json()["id"] == created.json()["id"]
    rid = created.json()["id"]
    proof = {
        "state": "verified",
        "reason": "合成原页机制核对",
        "identity_confirmed": True,
        "evidence_refs": body["evidence_refs"],
    }
    verified = client.post(
        f"/api/v1/business-results/{rid}/verification-decisions",
        json=proof,
        headers=hdr(headers, p["submission"], 1),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["verification"] == "verified"
    corrected = client.post(
        f"/api/v1/business-results/{rid}/corrections",
        json={
            **body,
            "stage": "awarded",
            "base_result_id": rid,
            "correction_reason": "合成候选转正式结果",
        },
        headers=hdr(headers, p["submission"], 2),
    )
    assert corrected.status_code == 201, corrected.text
    new_id = corrected.json()["id"]
    conflicted = client.post(
        f"/api/v1/business-results/{new_id}/verification-decisions",
        json={"state": "conflict", "reason": "合成来源冲突待核实"},
        headers=hdr(headers, p["submission"], 3),
    )
    assert conflicted.status_code == 200
    value = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert (
        value["known_outcomes"] == 0
        and value["conflicted_outcomes"] == 1
        and value["win_rate"]["value"] is None
    )
    assert client.get(f"/api/v1/business-results/{rid}").json()["verification"] == "verified"
    with connection() as c:
        original = c.execute(
            "SELECT value,score_kind FROM outcomes WHERE id=%s", (outcome,)
        ).fetchone()
        assert str(original["value"]) == "95" and original["score_kind"] == "technical_total"


def test_api_concurrent_correction_cas_epoch_and_official_evidence_boundaries(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
    missing = client.post(
        endpoint(p), json=payload(p), headers=headers | {"Idempotency-Key": uuid4().hex}
    )
    assert missing.status_code == 428
    created = client.post(endpoint(p), json=payload(p), headers=hdr(headers, p["submission"]))
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    denied = client.post(
        f"/api/v1/business-results/{rid}/verification-decisions",
        json={
            "state": "verified",
            "reason": "不能假核实",
            "identity_confirmed": True,
            "evidence_refs": [],
        },
        headers=hdr(headers, p["submission"], 1),
    )
    assert denied.status_code == 422

    def correct(stage):
        return client.post(
            f"/api/v1/business-results/{rid}/corrections",
            json={**payload(p, stage), "base_result_id": rid, "correction_reason": "合成并发更正"},
            headers=hdr(headers, p["submission"], 1),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(correct, ["awarded", "not_awarded"]))
    assert sorted(r.status_code for r in responses) == [201, 412]
    history = client.get(endpoint(p)).json()
    assert history["total"] == 2 and history["head_revision"] == 2
    with connection() as c:
        epochs = [r["payload"]["metrics_epoch"] for r in records.rows(c, p["bid"])]
        assert epochs == sorted(set(epochs)) and len(epochs) == 2


def test_source_withdrawal_and_submission_replacement_do_not_inherit_old_result(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
    created = client.post(
        endpoint(p), json=payload(p, "awarded", True), headers=hdr(headers, p["submission"])
    )
    assert created.status_code == 201, created.text
    with connection() as c:
        source = uuid4()
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'document','official_label',clock_timestamp(),%s)",
            (source, p["official"], "d" * 64),
        )
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'withdrawn',clock_timestamp())",
            (source,),
        )
    invalid = client.get(f"/api/v1/business-results/{created.json()['id']}").json()
    assert not invalid["usable"] and "source_no_longer_effective" in invalid["unusable_reasons"]
    with connection() as c:
        replacement = uuid4()
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by,supersedes_id) VALUES(%s,%s,%s,'offline_fact',%s,%s,%s,%s)",
            (
                replacement,
                p["bid"],
                p["body"],
                Jsonb({"identity": p["identity"], "gate_checked": False}),
                p["submitted_at"] + timedelta(minutes=1),
                actor,
                p["submission"],
            ),
        )
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (replacement, p["bid"]),
        )
    current = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert (
        current["effective_submissions"] == 1
        and current["unknown_outcomes"] == 1
        and current["known_outcomes"] == 0
    )
    assert client.get(endpoint(p)).json()["total"] == 1


def test_same_unit_heads_conflict_and_submitted_time_scope(context):
    client, actor, headers = context
    with connection() as c:
        one = make_project(c, actor)
        two = make_project(c, actor, unit=one["unit"])
    value = client.get(
        "/api/v1/metrics", params=[("bid_ids", str(one["bid"])), ("bid_ids", str(two["bid"]))]
    ).json()
    assert value["effective_submissions"] == 0
    assert value["scope"]["excluded_submission_counts"]["identity_submission_conflict"] == 2
    with connection() as c:
        isolated = make_project(c, actor)
    inside = client.get(
        "/api/v1/metrics",
        params={
            "bid_ids": str(isolated["bid"]),
            "period_from": (isolated["submitted_at"] - timedelta(hours=1)).isoformat(),
            "period_to": (isolated["submitted_at"] + timedelta(hours=1)).isoformat(),
        },
    ).json()
    assert inside["effective_submissions"] == 1
    late = client.get(
        "/api/v1/metrics",
        params={
            "bid_ids": str(isolated["bid"]),
            "period_from": (now() - timedelta(hours=1)).isoformat(),
        },
    ).json()
    assert (
        late["effective_submissions"] == 0
        and late["scope"]["excluded_submission_counts"]["outside_submitted_at_period"] == 1
    )


def test_unknown_risk_freeze_is_immutable_and_historical_prediction_not_qualified(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        submission = c.execute(
            "SELECT * FROM submission_records WHERE id=%s", (p["submission"],)
        ).fetchone()
        snapshot = snapshots.freeze_submission_risks(
            c, submission=submission, actor=actor, command=None
        )
        assert snapshot["basis"] == "unknown" and snapshot["open"] is None
        again = snapshots.freeze_submission_risks(
            c, submission=submission, actor=actor, command=None
        )
        assert again == snapshot
    metrics = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert metrics["qualification"]["open_at_submission"] is None
    assert metrics["qualification"]["unknown_snapshot_submissions"] == 1
    assert (
        metrics["online_error"]["prediction_pairs"] == 0 and metrics["online_error"]["mae"] is None
    )
    assert not metrics["model_qualification_granted"]


def make_review(
    c, actor, project, *, method=None, rule_set=None, source=None, previous_finding=None
):
    bid = project["bid"]
    method = method or sha(method_profile())
    if source is None:
        source = uuid4()
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'document','prebid_content',clock_timestamp(),%s)",
            (source, project["tender"], "a" * 64),
        )
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'published',clock_timestamp())",
            (source,),
        )
    if rule_set is None:
        rule_set = uuid4()
        rule = uuid4()
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, project["material"], "b" * 64),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'mandatory','Synthetic mandatory obligation','[]','[]',%s,'[]')",
            (rule, bid, rule_set, [UUID(project["refs"][str(project["tender"])]["evidence_id"])]),
        )
    else:
        rule = c.execute(
            "SELECT id FROM rule_items WHERE rule_set_id=%s ORDER BY id LIMIT 1", (rule_set,)
        ).fetchone()["id"]
    cmd = Command(c, actor, "synthetic-metrics-review", bid, uuid4().hex, {})
    cmd.finish({"synthetic_fixture": True})
    operation, review, input_id, manifest_blob = uuid4(), uuid4(), uuid4(), uuid4()
    manifest = {
        "bid_id": str(bid),
        "body_version_id": str(project["body"]),
        "material_snapshot_id": str(project["material"]),
        "rule_set_id": str(rule_set),
        "fact_set_id": None,
        "source_refs": [{"id": str(source)}],
        "content_checks": {"consistency": True, "scope_residue": False},
    }
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,0,'application/json')",
        (
            manifest_blob,
            sha(str(manifest_blob).encode()),
            "synthetic-metrics/" + str(manifest_blob),
        ),
    )
    c.execute(
        "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'synthetic-metrics',%s,'succeeded','synthetic-fixture/1',clock_timestamp())",
        (operation, bid, cmd.id, sha(str(review).encode())),
    )
    c.execute(
        "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s,clock_timestamp(),%s)",
        (
            input_id,
            bid,
            project["material"],
            project["body"],
            sha(str(input_id).encode()),
            method,
            Jsonb(manifest),
            manifest_blob,
            sha(manifest),
            "f" * 64,
        ),
    )
    c.execute(
        "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
        (review, bid, input_id, operation),
    )
    c.execute(
        "INSERT INTO review_results(id,bid_id,review_id,core_coverage,payload,coverage_layers) VALUES(%s,%s,%s,'complete','{}','{}')",
        (uuid4(), bid, review),
    )
    c.execute(
        "UPDATE bid_profiles SET current_review_id=%s,current_rule_set_id=%s WHERE bid_id=%s",
        (review, rule_set, bid),
    )
    finding = uuid4()
    c.execute(
        "INSERT INTO findings(id,bid_id,review_id,rule_id,category,severity,verdict,statement,searched_ranges,evidence_ids,carried_from_id) VALUES(%s,%s,%s,%s,'disqualification','critical','violated','Synthetic missing mandatory response','[]',%s,%s)",
        (
            finding,
            bid,
            review,
            rule,
            [UUID(project["refs"][str(project["tender"])]["evidence_id"])],
            previous_finding,
        ),
    )
    c.execute(
        "INSERT INTO matrix_checks(bid_id,review_id,rule_id,state,verdict,evidence_ids,revision) VALUES(%s,%s,%s,'succeeded','violated',%s,1)",
        (bid, review, rule, [UUID(project["refs"][str(project["tender"])]["evidence_id"])]),
    )
    return {
        "review": review,
        "rule_set": rule_set,
        "source": source,
        "finding": finding,
        "input": input_id,
        "operation": operation,
        "manifest": manifest,
    }


def test_server_observation_risk_freeze_survives_later_dismiss_and_uses_root_identity(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        first = make_review(c, actor, p)
        second = make_review(
            c,
            actor,
            p,
            rule_set=first["rule_set"],
            source=first["source"],
            previous_finding=first["finding"],
        )
        third = make_review(
            c,
            actor,
            p,
            rule_set=first["rule_set"],
            source=first["source"],
            previous_finding=second["finding"],
        )
        observation = snapshots.observe_review_risks(
            c, bid=p["bid"], actor=actor, command=None, review_id=third["review"]
        )
        assert observation["snapshot"]["identified"] == 1 and observation["snapshot"]["open"] == 1
        submitted = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        prepared = {
            "id": uuid4(),
            "bid_id": p["bid"],
            "body_version_id": p["body"],
            "review_id": third["review"],
            "submitted_at": submitted,
            "evidence": {"identity": p["identity"]},
        }
        frozen = snapshots.freeze_submission_risks(
            c, submission=prepared, actor=actor, command=None
        )
        assert (
            frozen["basis"] == "reconstructed_from_audit"
            and frozen["open"] == 1
            and len(frozen["scope_issue_ids"]) == 1
        )
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,review_id,kind,evidence,submitted_at,created_by,supersedes_id) VALUES(%s,%s,%s,%s,'offline_fact',%s,%s,%s,%s)",
            (
                prepared["id"],
                p["bid"],
                p["body"],
                third["review"],
                Jsonb(prepared["evidence"] | {"gate_checked": False, "open_risk_snapshot": frozen}),
                submitted,
                actor,
                p["submission"],
            ),
        )
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (prepared["id"], p["bid"]),
        )
    with connection() as c:
        decision_id = uuid4()
        c.execute(
            "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'finding',%s,1,'dismiss','Synthetic reasoned manual dismissal','{}',%s)",
            (decision_id, p["bid"], first["finding"], actor),
        )
        c.execute(
            "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'finding',%s,1,%s)",
            (p["bid"], first["finding"], decision_id),
        )
        snapshots.observe_review_risks(c, bid=p["bid"], actor=actor, command=None)
    value = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert value["qualification"]["identified"] == 1
    assert value["qualification"]["dismissed"] == 1 and value["qualification"]["resolved"] == 0
    assert (
        value["qualification"]["open_at_submission"] == 1
        and value["qualification"]["known_snapshot_submissions"] == 1
    )
    with connection() as c:
        submission = c.execute(
            "SELECT * FROM submission_records WHERE id=%s", (prepared["id"],)
        ).fetchone()
        assert snapshots.for_submission(c, submission) == frozen


def test_prepared_risk_snapshot_and_result_records_roll_back_atomically(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
    prepared = {
        "id": uuid4(),
        "bid_id": p["bid"],
        "body_version_id": p["body"],
        "review_id": None,
        "submitted_at": now(),
        "evidence": {"identity": p["identity"]},
    }
    with pytest.raises(RuntimeError, match="synthetic post-freeze failure"):
        with connection() as c:
            snapshots.freeze_submission_risks(c, submission=prepared, actor=actor, command=None)
            raise RuntimeError("synthetic post-freeze failure")
    with connection() as c:
        assert not [
            r
            for r in records.rows(c, p["bid"], (records.RISK,))
            if r["payload"]["submission_id"] == str(prepared["id"])
        ]
        assert (
            c.execute(
                "SELECT count(*) AS n FROM submission_records WHERE id=%s", (prepared["id"],)
            ).fetchone()["n"]
            == 0
        )


def test_round_trend_breaks_stable_paging_and_hidden_prediction_has_no_values(context):
    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor, visible=False)
        first = make_review(c, actor, p)
        second = make_review(
            c,
            actor,
            p,
            method="9" * 64,
            rule_set=first["rule_set"],
            source=first["source"],
            previous_finding=first["finding"],
        )
        for review in (first, second):
            c.execute(
                "INSERT INTO predictions(id,bid_id,review_id,method_digest,mode,value,maximum,lower_bound,upper_bound,reasons) VALUES(%s,%s,%s,%s,'raw_uncalibrated',80,100,70,90,'[]')",
                (uuid4(), p["bid"], review["review"], "e" * 64),
            )
    first_page = client.get(f"/api/v1/bids/{p['bid']}/round-trends", params={"limit": 1}).json()
    assert len(first_page["items"]) == 1 and first_page["next_after"]
    next_page = client.get(
        f"/api/v1/bids/{p['bid']}/round-trends",
        params={"limit": 1, "after": first_page["next_after"]},
    ).json()
    point = next_page["items"][0]
    assert not point["comparable_to_previous"] and "method_changed" in point["break_reasons"]
    assert point["prediction"] == {"mode": "hidden", "reasons": ["project_prediction_hidden"]}
    assert point["risk_counts"]["critical"] == 1
    aggregate = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert aggregate["online_error"]["mae"] is None and aggregate["online_error"]["bias"] is None
    assert aggregate["online_error"]["excluded_pair_counts"]["prediction_hidden"] == 1


def test_worker_observation_uses_restricted_lock_function_and_cannot_update_bid(context):
    import psycopg

    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        review = make_review(c, actor, p)
    with connection(True) as worker:
        result = snapshots.observe_review_risks(
            worker, bid=p["bid"], actor=actor, command=None, review_id=review["review"]
        )
        assert result["snapshot"]["identified"] == 1 and result["snapshot"]["open"] == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection(True) as worker:
            worker.execute("UPDATE bids SET revision=revision+1 WHERE id=%s", (p["bid"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection(True) as worker:
            worker.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (p["bid"],))


def test_actual_action_epoch_not_older_transaction_start_selects_head(context):
    client, actor, headers = context
    from packages.metrics import results

    with connection() as c:
        p = make_project(c, actor)
    with connection() as earlier_transaction:
        old_start = earlier_transaction.execute("SELECT transaction_timestamp() AS t").fetchone()[
            "t"
        ]
        with connection() as later_transaction:
            first, _ = results.register(
                later_transaction,
                bid=p["bid"],
                submission_id=p["submission"],
                actor=actor,
                command=None,
                body=payload(p),
                condition=f'"business-result-head:{p["submission"]}:0"',
            )
        second_body = {
            **payload(p, "awarded"),
            "base_result_id": first["id"],
            "correction_reason": "Synthetic later action in older transaction",
        }
        second, _ = results.register(
            earlier_transaction,
            bid=p["bid"],
            submission_id=p["submission"],
            actor=actor,
            command=None,
            body=second_body,
            condition=f'"business-result-head:{p["submission"]}:1"',
            previous=UUID(first["id"]),
        )
        assert (
            old_start
            < results.timestamp(first["created_at"])
            < results.timestamp(second["created_at"])
        )
    value = client.get(endpoint(p)).json()
    assert value["head_revision"] == 2 and value["current_result"]["id"] == second["id"]


def test_prediction_needs_dedicated_producer_authority_not_scalar_or_client_claim(context):
    from packages.metrics.predictions import freeze_prediction

    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        review = make_review(c, actor, p)
        prediction = uuid4()
        c.execute(
            "INSERT INTO predictions(id,bid_id,review_id,method_digest,mode,value,maximum,reasons) VALUES(%s,%s,%s,%s,'raw_uncalibrated',80,100,'[]')",
            (prediction, p["bid"], review["review"], sha(method_profile())),
        )
        with pytest.raises(DomainError) as failure:
            freeze_prediction(
                c,
                bid=p["bid"],
                actor=actor,
                command=None,
                prediction_id=prediction,
                proof_event_id=uuid4(),
            )
        assert failure.value.code == "PROSPECTIVE_PREDICTION_NOT_AVAILABLE"
    value = client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json()
    assert value["online_error"]["prediction_pairs"] == 0
    assert value["online_error"]["excluded_pair_counts"]["no_server_fixed_pre_submission_pair"] == 1


def test_actual_read_schemas_and_openapi_do_not_reuse_old_metrics_shape(context):
    from packages.metrics.contracts import extend_openapi, validate

    client, actor, headers = context
    with connection() as c:
        p = make_project(c, actor)
        make_review(c, actor, p)
    empty = client.get(endpoint(p)).json()
    validate("BusinessResultPage", empty)
    created = client.post(endpoint(p), json=payload(p), headers=hdr(headers, p["submission"]))
    assert created.status_code == 201
    validate("BusinessResultView", created.json())
    validate(
        "BusinessMetricsView",
        client.get("/api/v1/metrics", params={"bid_ids": str(p["bid"])}).json(),
    )
    validate("BusinessRoundTrendPage", client.get(f"/api/v1/bids/{p['bid']}/round-trends").json())
    document = extend_openapi(client.app.openapi())
    schema = document["paths"]["/api/v1/metrics"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["$ref"] == "#/components/schemas/BusinessMetricsView"
    assert (
        "bias"
        in document["components"]["schemas"]["BusinessMetricsView"]["properties"]["online_error"][
            "properties"
        ]
    )
