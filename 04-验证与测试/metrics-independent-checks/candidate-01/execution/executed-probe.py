"""Independent FR011 assertions; developer helpers reused only to seed explicit SQL fixtures."""

import hashlib
import importlib.util
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api.metrics_routes import router
from packages.domain.runtime import DomainError, assert_role, clean, connection, now, secret_hash, sha
from packages.metrics import records, snapshots


def main():
    root=Path(os.environ["QA_METRICS_ROOT"]);out=root/"execution";out.mkdir(exist_ok=False)
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name=="rag-checks.env"
    assert_role();assert_role(True)
    inputs=json.loads((root/"runtime-manifest.json").read_text())
    def verify():assert all(hashlib.sha256((root/"execution-inputs"/r["path"]).read_bytes()).hexdigest()==r["sha256"] for r in inputs)
    verify()
    fixture_file=root/"execution-inputs/tests/test_metrics_integration.py"
    spec=importlib.util.spec_from_file_location("qa_fr011_seed_only",fixture_file);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    actor,token,csrf=uuid4(),secrets.token_hex(32),secrets.token_hex(24)
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'FR011独立对抗用例')",(actor,))
        c.execute("INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",(uuid4(),actor,secret_hash(token),hashlib.sha256(csrf.encode()).hexdigest(),now()+timedelta(hours=1)))
    app=FastAPI();app.include_router(router)
    @app.exception_handler(DomainError)
    async def error(request,exc):return JSONResponse(dict(code=exc.code,details=clean(exc.details)),status_code=exc.status)
    result=dict(passed=False,checks=[],model_requests=0,object_requests=0,consumers_started=0,scope="Isolated nonownerPG + standalone actual metrics ASGI router; explicit SQL identities/reviews/outcomes fixtures, no live main/frontend or qualified official business result")
    def check(name,value,observation=None):result["checks"].append(dict(name=name,passed=bool(value),observation=observation))
    def h(tag=None,key=None):return {"Origin":os.environ["APP_ORIGIN"],"X-CSRF-Token":csrf,"Idempotency-Key":key or uuid4().hex,**({"If-Match":tag} if tag else {})}
    def payload(project,stage):return dict(submission_id=str(project["submission"]),stage=stage,source_type="user_reported",source_version_id=None,evidence_refs=[],announced_at=None,known_at=(project["submitted_at"]+timedelta(hours=6)).isoformat(),reason="Explicit independent synthetic business stage")
    stage="setup"
    try:
        with TestClient(app) as client:
            client.cookies.set("session",token)
            def metrics(projects,**params):
                response=client.get("/api/v1/metrics",params={"bid_ids":[str(p["bid"]) for p in projects],**params});assert response.status_code==200;return response.json()
            with connection() as c:
                p=helper.make_project(c,actor,visible=False)
                # A real stored scalar score is deliberately not a business stage.
                c.execute("INSERT INTO outcomes(id,bid_id,submission_id,score_kind,value,maximum,source_version_id,known_at,created_by) VALUES(%s,%s,%s,'technical_total',0,100,%s,clock_timestamp(),%s)",(uuid4(),p["bid"],p["submission"],p["official"],actor))
            endpoint=f"/api/v1/bids/{p['bid']}/submissions/{p['submission']}/business-results"
            empty=metrics([p]);check("stored_real_zero_score_does_not_infer_business_stage",empty["unknown_outcomes"]==1 and empty["known_outcomes"]==0 and empty["shortlist_rate"]["value"] is None and empty["win_rate"]["value"] is None)
            before=client.get(endpoint).json()
            invalid=client.post(endpoint,json=payload(p,"95"),headers=h())
            check("invalid_stage_no_partial_result_head",invalid.status_code==422 and client.get(endpoint).json()==before)
            tag=f'"business-result-head:{p["submission"]}:0"'
            create_key=uuid4().hex;body=payload(p,"cancelled")
            created=client.post(endpoint,json=body,headers=h(tag,create_key));assert created.status_code==201
            current=created.json();tag=created.headers["etag"]
            cancelled=metrics([p]);check("cancelled_has_known_stage_but_both_denominators_null",cancelled["known_outcomes"]==1 and cancelled["stage_counts"]["cancelled"]==1 and cancelled["shortlist_rate"]==dict(numerator=0,denominator=0,value=None) and cancelled["win_rate"]==dict(numerator=0,denominator=0,value=None))
            for next_stage,expected_short,expected_win in [("technical_shortlisted",dict(numerator=1,denominator=1,value="1"),dict(numerator=0,denominator=0,value=None)),("not_awarded",dict(numerator=1,denominator=1,value="1"),dict(numerator=0,denominator=1,value="0"))]:
                correction=payload(p,next_stage)|dict(base_result_id=current["id"],correction_reason="Independent stage correction")
                updated=client.post(f"/api/v1/business-results/{current['id']}/corrections",json=correction,headers=h(tag));assert updated.status_code==201
                current,tag=updated.json(),updated.headers["etag"]
                value=metrics([p]);check("stage_"+next_stage+"_uses_exact_distinct_denominators",value["shortlist_rate"]==expected_short and value["win_rate"]==expected_win)
            stage="conflict_and_temporal_current_head"
            conflicted=client.post(f"/api/v1/business-results/{current['id']}/verification-decisions",json=dict(state="conflict",reason="Independent unresolved contradiction"),headers=h(tag));assert conflicted.status_code==200
            tag=conflicted.headers["etag"]
            with connection() as c:cutoff=c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
            old_replay=client.post(endpoint,json=body,headers=h(f'"business-result-head:{p["submission"]}:0"',create_key))
            value=metrics([p]);verified_only=metrics([p],verification_scope="verified_only")
            check("exact_old_command_replay_cannot_roll_back_conflicted_current_head",old_replay.status_code==201 and old_replay.json()==created.json() and client.get(endpoint).json()["current_result"]["id"]==current["id"] and value["known_outcomes"]==0 and value["conflicted_outcomes"]==1 and value["win_rate"]["value"] is None and verified_only["win_rate"]["value"] is None)
            def correct(next_stage):return client.post(f"/api/v1/business-results/{current['id']}/corrections",json=payload(p,next_stage)|dict(base_result_id=current["id"],correction_reason="Independent simultaneous corrections"),headers=h(tag))
            with ThreadPoolExecutor(max_workers=2) as pool:parallel=list(pool.map(correct,["awarded","rejected"]))
            check("two_real_corrections_one_head_winner_and_one_cas_rejection",sorted(r.status_code for r in parallel)==[201,412] and client.get(endpoint).json()["head_revision"]==5)
            historical=metrics([p],as_of=cutoff.isoformat())
            check("as_of_uses_conflict_before_later_correction_without_old_stage_fallback",historical["conflicted_outcomes"]==1 and historical["known_outcomes"]==0 and historical["win_rate"]["value"] is None)
            with connection() as c:q=helper.make_project(c,actor,unit=p["unit"])
            duplicate=metrics([p,q]);check("two_effective_submission_heads_same_unit_are_both_excluded",duplicate["effective_submissions"]==0 and duplicate["scope"]["excluded_submission_counts"].get("identity_submission_conflict")==2 and len(duplicate["scope"]["identity_submission_conflicts"])==1 and duplicate["win_rate"]["value"] is None)
            stage="risk_cutoff_and_hidden_prediction"
            with connection() as c:
                risk=helper.make_project(c,actor,visible=False);review=helper.make_review(c,actor,risk)
                observed=snapshots.observe_review_risks(c,bid=risk["bid"],actor=actor,command=None)
                at=helper.now().fromisoformat(observed["observed_at"])
                late=dict(id=uuid4(),bid_id=risk["bid"],body_version_id=risk["body"],review_id=review["review"],submitted_at=at-timedelta(microseconds=1),evidence={"identity":risk["identity"]})
                timely=dict(late,id=uuid4(),submitted_at=at)
                late_snapshot=snapshots.freeze_submission_risks(c,submission=late,actor=actor,command=None)
                timely_snapshot=snapshots.freeze_submission_risks(c,submission=timely,actor=actor,command=None)
                c.execute("INSERT INTO submission_records(id,bid_id,body_version_id,review_id,kind,evidence,submitted_at,created_by,supersedes_id) VALUES(%s,%s,%s,%s,'offline_fact',%s,%s,%s,%s)",(timely["id"],risk["bid"],risk["body"],review["review"],Jsonb(clean(timely["evidence"]|dict(gate_checked=False,open_risk_snapshot=timely_snapshot))),at,actor,risk["submission"]))
                c.execute("UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",(timely["id"],risk["bid"]))
                c.execute("INSERT INTO predictions(id,bid_id,review_id,method_digest,mode,value,maximum,lower_bound,upper_bound,reasons) VALUES(%s,%s,%s,%s,'raw_uncalibrated',98.7654321,100,90.123456,99.999999,'[]')",(uuid4(),risk["bid"],review["review"],sha(b'fake-unqualified-prediction')))
            check("one_microsecond_after_submission_observation_cannot_backfill_known_zero",late_snapshot["basis"]=="unknown" and late_snapshot["open"] is None and late_snapshot["identified"] is None)
            check("exact_cutoff_immutable_risk_snapshot_records_actual_one_open_root",timely_snapshot["open"]==1 and timely_snapshot["identified"]==1 and timely_snapshot["basis"]=="reconstructed_from_audit")
            with connection() as c:
                decision=uuid4();c.execute("INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'finding',%s,1,'dismiss','Independent later reasoned dismissal','{}',%s)",(decision,risk["bid"],review["finding"],actor))
                c.execute("INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'finding',%s,1,%s)",(risk["bid"],review["finding"],decision))
                unchanged=snapshots.freeze_submission_risks(c,submission=timely,actor=actor,command=None)
            value=metrics([risk]);check("later_dismissal_changes_current_count_but_not_submission_snapshot",unchanged==timely_snapshot and value["qualification"]["dismissed"]==1 and value["qualification"]["resolved"]==0 and value["qualification"]["open_at_submission"]==1)
            trends=client.get(f"/api/v1/bids/{risk['bid']}/round-trends");assert trends.status_code==200
            points=trends.json()["items"]
            check("hidden_prediction_has_no_number_interval_or_error_derivatives",points and all(p["prediction"]==dict(mode="hidden",reasons=["project_prediction_hidden"]) for p in points) and all(marker not in json.dumps(trends.json()) for marker in ["98.7654321","90.123456","99.999999"]) and value["online_error"]["mae"] is None and value["online_error"]["bias"] is None and value["online_error"]["excluded_pair_counts"].get("prediction_hidden")==1)
            with connection() as c:
                unknown=helper.make_project(c,actor)
                before=c.execute("SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type=%s",(unknown["bid"],records.RISK)).fetchone()["n"]
            absent=metrics([unknown])
            with connection() as c:after=c.execute("SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type=%s",(unknown["bid"],records.RISK)).fetchone()["n"]
            check("GET_metrics_never_creates_missing_historical_risk_snapshot",before==after==0 and absent["qualification"]["unknown_snapshot_submissions"]==1 and absent["qualification"]["open_at_submission"] is None)
            verify();result["passed"]=all(c["passed"] for c in result["checks"])
    except Exception as error:result["fatal"]=dict(stage=stage,type=type(error).__name__,message="Independent metrics probe incomplete; no credentials or private response logged")
    result.update(known_independent_architect_blocker="Canonical candidate/future-known source may still grant verified/usable; separately retained architect counterexamples. This partial QA does not release module01.",setup_helper_scope="Frozen developer make_project/make_review only for SQL fixture setup; none of developer test functions or assertions executed",runtime_manifest_sha256=hashlib.sha256((root/"runtime-manifest.json").read_bytes()).hexdigest(),probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/"executed-probe.py").write_bytes(Path(__file__).read_bytes());(out/"result.json").write_text(json.dumps(clean(result),ensure_ascii=False,indent=2)+"\n")
    print(dict(passed=result["passed"],checks=len(result["checks"]),failed=[c["name"] for c in result["checks"] if not c["passed"]],fatal=result.get("fatal")))
    return 0 if result["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
