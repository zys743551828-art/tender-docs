"""Independent source-time/replay/graph-race assertions; SQL fixture factory only."""
import hashlib
import importlib.util
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from packages.domain.runtime import DomainError,assert_role,clean,connection,now,secret_hash,sha
from packages.metrics import records,sources
from packages.rag.indexing import original_source_id


def main():
    root=Path(os.environ['QA_METRICS_ROOT']);out=root/'delta-execution';out.mkdir(exist_ok=False)
    rows=json.loads((root/'runtime-manifest.json').read_text())
    def verify():assert all(hashlib.sha256((root/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in rows)
    verify();assert_role();assert_role(True);assert Path(os.environ['WORKBENCH_ENV_FILE']).name=='rag-checks.env'
    spec=importlib.util.spec_from_file_location('qa_source_fixture_only',root/'execution-inputs/tests/test_metrics_integration.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    actor,token,csrf=uuid4(),secrets.token_hex(32),secrets.token_hex(24)
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立FR01103来源反例')",(actor,))
        c.execute('INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)',(uuid4(),actor,secret_hash(token),hashlib.sha256(csrf.encode()).hexdigest(),now()+timedelta(hours=1)))
    from apps.api.metrics_routes import router
    app=FastAPI();app.include_router(router)
    @app.exception_handler(DomainError)
    async def error(request,e):return JSONResponse(dict(code=e.code,details=clean(e.details)),status_code=e.status)
    result=dict(passed=False,checks=[],model_requests=0,object_requests=0,consumers_started=0,scope='Actual restrictedPG and standalone metrics ASGI, own synthetic SQL fixtures; developer fixture functions only, no developer tests/assertions executed')
    def check(name,value,observation=None):result['checks'].append(dict(name=name,passed=bool(value),observation=observation))
    def header(tag=None,key=None):return {'Origin':os.environ['APP_ORIGIN'],'X-CSRF-Token':csrf,'Idempotency-Key':key or uuid4().hex,**({'If-Match':tag} if tag else {})}
    def seed(**kwargs):
        with connection() as c:return helper.make_project(c,actor,**kwargs)
    def payload(p,known=None):return dict(submission_id=str(p['submission']),stage='awarded',source_type='official',source_version_id=str(p['official']),evidence_refs=[p['refs'][str(p['official'])]],announced_at=(known or p['submitted_at']+timedelta(hours=2)).isoformat(),known_at=(known or p['submitted_at']+timedelta(hours=2)).isoformat(),reason='Synthetic explicit awarded business fact, independent of any technical score')
    def verification(p):return dict(state='verified',reason='独立核对合成官方原页',identity_confirmed=True,evidence_refs=[p['refs'][str(p['official'])]])
    stage='setup'
    try:
      with TestClient(app) as client:
        client.cookies.set('session',token)
        def create(p,known=None):
            body=payload(p,known);path=f"/api/v1/bids/{p['bid']}/submissions/{p['submission']}/business-results";r=client.post(path,json=body,headers=header(f'"business-result-head:{p["submission"]}:0"'));assert r.status_code==201,r.json();return r
        def confirm(p,created,key=None):return client.post(f"/api/v1/business-results/{created.json()['id']}/verification-decisions",json=verification(p),headers=header(created.headers['etag'],key))
        def metric(p,**params):
            r=client.get('/api/v1/metrics',params={'bid_ids':str(p['bid']),'verification_scope':'verified_only',**params});assert r.status_code==200;return r.json()
        stage='historical_business_current_publication'
        ancient=datetime(2019,1,1,tzinfo=timezone.utc);p=seed(source_at=ancient,submitted_at=ancient+timedelta(days=1));created=create(p);vk=uuid4().hex;approved=confirm(p,created,vk);assert approved.status_code==200,approved.json()
        current=metric(p);check('2019_business_knowledge_and_new_system_published_head_qualify_now',approved.json()['verification']=='verified' and approved.json()['usable'] and current['win_rate']==dict(numerator=1,denominator=1,value='1'))
        historical=metric(p,as_of=(ancient+timedelta(days=10)).isoformat())
        with connection() as c:
            auth,reasons=sources.inspect(c,p['bid'],p['official'],as_of=ancient+timedelta(days=10),cutoff=payload(p)['known_at'],refs=verification(p)['evidence_refs'])
        check('new_system_head_does_not_fabricate_historical_system_qualification',auth is None and 'source_no_longer_effective' in reasons and historical['win_rate']['value'] is None)
        stage='candidate_and_future_regressions'
        for case in ['candidate','future_known','after_business_known_cutoff']:
            kwargs={'source_at':now()+timedelta(days=1)} if case=='future_known' else {'source_at':now()-timedelta(hours=1)} if case=='after_business_known_cutoff' else {}
            q=seed(**kwargs);rr=create(q)
            if case=='candidate':
                with connection() as c:c.execute("UPDATE retrieval.source_heads SET status='candidate',revision=revision+1 WHERE source_id=%s",(original_source_id(q['official']),))
            refused=confirm(q,rr);projected=metric(q)
            with connection() as c:n=c.execute("SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type=%s",(q['bid'],records.VERIFY)).fetchone()['n']
            check(case+'_cannot_gain_verified_authority_or_verified_rate',refused.status_code==409 and refused.json()['code']=='BUSINESS_RESULT_SOURCE_INVALID' and n==0 and projected['win_rate']['denominator']==0 and projected['win_rate']['value'] is None,dict(status=refused.status_code,reasons=refused.json().get('details')))
        stage='same_key_replay_reprojects_authority'
        with connection() as c:c.execute('UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s',(original_source_id(p['official']),))
        replay=confirm(p,created,vk);projected=metric(p)
        check('same_key_verified_replay_preserves_identity_but_cannot_restore_old_authority',replay.status_code==200 and replay.headers.get('Idempotent-Replayed')=='true' and replay.json()['id']==approved.json()['id'] and not replay.json()['usable'] and 'source_authority_changed' in replay.json()['unusable_reasons'] and projected['win_rate']['value'] is None)
        # Same source can be explicitly rechecked after a benign head revision, without changing history.
        refreshed=client.get('/api/v1/business-results/'+approved.json()['id']);reauthorized=confirm(p,refreshed)
        check('explicit_fresh_verification_restores_v2_authority_after_head_revision',reauthorized.status_code==200 and reauthorized.json()['usable'] and metric(p)['win_rate']['value']=='1')
        stage='graph_lock_concurrent_withdrawal'
        race=seed();rc=create(race)
        with connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(90127,1)');holder=c.info.backend_pid
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(confirm,race,rc);waiting=False
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    with connection() as observer:
                        blocked=observer.execute("SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND wait_event='advisory' AND %s=ANY(pg_blocking_pids(pid))",(holder,)).fetchall()
                    if blocked:waiting=True;break
                    threading.Event().wait(.025)
                assert waiting,'actual verify did not reach graph lock wait'
                c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(race['official']),));c.commit();raced=future.result(10)
        with connection() as c:n=c.execute('SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type=%s',(race['bid'],records.VERIFY)).fetchone()['n']
        check('actual_graph_lock_wait_sees_committed_withdrawal_without_partial_verified_event',waiting and raced.status_code==409 and raced.json()['code']=='BUSINESS_RESULT_SOURCE_INVALID' and n==0 and metric(race)['win_rate']['value'] is None,dict(wait_observed=waiting,status=raced.status_code))
        stage='ancestor_authority'
        child=seed()
        with connection() as c:parent=helper.add_official_parent(c,child,actor,'published')
        cr=create(child);cv=confirm(child,cr);assert cv.status_code==200,cv.json()
        with connection() as c:c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(parent,))
        child_get=client.get('/api/v1/business-results/'+cr.json()['id']).json()
        check('active_canonical_child_cannot_ignore_withdrawn_official_ancestor',not child_get['usable'] and 'source_no_longer_effective' in child_get['unusable_reasons'] and metric(child)['win_rate']['value'] is None)
        stage='old_protocol_authority'
        old=seed();old_result=create(old)
        with connection() as c:
            authority,reasons=sources.inspect(c,old['bid'],old['official'],as_of=now(),cutoff=payload(old)['known_at'],refs=verification(old)['evidence_refs']);assert not reasons
            authority['snapshot']['schema']='official-source-authority/v1';authority['digest']=sha(authority['snapshot'])
            records.append(c,bid=old['bid'],actor=actor,command=None,event=records.VERIFY,payload=dict(submission_id=str(old['submission']),result_id=old_result.json()['id'],head_revision=2,state='verified',details=verification(old),source_authority=authority))
        old_current=client.get('/api/v1/business-results/'+old_result.json()['id']);old_metrics=metric(old)
        check('old_v1_historical_verified_event_is_preserved_but_not_granted_v2_usability',old_current.json()['verification']=='verified' and not old_current.json()['usable'] and 'source_authority_changed' in old_current.json()['unusable_reasons'] and old_metrics['win_rate']['value'] is None)
        new_verify=confirm(old,old_current)
        check('explicit_v2_reverification_can_qualify_without_rewriting_old_event',new_verify.status_code==200 and new_verify.json()['usable'] and metric(old)['win_rate']['value']=='1')
        result['passed']=all(x['passed'] for x in result['checks'])
    except Exception as error:
        result['fatal']=dict(stage=stage,type=type(error).__name__,message='Independent source delta incomplete; no private data logged')
    verify();result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest(),fixture_helper_scope='Only frozen make_project/add_official_parent SQL factories; no developer test or assertion invoked')
    (out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
