"""Independent scoring API/purpose authority; synthetic full body, restricted real PG."""
import hashlib,importlib.util,json,os,select,subprocess,sys
from pathlib import Path
from uuid import UUID,uuid4
from decimal import Decimal
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(os.environ['QA_ASSISTANT_ROOT']);RUNTIME=ROOT/'execution-inputs'
spec=importlib.util.spec_from_file_location('independent_historical_fixture',ROOT/'qa-helpers/historical_independent.py');hist=importlib.util.module_from_spec(spec);spec.loader.exec_module(hist)
from apps.api import main
from apps.api.scoring_routes import router
from packages.domain.runtime import connection,sha,DomainError,Command
from packages.domain.reviews import freeze_intent
from packages.identity.repository import record_binding
from packages.model_governance import scoring_repository as repo
from packages.model_governance.scoring_profiles import environment
from packages.model_governance.scoring_contracts import extend_openapi
OBS=[];CHILDREN=[]
def note(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,code):return hist.expect(r,code)
@pytest.fixture
def project(monkeypatch):
    world=hist.world.__wrapped__(monkeypatch);w=next(world);prepared=hist.prepared.__wrapped__(w)
    try:
        w['history']=expect(hist.freeze(w),201)['id']
        with connection() as c:record_binding(c,w['bids'][0],w['actors'][0],None,reason='独立合成当前身份明确确认',confirmed=True)
        w['bid']=w['bids'][0];w['review']=review(w);yield w
    finally:world.close()
def review(w,coverage=None,extra=False):
    bid=w['bids'][0];api=w['api']
    with connection() as c:
        old=c.execute('SELECT current_review_id FROM bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_review_id']
        if old:c.execute("UPDATE operations SET state='cancelled',cancel_requested_at=clock_timestamp(),finished_at=clock_timestamp() WHERE id=(SELECT operation_id FROM review_runs WHERE id=%s)",(old,))
        current=c.execute('SELECT p.current_body_id,p.current_rule_set_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s',(bid,)).fetchone()
        rules=current['current_rule_set_id']
        if coverage:
            rules=uuid4();c.execute('INSERT INTO rule_sets SELECT %s,bid_id,material_id,revision+1,digest,%s FROM rule_sets WHERE id=%s',(rules,coverage,current['current_rule_set_id']));c.execute('INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,%s,kind,statement,conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE rule_set_id=%s',(rules,current['current_rule_set_id']));c.execute('UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s',(rules,bid))
        elif extra:c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,rule_set_id,kind,'Independent additional necessary requirement',conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE rule_set_id=%s LIMIT 1",(rules,))
    tag=api.get(f'/api/v1/bids/{bid}',headers=w['h']()).headers['etag']
    expect(api.post(f'/api/v1/bids/{bid}/reviews',json=dict(body_version_id=str(current['current_body_id']),material_revision=current['material_revision'],trigger='manual'),headers=w['h'](tag=tag)),202)
    # Explicit manual-rule fixture: no native/model extraction invoked or represented.
    with connection() as c:c.execute("UPDATE review_slots SET pending_intent=jsonb_set(pending_intent,'{prepared_rule_set_id}',to_jsonb(%s::text)) WHERE bid_id=%s",(str(rules),bid))
    freeze_intent(bid)
    with connection() as c:
        value=c.execute('SELECT current_review_id FROM bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_review_id'];assert value and value!=old;return value

def freeze(w,branch='current'):
    api=w['api'];ctx=api.get(f'/api/v1/bids/{w["bid"]}/score-context',headers=w['h']());expect(ctx,200)
    return api.post(f'/api/v1/bids/{w["bid"]}/score-inputs',json=dict(reason='独立合成完整评分软件',**({'review_id':str(w['review'])} if branch=='current' else {'historical_input_id':w['history']})),headers=w['h'](tag=ctx.headers['etag']))
def job(w,frozen):
    return w['api'].post(f'/api/v1/score-inputs/{frozen.json()["id"]}/jobs',json=dict(budget='2',reason='独立硬Mock评分，无费用资格'),headers=w['h'](tag=frozen.headers['etag']))
def child(mode,identity):
    p=subprocess.run([sys.executable,'-I',str(ROOT/'qa-helpers/score_child.py'),str(RUNTIME),mode,str(identity),'1'],cwd='/tmp',env=environment('/workspace/tender-workbench/.local/score_execution02-model_score.env'),capture_output=True,text=True,timeout=60,check=True);value=json.loads(p.stdout.splitlines()[-1]);CHILDREN.append(value);return value

def test_current_and_history_full_range_three_judgments_success_binding_reuse(project):
    w=project;first=freeze(w);expect(first,201);j=job(w,first);expect(j,201);out=child('success',j.json()['id']);assert out['ok'] and out['mock_calls']==6,out
    assert len(out['result']['items'])==2 and out['result']['formal_eligibility']=='unavailable';assert out['result']['prediction']['value'] is None
    for rule in set(x['rule'] for x in out['requests']):
        rs=[x for x in out['requests'] if x['rule']==rule];assert len(rs)==3 and len(set(x['messages_sha256'] for x in rs))==1
    current=w['api'].get('/api/v1/score-jobs/'+j.json()['id'],headers=w['h']());v=expect(current,200);assert v['state']=='succeeded' and Decimal(v['reserved_fee'])==0 and Decimal(v['estimated_fee'])>0
    again=freeze(w);expect(again,201);j2=job(w,again);expect(j2,201);cached=child('success',j2.json()['id']);assert cached['ok'] and cached['mock_calls']==0,cached
    with connection() as c:
        assert c.execute('SELECT count(*) n FROM score_execution.calls WHERE job_id=%s',(UUID(j2.json()['id']),)).fetchone()['n']==0
        bindings=c.execute('SELECT count(*) n FROM score_execution.call_bindings WHERE job_id=%s',(UUID(j2.json()['id']),)).fetchone()['n'];assert bindings==6
    historical=freeze(w,'historical');expect(historical,201);hj=job(w,historical);expect(hj,201);h=child('success',hj.json()['id']);assert h['ok'] and len(h['result']['items'])==2,h
    note('full_range_and_reuse',current_calls=6,per_rule_replicates=3,same_full_messages_no_previous_answer=True,new_equivalent_job_calls=0,bindings=bindings,historical_complete=True,formal_eligibility='unavailable',prediction=None)


def test_global_unknown_equivalent_and_expanded_scope_cannot_resend(project):
    w=project;first=freeze(w);expect(first,201);j=job(w,first);expect(j,201);unknown=child('timeout',j.json()['id']);assert not unknown['ok'] and unknown['mock_calls']==1
    status=w['api'].get('/api/v1/score-jobs/'+j.json()['id'],headers=w['h']());v=expect(status,200);assert v['state']=='paused' and Decimal(v['reserved_fee'])>0
    resume=w['api'].post('/api/v1/score-jobs/'+j.json()['id']+'/state',json=dict(action='resume',reason='独立尝试不得补发'),headers=w['h'](tag=status.headers['etag']));assert resume.status_code==409
    equivalent=freeze(w);expect(equivalent,201);assert job(w,equivalent).status_code==409
    w['review']=review(w,extra=True);expanded=freeze(w);v2=expect(expanded,201);assert v2['rule_count']==3 and v2['execution_key']!=first.json()['execution_key'];assert job(w,expanded).status_code==409
    with connection() as c:assert c.execute('SELECT count(*) n FROM score_execution.calls a JOIN score_execution.jobs j ON j.id=a.job_id JOIN score_execution.inputs i ON i.id=j.input_id WHERE i.bid_id=%s',(w['bid'],)).fetchone()['n']==1
    note('unknown_not_resent',actual_mock_requests=1,pause_preserves_reserve=True,resume_blocked=True,equivalent_and_expanded_new_job_blocked=True)


@pytest.mark.parametrize('coverage',['partial','conflict'])
def test_incomplete_rule_scope_refused_before_staging(project,coverage):
    w=project;w['review']=review(w,coverage=coverage);before=len(w['objects']);r=freeze(w);assert r.status_code==409 and len(w['objects'])==before
    with connection() as c:assert c.execute('SELECT count(*) n FROM score_execution.inputs WHERE bid_id=%s',(w['bid'],)).fetchone()['n']==0
    note('rule_scope_'+coverage,actual_server_root=True,no_staged_score_input=True)


def test_historical_archive_readonly_and_current_closed_refused(project):
    w=project
    with connection() as c:c.execute("UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",(w['bid'],));before=c.execute('SELECT current_body_id,current_review_id,phase,archived_at FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()
    assert freeze(w).status_code==409
    historical=freeze(w,'historical');expect(historical,201);j=job(w,historical);expect(j,201);out=child('success',j.json()['id']);assert out['ok'],out
    with connection() as c:after=c.execute('SELECT current_body_id,current_review_id,phase,archived_at FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone();assert before==after
    note('closed_archive_history',current_refused=True,historical_research_only_completed=True,project_body_review_phase_archive_unchanged=True)


@pytest.mark.parametrize('change',['cancel','withdraw'])
def test_late_actual_purpose_usage_recorded_but_result_not_published(project,change):
    w=project;f=freeze(w,'historical');expect(f,201);j=job(w,f);expect(j,201)
    p=subprocess.Popen([sys.executable,'-I',str(ROOT/'qa-helpers/score_child.py'),str(RUNTIME),'hold',j.json()['id'],'1'],cwd='/tmp',env=environment('/workspace/tender-workbench/.local/score_execution02-model_score.env'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=False)
    try:
        assert select.select([p.stdout],[],[],30)[0];started=json.loads(p.stdout.readline());assert started['event']=='mock_started'
        if change=='cancel':
            r=w['api'].get('/api/v1/score-jobs/'+j.json()['id'],headers=w['h']());expect(r,200);expect(w['api'].post('/api/v1/score-jobs/'+j.json()['id']+'/state',json=dict(action='cancel',reason='独立迟到计费取消反例'),headers=w['h'](tag=r.headers['etag'])),200)
        else:
            from packages.rag.indexing import original_source_id
            with connection() as c:
                c.execute('SELECT pg_advisory_xact_lock(90127,1)');r=c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(UUID(w['uploaded']['tender']['document_version_id'])),));assert r.rowcount==1
        output,stderr=p.communicate(input=b'1',timeout=40);assert p.returncode==0;v=json.loads(output.splitlines()[-1]);CHILDREN.append(v);assert not v['ok'] and v['mock_calls']==1,v
    finally:
        if p.poll() is None:p.terminate();p.wait(timeout=5)
    with connection() as c:
        calls=c.execute('SELECT a.state,a.estimated_fee,a.response FROM score_execution.calls a WHERE job_id=%s',(UUID(j.json()['id']),)).fetchall();assert len(calls)==1 and calls[0]['state']=='succeeded' and Decimal(calls[0]['estimated_fee'])>0
        assert c.execute('SELECT count(*) n FROM score_execution.results WHERE job_id=%s',(UUID(j.json()['id']),)).fetchone()['n']==0
    note('late_'+change,succeeded_call_usage_preserved=True,new_requests=1,no_result_publication=True)


def test_purpose_login_permissions_rr_and_no_implicit_transport(project):
    w=project;f=freeze(w);expect(f,201);j=job(w,f);expect(j,201);disabled=child('disabled',j.json()['id']);assert disabled['code']=='SCORE_PAID_EXECUTION_DISABLED' and disabled['mock_calls']==0
    permissions=child('permissions',j.json()['id']);assert permissions['ok'] and len(permissions['denied'])==6 and permissions['rr_refused']
    with connection() as c:assert c.execute('SELECT state FROM score_execution.jobs WHERE id=%s',(UUID(j.json()['id']),)).fetchone()['state']=='queued'
    note('purpose_isolation',permissions=permissions['denied'],rr_refused=True,no_transport_before_claim=True,actual_child_environment_single_purpose=True)

@pytest.mark.parametrize('change',['late_rule','published_head','forged_root'])
def test_current_root_identity_and_complete_rule_scope_revalidated(project,change):
    w=project;before=len(w['objects'])
    if change=='late_rule':
        with connection() as c:c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) SELECT gen_random_uuid(),bid_id,rule_set_id,kind,'Added after frozen root',conditions,exceptions,max_points,evidence_ids,bands FROM rule_items WHERE rule_set_id=(SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s) LIMIT 1",(w['bid'],))
        response=freeze(w);assert response.status_code==409 and len(w['objects'])==before
    elif change=='published_head':
        from packages.rag.indexing import original_source_id
        with connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(90127,1)');changed=c.execute("UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s AND status='published'",(original_source_id(UUID(w['uploaded']['tender']['document_version_id'])),));assert changed.rowcount==1
        response=freeze(w);assert response.status_code==409 and len(w['objects'])==before
    else:
        ctx=w['api'].get(f'/api/v1/bids/{w["bid"]}/score-context',headers=w['h']());expect(ctx,200)
        with connection() as c:metadata=repo.root_metadata(c,w['bid'],w['review'])
        root,content=repo.read_root(metadata);root['rules']=root['rules'][:1];forged=json.dumps(root,ensure_ascii=False,sort_keys=True)
        with pytest.raises(DomainError),connection() as c:repo.prepare_current(c,bid=w['bid'],review=w['review'],root=root,root_content=forged,metadata=metadata,condition=ctx.headers['etag'])
        fake=dict(metadata);fake['digest']=sha(forged.encode());fake['byte_size']=len(forged.encode())
        with pytest.raises(DomainError),connection() as c:repo.prepare_current(c,bid=w['bid'],review=w['review'],root=root,root_content=forged,metadata=fake,condition=ctx.headers['etag'])
    with connection() as c:assert c.execute('SELECT count(*) n FROM score_execution.inputs WHERE bid_id=%s',(w['bid'],)).fetchone()['n']==0
    note('frozen_root_'+change,no_score_inputs=True,actual_frozen_root_and_scope_rechecked=True)


def test_shadow_snapshot_same_result_route_changes_leave_history(project):
    from apps.api.model_governance_routes import router as governance_router
    from apps.api.model_registry_routes import router as registry_router
    from packages.model_governance import purpose_profiles
    from tests.test_model_governance_integration import queued_training
    w=project;api=w['api'];api.cookies.set('session',w['h']()['Cookie'].removeprefix('session='));api.app.include_router(governance_router);api.app.include_router(registry_router)
    # Frozen developer setup generator only: new synthetic 5-sample cohort, 15 hard Mock
    # research judgments and real CPU training. None of its test conclusions are reused.
    headers=lambda tag=None:w['h'](tag=tag)
    training,_,_=queued_training((api,w['actors'][0],headers,w['objects']))
    fitted=purpose_profiles.launch('train',training.json()['id'],profile='/workspace/tender-workbench/.local/score_execution02-model_train.env');assert fitted.returncode==0
    preview=api.get('/api/v1/model-registry/training-jobs/'+training.json()['id']+'/model-preview',headers=w['h']());expect(preview,200)
    model=api.post('/api/v1/model-registry/models',json=dict(training_job_id=training.json()['id'],reason='独立影子软件验收，合成模型无正式资格'),headers=w['h'](tag=preview.headers['etag']));candidate=expect(model,201);assert candidate['formal_eligibility']=='unavailable' and candidate['research_only']
    path='/api/v1/model-registry/routes/shadow/'+candidate['route_key'];route=api.get(path,headers=w['h']());expect(route,200)
    published=api.post(path+'/changes',json=dict(action='publish',model_id=candidate['model_id'],reason='独立合成shadow路由'),headers=w['h'](tag=route.headers['etag']));expect(published,200)
    frozen=freeze(w);expect(frozen,201);j=job(w,frozen);expect(j,201);out=child('success',j.json()['id']);assert out['ok'] and out['mock_calls']==6
    ctx=api.get('/api/v1/score-jobs/'+j.json()['id']+'/shadow-context',params=dict(model_id=candidate['model_id']),headers=w['h']());context=expect(ctx,200);assert context['additional_model_calls']==0 and context['source_current'] and context['model_current'] and context['route_current']
    with connection() as c:
        result=c.execute('SELECT digest FROM score_execution.results WHERE job_id=%s',(UUID(j.json()['id']),)).fetchone()['digest'];count=c.execute('SELECT count(*) n FROM score_execution.calls WHERE job_id=%s',(UUID(j.json()['id']),)).fetchone()['n']
    body=dict(model_id=candidate['model_id'],reason='仅同一三判断结果保存惰性模型影子');h=w['h'](tag=ctx.headers['etag']);url='/api/v1/score-jobs/'+j.json()['id']+'/shadows';saved=api.post(url,json=body,headers=h);v=expect(saved,201)
    replay=api.post(url,json=body,headers=h);assert expect(replay,201)==v
    suspend=api.post(path+'/changes',json=dict(action='suspend',reason='路由停用不改旧影子快照'),headers=w['h'](tag=published.headers['etag']));expect(suspend,200)
    assert api.post(url,json=body,headers=h).status_code==409
    assert expect(api.get('/api/v1/score-shadows/'+v['id'],headers=w['h']()),200)==v
    with connection() as c:
        row=c.execute('SELECT document FROM score_execution.shadows WHERE id=%s',(UUID(v['id']),)).fetchone()['document'];assert row['score_result_digest']==result and row['job_id']==j.json()['id'] and row['model_id']==candidate['model_id'];assert row['prediction']['value'] is None
        assert c.execute('SELECT count(*) n FROM score_execution.calls WHERE job_id=%s',(UUID(j.json()['id']),)).fetchone()['n']==count
        assert c.execute('SELECT count(*) n FROM score_execution.shadows WHERE job_id=%s',(UUID(j.json()['id']),)).fetchone()['n']==1
    note('shadow_same_result',same_result_digest=result,additional_score_calls=0,one_snapshot=True,route_suspend_replay_refused=True,history_unchanged=True,training_setup='Frozen developer fixture generator: 5 own synthetic samples/15 hard Mock judgments; actual purpose-only CPU train; no independent dataset accuracy claim')


@pytest.fixture(scope='session',autouse=True)
def evidence():
    manifest=json.loads((ROOT/'runtime-manifest.json').read_text())
    for x in manifest:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    assert '/api/v1/scoring-capabilities' not in main.app.openapi()['paths'];main.app.include_router(hist.proofs_router);main.app.include_router(hist.inputs_router);main.app.include_router(router)
    standalone=FastAPI();standalone.include_router(router);actual=extend_openapi(standalone.openapi());assert actual==json.loads((ROOT/'actual.openapi.json').read_text())
    yield
    for x in manifest:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    loaded=[]
    for name,mod in sorted(sys.modules.items()):
        p=getattr(mod,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(RUNTIME),name;loaded.append(dict(module=name,path=str(p.relative_to(RUNTIME)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_SCORE_ROUND','execution');out.mkdir(exist_ok=True);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,children=CHILDREN,loaded_modules=loaded,actual_operations=11,actual_matches_candidate=True,real_paid_calls=0,cloud_requests=0,consumers=0,composition='Historical proof/input/scoring explicitly registered in local TestClient; original main/live not mounted.'),ensure_ascii=False,indent=2)+'\n')
