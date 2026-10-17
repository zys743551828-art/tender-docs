"""Independent actual BGE/PG staged authority, RR rejection and effectivity exclusions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api import rag_routes
from apps.api.main import app
from packages.domain.runtime import DomainError, assert_role, clean, connection, sha
from packages.domain.source_effectivity import EVENT
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp
from packages.rag.indexing import compute_index, prepare_index, publish_index


def main():
    root = Path(os.environ["QA_RAG04_ROOT"])
    out = root / os.environ.get("QA_RAG04_EXECUTION", "execution")
    out.mkdir(exist_ok=False)
    manifest = root / "input-manifest.json"
    entries = json.loads(manifest.read_text())
    assert Path(rag_routes.__file__).is_relative_to(root / "execution-inputs")
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    assert_role(); assert_role(True)

    def verify():
        assert all(hashlib.sha256((root/"execution-inputs"/e["path"]).read_bytes()).hexdigest()==e["sha256"] for e in entries)

    verify()
    result=dict(passed=False,checks=[],paid_generation_calls=0,real_kafka_consumers_started=0,scope="Frozen RAG04 + frozen main03 ASGI, actual restricted PG and actual local BGE/lexical/rerank HTTP; synthetic SQL parse/source-effectivity fixtures, not real PDF or business quality")

    def check(name,value,observation=None):
        result["checks"].append(dict(name=name,passed=bool(value),observation=observation))

    login,password="qa-rag04-"+uuid4().hex,secrets.token_urlsafe(30)
    bootstrap=subprocess.run([sys.executable,"-m","apps.api.bootstrap","--login",login,"--name","RAG04独立验证成员","--password-stdin"],input=password+"\n",text=True,capture_output=True,check=False)
    assert bootstrap.returncode==0
    tls=threading.local();observed=[];rpc=[];mode={"value":"normal"};final_pids=[]
    original_connection=rag_routes.connection
    original_auth=rag_routes.auth

    def tagged_auth(*args,**kwargs):
        actor=original_auth(*args,**kwargs)
        tls.number=0;tls.request_pids=[]
        return actor

    @contextmanager
    def instrumented_connection():
        number=getattr(tls,"number",0)+1;tls.number=number
        with original_connection() as c:
            selected=mode["value"]
            if selected=="initial_rr" and number==1 or selected=="final_rr" and number==4:
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            pid=c.execute("SELECT pg_backend_pid() AS id").fetchone()["id"]
            if number==4:final_pids.append(pid)
            observed.append(pid);tls.request_pids.append(pid);tls.depth=getattr(tls,"depth",0)+1
            try:yield c
            finally:tls.depth-=1

    class TrackedDense(DenseHttp):
        def _call(self,method,path,body=None):
            with original_connection() as c:
                present=c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE pid=ANY(%s)",(list(set(getattr(tls,"request_pids",[]))),)).fetchone()["n"]
            rpc.append(dict(path=path,request_connection_depth=getattr(tls,"depth",0),request_pids_present=present))
            assert getattr(tls,"depth",0)==0 and present==0
            return super()._call(method,path,body)

    def request(client,bid,context="body_evidence",key=None):
        tls.number=0
        response=client.post("/api/v1/retrievals",json=dict(bid_id=str(bid),context=context,query="围挡高度与排水沟宽度",limit=4),headers={"Origin":os.environ["APP_ORIGIN"],"X-CSRF-Token":csrf,"Idempotency-Key":key or uuid4().hex})
        result.setdefault("request_observations",[]).append(dict(stage=stage,status=response.status_code,code=response.json().get("code")))
        return response

    def counts(bid):
        with original_connection() as c:
            return c.execute("SELECT (SELECT count(*) FROM retrieval.retrieval_traces WHERE bid_id=%s) AS traces,(SELECT count(*) FROM commands WHERE target_id=%s AND command_kind='retrieve-evidence') AS commands",(bid,bid)).fetchone()

    def seed(client,suffix):
        response=client.post("/api/v1/bids",json=dict(title="RAG04独立事务-"+suffix),headers={"Origin":os.environ["APP_ORIGIN"],"X-CSRF-Token":csrf,"Idempotency-Key":uuid4().hex});assert response.status_code==201
        bid=UUID(response.json()["id"]);version,blob,parse,material=uuid4(),uuid4(),uuid4(),uuid4();evidence=[uuid4(),uuid4()]
        texts=["道路围挡高度不得低于2米。独立待核验条款。", "排水沟宽度应为0.6米。独立其他可用条款。"]
        with original_connection() as c:
            actor=c.execute("SELECT created_by FROM bids WHERE id=%s",(bid,)).fetchone()["created_by"]
            c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",(blob,sha(str(blob).encode()),"synthetic-sql-rag04/"+str(blob)))
            c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'SQL原页夹具非真实PDF.pdf',now()-interval '1 day','source_original',%s)",(version,bid,blob,actor))
            c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,2,'partial',%s)",(parse,bid,version,sha(b'qa-rag04-fixture-parser'),sha(str(parse).encode()),Jsonb(['Synthetic SQL fixture, no PDF accuracy claim'])))
            for page,(eid,text) in enumerate(zip(evidence,texts),1):
                c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,595,842,0,'usable','[]')",(bid,parse,page))
                c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,%s,0,'text',%s,%s)",(eid,bid,parse,page,text,sha(text.encode())))
            c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,sha(str(material).encode())))
            c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'body',0)",(material,bid,version))
            c.execute("UPDATE bid_profiles SET current_body_id=%s,current_material_id=%s WHERE bid_id=%s",(version,material,bid))
        dense=DenseHttp()
        with connection(True) as c:prepared=prepare_index(c,bid,parse,dense,LexicalHttp(dense))
        computed=compute_index(prepared,dense)
        with connection(True) as c:indexed=publish_index(c,computed)
        return dict(bid=bid,version=version,parse=parse,material=material,evidence=evidence,texts=texts,index=indexed,actor=actor)

    stage="setup"
    try:
        with TestClient(app) as client:
            logged=client.post("/api/v1/auth/login",json=dict(login_name=login,password=password),headers={"Origin":os.environ["APP_ORIGIN"]});assert logged.status_code==200
            csrf=logged.json()["csrf_token"]
            f=seed(client,"scope");bid=f["bid"];result["fixture_bid_id"]=str(bid)
            with patch.object(rag_routes,"connection",instrumented_connection),patch.object(rag_routes,"DenseHttp",TrackedDense),patch.object(rag_routes,"auth",tagged_auth):
                stage="reject_initial_rr"
                mode["value"]="initial_rr";before=counts(bid);rpc_before=len(rpc)
                rejected=request(client,bid)
                check("initial_RR_rejected_before_any_model_rpc_and_no_command_trace",rejected.status_code==409 and rejected.json()["code"]=="RAG_FRESH_AUTHORITY_REQUIRED" and len(rpc)==rpc_before and counts(bid)==before)
                stage="normal_staged_and_replay"
                mode["value"]="normal";key=uuid4().hex
                first=request(client,bid,key=key)
                assert first.status_code==200
                hits=first.json()["hits"]
                check("RC_actual_staged_models_return_exact_two_page_evidence",len(hits)==2 and {h["physical_page"] for h in hits}=={1,2} and all(h["excerpt"] in f["texts"][h["physical_page"]-1] and h["parse_run_id"]==str(f["parse"]) for h in hits))
                rpc_before=len(rpc);replayed=request(client,bid,key=key)
                check("unchanged_replay_uses_no_local_model_rpc",replayed.status_code==200 and replayed.json()==first.json() and replayed.headers.get("Idempotent-Replayed")=="true" and len(rpc)==rpc_before)
                mode["value"]="initial_rr";before=counts(bid);rpc_before=len(rpc)
                rejected=request(client,bid,key=key)
                check("initial_RR_exact_replay_rejected_before_rpc_or_publication",rejected.status_code==409 and rejected.json()["code"]=="RAG_FRESH_AUTHORITY_REQUIRED" and len(rpc)==rpc_before and counts(bid)==before)
                with original_connection() as c:
                    saved=c.execute("SELECT response FROM commands WHERE target_id=%s AND idempotency_key=%s AND command_kind='retrieve-evidence'",(bid,key)).fetchone()["response"]
                with original_connection() as c:
                    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    try:rag_routes.replay(c,dict(bid_id=str(bid),context="body_evidence",query="围挡高度与排水沟宽度",limit=4),bid,saved)
                    except DomainError as error:direct_rejected=error.code=="RAG_FRESH_AUTHORITY_REQUIRED"
                    else:direct_rejected=False
                check("direct_replay_helper_rejects_RR_before_authoritative_reads",direct_rejected)
                stage="reject_final_rr"
                mode["value"]="final_rr";before=counts(bid);rpc_before=len(rpc)
                rejected=request(client,bid)
                check("final_only_RR_rejected_after_model_work_without_command_or_trace",rejected.status_code==409 and rejected.json()["code"]=="RAG_FRESH_AUTHORITY_REQUIRED" and len(rpc)>rpc_before and counts(bid)==before)
                mode["value"]="normal"
                stage="effectivity_before_ranking"
                rule_set,rule=uuid4(),uuid4()
                with original_connection() as c:
                    c.execute("INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'partial')",(rule_set,bid,f["material"],sha(str(rule_set).encode())))
                    c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands,provenance_status) VALUES(%s,%s,%s,'mandatory',%s,'[]','[]',%s,'[]','needs_revalidation')",(rule,bid,rule_set,f["texts"][0],[f["evidence"][0]]))
                    c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s",(rule_set,bid))
                captured=[]
                class ObservedRerank(RerankHttp):
                    def rank(self,query,texts):
                        captured.extend(texts)
                        return super().rank(query,texts)
                with patch.object(rag_routes,"RerankHttp",ObservedRerank):pending=request(client,bid)
                check("pending_obligation_excluded_before_rerank_other_evidence_remains",pending.status_code==200 and len(pending.json()["hits"])==1 and pending.json()["hits"][0]["physical_page"]==2 and captured and all(f["texts"][0] not in text for text in captured) and any("候选阶段排除" in g for g in pending.json()["gaps"]))
                history=request(client,bid,"history")
                check("history_keeps_exact_original_two_pages_with_explicit_notice",history.status_code==200 and len(history.json()["hits"])==2 and any("历史原文" in g for g in history.json()["gaps"]))
                # Explicit synthetic effectivity record; not a claim that the legal
                # business decision command or source originals were executed.
                with original_connection() as c:
                    c.execute("INSERT INTO audit_entries(id,actor_id,bid_id,event_type,payload) VALUES(%s,%s,%s,%s,%s)",(uuid4(),f["actor"],bid,EVENT,Jsonb(dict(action="partial_replace",old_document_version_id=str(f["version"]),new_document_version_id=str(f["version"]),reason="Explicit independent SQL policy fixture",ended_rules=[dict(rule_id=str(rule),semantic_key=sha(b'ended-fixture'),evidence_ids=[str(f["evidence"][1])])]))))
                ended=request(client,bid)
                check("ended_and_pending_evidence_both_absent_from_current_candidates",ended.status_code==200 and ended.json()["hits"]==[] and ended.json()["coverage"]=="unavailable")
                old=request(client,bid,key=key)
                check("old_key_cannot_replay_before_new_effectivity_control",old.status_code==409 and old.json()["code"]=="RETRIEVAL_SOURCE_CHANGED")
                stage="concurrent_same_key"
                concurrent_key=uuid4().hex;before=counts(bid)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures=[pool.submit(request,client,bid,"history",concurrent_key) for _ in range(2)]
                    parallel=[future.result(120) for future in futures]
                after=counts(bid)
                check("concurrent_same_key_staged_requests_share_one_trace_command",all(r.status_code==200 for r in parallel) and parallel[0].json()==parallel[1].json() and after["traces"]==before["traces"]+1 and after["commands"]==before["commands"]+1)

            stage="RC_wait_graph_then_withdraw"
            race=seed(client,"graph-race");race_bid=race["bid"];ready=threading.Event();waited=[];failures=[];threads=[]
            def withdraw():
                try:
                    with original_connection() as c:
                        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
                        c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(race["index"]["original_source_id"],))
                        ready.set();until=time.monotonic()+10
                        while time.monotonic()<until:
                            row=c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted AND locktype='advisory' AND classid=90127 AND objid=1 AND pid=ANY(%s)) AS waiting",(list(final_pids),)).fetchone()
                            if row["waiting"]:waited.append(True);break
                            time.sleep(0.01)
                        else:failures.append("final_graph_wait_not_observed")
                except Exception:failures.append("withdraw_fixture_failed");ready.set()
            class WithdrawingRerank(RerankHttp):
                def rank(self,query,texts):
                    scores=super().rank(query,texts)
                    thread=threading.Thread(target=withdraw);threads.append(thread);thread.start();assert ready.wait(5)
                    return scores
            with patch.object(rag_routes,"connection",instrumented_connection),patch.object(rag_routes,"DenseHttp",TrackedDense),patch.object(rag_routes,"RerankHttp",WithdrawingRerank),patch.object(rag_routes,"auth",tagged_auth):
                before=counts(race_bid);raced=request(client,race_bid)
            for thread in threads:thread.join(12)
            after=counts(race_bid)
            with original_connection() as c:status=c.execute("SELECT status FROM retrieval.source_heads WHERE source_id=%s",(race["index"]["original_source_id"],)).fetchone()["status"]
            check("RC_final_rechecks_withdrawn_ancestor_after_real_graph_wait",bool(waited) and not failures and status=="withdrawn" and raced.status_code==409 and after==before,dict(actual_graph_wait=bool(waited),status=status,response_status=raced.status_code,error=raced.json().get("code"),counts_before=before,counts_after=after))
            check("all_endpoint_model_HTTP_occurs_after_request_connections_close",bool(rpc) and all(r["request_connection_depth"]==0 and r["request_pids_present"]==0 for r in rpc),dict(requests=len(rpc),paths=sorted(set(r["path"] for r in rpc))))
            verify();result["passed"]=all(c["passed"] for c in result["checks"])
            result["model_rpc_observations"]=rpc
    except Exception as error:
        result["fatal"]=dict(stage=stage,type=type(error).__name__,message="Independent RAG04 delta incomplete; no private token or provider request logged")
    result["model_rpc_observations"]=rpc
    result.update(candidate_sha256="d6057de89b507423f1849478d2e19e0c4f9bc1200ab1e8789b741a36ad73398c",input_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/"executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out/"result.json").write_text(json.dumps(clean(result),ensure_ascii=False,indent=2)+"\n")
    print(dict(passed=result["passed"],checks=len(result["checks"]),failed=[c["name"] for c in result["checks"] if not c["passed"]],fatal=result.get("fatal")))
    return 0 if result["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
