"""Independent frozen07 actual guards/shared registration. No consumer/provider network."""
import importlib.util
import io
import json
import os
import sys
import time
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

ROOT=Path(os.environ['QA_ASSISTANT_ROOT'])
spec=importlib.util.spec_from_file_location('independent_assistant_fixture',ROOT/'qa-helpers/assistant_fixture.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from apps.api import main
from apps.api.lifecycle_routes import router as lifecycle_router
from apps.api.views import bid_etag, upload_row
from apps.worker import engine
from packages.domain import storage, model_calls, reviews
from packages.domain.runtime import Command, DomainError, connection, enqueue, sha, etag
from packages.lifecycle import service
from packages.tasks import inputs

OBS=[]
def record(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,status):return helper.expect(r,status)


@pytest.fixture
def s(monkeypatch):
    gen=helper.s.__wrapped__(monkeypatch);s=next(gen)
    monkeypatch.setenv('OBJECT_STORE_BACKEND','s3')
    monkeypatch.setattr(storage,'sign',lambda *args,**kwargs:'http://memory.invalid/explicit-offline-grant')
    monkeypatch.setattr(main,'configured_parser',lambda:None)
    class Memory:
        def head_object(self,**kwargs):
            raw=s['objects'][kwargs['Key']];return dict(ContentLength=len(raw),Metadata=dict(sha256=sha(raw)))
        def get_object(self,**kwargs):
            raw=s['objects'][kwargs['Key']];return dict(ContentLength=len(raw),Body=io.BytesIO(raw))
    monkeypatch.setattr(storage,'client',lambda:Memory())
    with TestClient(main.app) as api:
        s['api']=api
        yield s
    try:next(gen)
    except StopIteration:pass


def lifecycle(s,action,bid=None):
    bid=bid or s['bids'][0];base=f'/api/v1/bids/{bid}/lifecycle';current=s['api'].get(base,headers=s['h']());expect(current,200)
    r=s['api'].post(base,json=dict(action=action,reason='独立QA明确生命周期动作'),headers=s['h'](tag=current.headers['etag']));return r


def close_batch(s,batch):
    base=f'/api/v1/bids/{s["bids"][0]}/upload-batches/{batch}'
    current=s['api'].get(base+'/closure-state',headers=s['h']());expect(current,200)
    return s['api'].post(base+'/close',json=dict(reason='结束原意图，保留本次合成字节'),headers=s['h'](tag=current.headers['etag']))


def new_batch(s,bid=None,h=None):
    return s['api'].post(f'/api/v1/bids/{bid or s["bids"][0]}/upload-batches',json={},headers=h or s['h']())


def upload(s):
    bid=s['bids'][0];batch=expect(new_batch(s),201)['id'];raw=(ROOT/'external-inputs/synthetic-body.pdf').read_bytes()
    body=dict(batch_id=batch,filename='独立恢复原件.pdf',role='body',declared_sha256=sha(raw),byte_size=len(raw),target=dict(kind='new_file'))
    grant=s['api'].post(f'/api/v1/bids/{bid}/uploads',json=body,headers=s['h']());v=expect(grant,200);identity=UUID(v['upload_id'])
    with connection() as c:u=upload_row(c,identity)
    s['objects'][u['staging_key']]=raw
    return batch,identity,u,body


def verified(s):
    batch,identity,u,body=upload(s);h=s['h']();complete_body=dict(parts=[],declared_sha256=u['declared_digest'])
    complete=s['api'].post(f'/api/v1/uploads/{identity}/complete',json=complete_body,headers=h);v=expect(complete,202)
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['payload']
    lease=engine.acquire(m);assert lease;computed=engine.run_compute(m,lease);assert engine.finish(m,lease,computed)
    with connection() as c:row=upload_row(c,identity);assert row['state']=='verified'
    return batch,identity,row,complete_body,h,v


def test_archive_waiting_producer_sees_current_closed_state(s):
    bid=s['bids'][0];actor=s['actors'][0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection() as holder:
            service.lock(holder,bid);cmd=Command(holder,actor,'qa-concurrent-archive',bid,uuid4().hex,{})
            service.change(holder,bid,actor,'terminate','明确结束本轮',cmd.id);service.change(holder,bid,actor,'archive','没有待处理意图，明确归档',cmd.id);cmd.finish({'fixture':'closed and archived'})
            pid=holder.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'];future=pool.submit(new_batch,s)
            waited=False
            for _ in range(100):
                with connection() as observer:waited=observer.execute('SELECT EXISTS(SELECT FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))) AS waited',(pid,)).fetchone()['waited']
                if waited:break
                time.sleep(.02)
            assert waited and not future.done()
        rejected=future.result(timeout=10)
    assert rejected.status_code==409 and rejected.json()['code']=='PROJECT_WORK_CLOSED'
    with connection() as c:
        assert service.profile(c,bid)['archived_at'] is not None
        assert c.execute('SELECT count(*) AS n FROM upload_batches WHERE bid_id=%s',(bid,)).fetchone()['n']==0
    record('archive_then_waiting_actual_api_producer',real_pg_blocking_observed=True,http_status=409,new_batches=0)


def test_producer_then_archive_preserves_old_ack_and_intent_blocker(s):
    bid=s['bids'][0];old_h=s['h']();first=new_batch(s,h=old_h);batch=expect(first,201)['id'];expect(lifecycle(s,'terminate'),200)
    blocked=lifecycle(s,'archive');assert blocked.status_code==409 and blocked.json()['code']=='PROJECT_UPLOAD_INTENTS_UNFINISHED'
    expect(close_batch(s,batch),200);expect(lifecycle(s,'archive'),200)
    replay=new_batch(s,h=old_h);assert expect(replay,201)==first.json() and replay.headers['Idempotent-Replayed']=='true'
    fresh=new_batch(s);assert fresh.status_code==409 and fresh.json()['code']=='PROJECT_WORK_CLOSED'
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM upload_batches WHERE bid_id=%s',(bid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) AS n FROM upload_batch_closures WHERE batch_id=%s',(UUID(batch),)).fetchone()['n']==1
    record('producer_before_archive',unfinished_intent_blocks_archive=True,explicit_close_retains_batch=True,old_ack_replays_no_new_effect=True)


@pytest.mark.parametrize('isolation',['REPEATABLE READ','SERIALIZABLE'])
def test_noncurrent_snapshot_cannot_enter_work_mutation(s,isolation):
    from packages.domain.work_access import begin
    with connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL '+isolation);c.execute('SELECT 1')
        with pytest.raises(DomainError) as e:begin(c,s['actors'][0],'qa-old-snapshot',s['bids'][0],uuid4().hex,{},None,bid=s['bids'][0])
    assert e.value.code=='CURRENT_AUTHORITY_REQUIRES_READ_COMMITTED'
    record('snapshot_'+isolation,error=e.value.code)


@pytest.mark.parametrize('started',[False,True])
def test_worker_finish_after_termination_cannot_publish(s,started):
    batch,identity,u,body=upload(s);complete_body=dict(parts=[],declared_sha256=u['declared_digest'])
    accepted=s['api'].post(f'/api/v1/uploads/{identity}/complete',json=complete_body,headers=s['h']());v=expect(accepted,202)
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['payload']
    lease=engine.acquire(m) if started else None;out=engine.run_compute(m,lease) if started else None
    expect(lifecycle(s,'terminate'),200)
    if started:assert engine.finish(m,lease,out)
    else:assert engine.acquire(m) is None
    with connection() as c:
        row=upload_row(c,identity);assert row['verified_version_id'] is None
        assert c.execute('SELECT state FROM operations WHERE id=%s',(UUID(v['operation']['id']),)).fetchone()['state']=='failed'
        receipts=c.execute('SELECT result FROM effect_receipts WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchall()
        assert receipts==([dict(result=dict(error_code='PROJECT_WORK_CLOSED',result_ref=None))] if started else [])
        assert c.execute('SELECT count(*) AS n FROM document_versions WHERE bid_id=%s',(s['bids'][0],)).fetchone()['n']==0
    record('late_worker_started' if started else 'worker_acquire_after_close',new_document_versions=0,business_effects=0,failure_receipts=int(started),operation_state='failed',actual_memory_verify_compute=started)


def test_closed_batch_reuse_is_new_identity_not_old_complete_or_commit(s):
    bid=s['bids'][0];batch,identity,u,body,old_h,accepted=verified(s);expect(close_batch(s,batch),200)
    # Completed ACK is still the original historical command; a new completion is forbidden.
    replay=s['api'].post(f'/api/v1/uploads/{identity}/complete',json=body,headers=old_h);assert expect(replay,202)==accepted and replay.headers['Idempotent-Replayed']=='true'
    denied=s['api'].post(f'/api/v1/uploads/{identity}/complete',json=body,headers=s['h']());assert denied.status_code==409 and denied.json()['code']=='BATCH_EXPLICITLY_CLOSED'
    with connection() as c:tag=bid_etag(c,bid)
    commit=dict(accepted_upload_ids=[str(identity)],accepted_binding_ids=[str(u['current_binding_id'])],explicit_partial_acceptance=False)
    old_commit=s['api'].post(f'/api/v1/bids/{bid}/upload-batches/{batch}/commit',json=commit,headers=s['h'](tag=tag));assert old_commit.status_code==409 and old_commit.json()['code']=='BATCH_EXPLICITLY_CLOSED'
    new=expect(new_batch(s),201)['id'];reuse_body=dict(source_upload_id=str(identity),target=dict(kind='new_file'),reason='明确选择同一合成字节进入全新批次');rh=s['h'](tag=etag('upload',identity,u['revision']))
    r=s['api'].post(f'/api/v1/bids/{bid}/upload-batches/{new}/retained-uploads',json=reuse_body,headers=rh);newu=expect(r,201);assert newu['id']!=str(identity) and newu['document_version_id']!=str(u['verified_version_id'])
    assert expect(s['api'].post(f'/api/v1/bids/{bid}/upload-batches/{new}/retained-uploads',json=reuse_body,headers=rh),201)==newu
    with connection() as c:
        created=upload_row(c,UUID(newu['id']));assert created['batch_id']==UUID(new) and created['current_binding_id']!=u['current_binding_id']
        blobs=c.execute('SELECT blob_id FROM document_versions WHERE id=ANY(%s)',([u['verified_version_id'],created['verified_version_id']],)).fetchall();assert len(blobs)==2 and blobs[0]['blob_id']==blobs[1]['blob_id']
        assert upload_row(c,identity)['batch_id']==UUID(batch)
        tag=bid_etag(c,bid)
    newcommit=dict(accepted_upload_ids=[newu['id']],accepted_binding_ids=[str(created['current_binding_id'])],explicit_partial_acceptance=False)
    committed=s['api'].post(f'/api/v1/bids/{bid}/upload-batches/{new}/commit',json=newcommit,headers=s['h'](tag=tag));expect(committed,202)
    with connection() as c:
        assert upload_row(c,identity)['committed_binding_id'] is None
        assert upload_row(c,UUID(newu['id']))['committed_binding_id']==created['current_binding_id']
    record('verified_closed_batch_explicit_reselection',old_complete_ack_only=True,old_complete_and_commit_new_command_rejected=True,fresh_upload_version_binding=True,same_immutable_blob=True,new_batch_actual_commit=True)


def test_interim_multipart_ack_does_not_reauthorize_after_close(s,monkeypatch):
    from packages.uploads import service as uploads
    from packages.uploads.protocol import pack
    batch,identity,u,body=upload(s)
    with connection() as c:
        c.execute("UPDATE upload_transfers SET mode='multipart',provider_upload_id=%s WHERE upload_id=%s",(pack('ready',key=u['staging_key'],nonce=str(uuid4()),provider_id='explicit-offline'),identity))
    remote=[]
    class Uncertain:
        def verify_complete(self,row):remote.append('verify');raise DomainError('OFFLINE_COMPLETION_UNCERTAIN','显式离线不确定完成',503)
    monkeypatch.setattr(uploads,'NativeMultipart',Uncertain)
    body=dict(declared_sha256=u['declared_digest'],parts=[dict(part_number=1,byte_size=u['declared_size'],etag='offline-part')]);h=s['h'](tag=etag('upload',identity,1));url=f'/api/v1/uploads/{identity}/complete'
    first=s['api'].post(url,json=body,headers=h);assert first.status_code==503 and len(remote)==1
    with connection() as c:
        row=upload_row(c,identity);assert row['state']=='uploaded'
        saved=c.execute("SELECT response FROM commands WHERE target_id=%s AND command_kind='complete_upload'",(identity,)).fetchone()['response'];assert uploads.PENDING in saved['headers']
    expect(lifecycle(s,'terminate'),200)
    again=s['api'].post(url,json=body,headers=h);assert again.status_code==409 and again.json()['code']=='PROJECT_WORK_CLOSED' and len(remote)==1
    with connection() as c:assert c.execute('SELECT count(*) AS n FROM operations WHERE bid_id=%s',(s['bids'][0],)).fetchone()['n']==0
    record('multipart_interim_ack_closed',explicit_fault_injection=True,native_mock_invocations_before=1,native_mock_invocations_after=1,no_verify_queue=True)


def test_shared_registered_assistant_readonly_archive_and_authoring_block(s):
    bid=s['bids'][0];expect(lifecycle(s,'terminate'),200);expect(lifecycle(s,'archive'),200)
    assert 'assistant/1' in engine.ORDINARY_HANDLERS and 'authoring-generate/1' in engine.ORDINARY_HANDLERS
    mainr=s['api'].post(f'/api/v1/bids/{bid}/conversations/main',json={},headers=s['h']());conv=expect(mainr,200)['id']
    answer=s['api'].post(f'/api/v1/bids/{bid}/conversations/{conv}/turns',json=dict(client_message_id=str(uuid4()),text='只读解释归档项目，不产生正文或正式分',source_selection=[]),headers=s['h'](tag=mainr.headers['etag']));v=expect(answer,202)
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['payload']
    lease=engine.acquire(m);assert lease;outcome=engine.run_compute(m,lease);assert engine.finish(m,lease,outcome)
    published=helper.coordinator.publish(UUID(v['id']));assert published['publication']['state']=='published' and len(s['calls'])==1
    with connection() as c:assert service.profile(c,bid)['archived_at'] is not None
    record('shared_assistant_readonly_archive',real_engine_registry_no_adapter=True,assistant_published=True,archived_state_preserved=True)


def test_authoring_late_model_reservation_and_new_generation_denied(s):
    bid,draft,sec,content,v,m=helper.generation(s);lease=engine.acquire(m);assert lease;expect(lifecycle(s,'terminate'),200)
    with pytest.raises(DomainError) as e:engine.run_compute(m,lease)
    assert e.value.code=='PROJECT_WORK_CLOSED' and len(s['calls'])==0;assert engine.finish(m,lease,None,e.value)
    with connection() as c:body=jobs_manifest=c.execute('SELECT input_manifest FROM assistant_jobs WHERE id=%s',(UUID(v['id']),)).fetchone()['input_manifest']['request']
    again=s['api'].post(f'/api/v1/bids/{bid}/authoring/generations',json=body,headers=s['h'](tag=etag('draft',draft,2)));assert again.status_code==409 and again.json()['code']=='PROJECT_WORK_CLOSED'
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM model_calls WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['n']==0
        assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==2
    record('authoring_lease_does_not_authorize_after_close',registered_dispatch=True,provider_invocations=0,reservations=0,draft_revision=2)


def test_archive_offline_fact_and_identity_correction_are_honest_exceptions(s):
    from datetime import timedelta
    from packages.domain.runtime import now
    bid=s['bids'][0];doc=helper.document(s)
    with connection() as c:
        original=c.execute('SELECT b.object_key FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s',(doc['version'],)).fetchone()['object_key']
    s['objects'][original]=(ROOT/'external-inputs/synthetic-body.pdf').read_bytes()
    expect(lifecycle(s,'terminate'),200);expect(lifecycle(s,'archive'),200)
    with connection() as c:before=service.profile(c,bid);tag=bid_etag(c,bid)
    # Fact recording accepts the actual already-happened wrong/nonreviewed artifact, without claiming gate success.
    fact_body=dict(actual_document_version_id=str(doc['version']),submitted_at=(now()-timedelta(minutes=1)).isoformat(),evidence_ref=str(doc['version']),explanation='独立合成：实际已经错传参考原件，主体待核且从未完整检查，只登记声明事实')
    rh=s['h'](tag=tag);fact=s['api'].post(f'/api/v1/bids/{bid}/submission-facts',json=fact_body,headers=rh);value=expect(fact,201)
    assert value['kind']=='offline_fact' and value['gate_snapshot'] is None and value['checks_label']=='未经过系统完整检查' and value['evidence']['platform_submission_verified'] is False
    repeat=s['api'].post(f'/api/v1/bids/{bid}/submission-facts',json=fact_body,headers=rh);assert expect(repeat,201)==value and repeat.headers['Idempotent-Replayed']=='true'
    company=s['api'].post('/api/v1/identity/bidders',json=dict(name='独立归档档案更正主体'+uuid4().hex,reason='仅明确登记主体，非资格核实'),headers=s['h']());registered=expect(company,201)
    identity=s['api'].get(f'/api/v1/bids/{bid}/identity',headers=s['h']());old=expect(identity,200)
    change=s['api'].put(f'/api/v1/bids/{bid}/identity',json=dict(bidder_id=registered['bidder']['id'],reason='独立归档后纠正主体档案，不能隐含重开工作'),headers=s['h'](tag=identity.headers['etag']));corrected=expect(change,200)
    assert corrected['identity']['binding_status']=='confirmed' and corrected['identity']['authority_id']!=old['identity']['authority_id']
    with connection() as c:
        after=service.profile(c,bid);assert after['phase']==before['phase']=='terminated' and after['archived_at']==before['archived_at']
        stored=c.execute('SELECT evidence FROM submission_records WHERE id=%s',(UUID(value['id']),)).fetchone()['evidence'];assert stored['identity']['binding_status']=='pending' and stored['prediction_binding'] is None
        assert c.execute('SELECT count(*) AS n FROM submission_records WHERE bid_id=%s',(bid,)).fetchone()['n']==1
    assert new_batch(s).status_code==409
    record('archive_explicit_historical_exceptions',pending_identity_offline_fact_allowed=True,no_gate_or_verified_platform_claim=True,immutable_fact_keeps_original_identity=True,identity_correction_allowed=True,phase_and_archive_unchanged=True,new_work_still_blocked=True)


@pytest.mark.parametrize('entry',['advance','freeze'])
def test_real_task_input_hook_supersedes_instead_of_auto_rebase(s,entry):
    bid=s['bids'][0];doc=helper.document(s)
    # Explicit new body reference for the own preparation fixture; no parser/model work.
    with connection() as c:
        body=uuid4();row=c.execute('SELECT * FROM document_versions WHERE id=%s',(doc['version'],)).fetchone()
        c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'独立任务正文.pdf',now(),'bid_body',%s)",(body,bid,row['blob_id'],s['actors'][0]));material=c.execute('SELECT current_material_id FROM bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_material_id'];c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'body',1)",(material,bid,body));c.execute('UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s',(body,bid));tag=bid_etag(c,bid)
        # Domain preparation sees an actual existing document operation, still pending.
        cmd=Command(c,s['actors'][0],'qa-task-parse-intent',bid,uuid4().hex,{});op=enqueue(c,bid,cmd.id,'parse-materials',dict(id=material));cmd.finish(op)
    template=s['api'].post(f'/api/v1/bids/{bid}/task-definitions',json=dict(name='独立固定输入否决检查',task_kind='denial_check',instructions='正文变化不授权自动换新稿'),headers=s['h'](tag=tag));definition=expect(template,201)
    run=s['api'].post(f'/api/v1/bids/{bid}/task-definitions/{definition["id"]}/runs',json=dict(rerun=False),headers=s['h'](tag=template.headers['etag']));rv=expect(run,202)
    assert reviews.TASK_INPUT_GUARD is inputs.review_guard
    with connection() as c:
        service.lock(c,bid);old=c.execute('SELECT pending_intent FROM review_slots WHERE bid_id=%s',(bid,)).fetchone()['pending_intent'];expected_revision=old['material_revision'];c.execute('UPDATE bids SET material_revision=material_revision+1,settings=%s WHERE id=%s',(Jsonb(dict(auto_review=True)),bid))
        if entry=='advance':bidrow,slot=reviews._locked(c,bid);reviews._advance_head(c,bidrow,slot['pending_intent'])
        else:assert reviews.freeze_candidate(c,bid) is None
        final=c.execute('SELECT pending_intent FROM review_slots WHERE bid_id=%s',(bid,)).fetchone()['pending_intent'];assert final['state']=='superseded' and final['error_code']=='TASK_INPUT_SUPERSEDED' and final['material_revision']==expected_revision
        assert c.execute('SELECT count(*) AS n FROM review_runs WHERE bid_id=%s',(bid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s',(bid,)).fetchone()['n']==0
    record('registered_task_guard_'+entry,frozen_revision_retained=True,despite_auto_review_true=True,review_runs=0,model_calls=0)


def test_comparison_lane_rejects_valid_paid_envelopes_without_acquire(s,monkeypatch):
    bid=s['bids'][0];messages=[]
    with connection() as c:
        for handler in ['prepare/1','review/1','assistant/1','authoring-generate/1']:
            cmd=Command(c,s['actors'][0],'qa-misroute-'+handler,bid,uuid4().hex,{})
            # Actual enqueue_plan schema identities remain valid even when routed to the wrong lane.
            from packages.domain.runtime import enqueue_plan
            op=enqueue_plan(c,bid,cmd.id,'qa-misroute',dict(id=str(uuid4())),stages=[dict(key='task',depends_on=[])],handler_version=handler,topic=engine.task_topic(handler));cmd.finish(op);messages.append(c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(op['id']),)).fetchone()['payload'])
    for m in messages:
        with pytest.raises(DomainError) as e:engine.handle_lane('comparison',m)
        assert e.value.code=='COMPARISON_LANE_REJECTED'
        for lane in ['document','export','assistant']:
            if m['handler_version'] not in engine.LANE_HANDLERS[lane]:
                with pytest.raises(DomainError) as e:engine.handle_lane(lane,m)
                assert e.value.code=='WORKER_LANE_REJECTED'
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM step_receipts r JOIN operations o ON o.id=r.operation_id WHERE o.bid_id=%s',(bid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s',(bid,)).fetchone()['n']==0
    policy=json.loads((ROOT/'execution-inputs/apps/worker/execution-policy-07.json').read_text());assert policy['consumer_lanes']['comparison']['topic']=='tender.task.comparison.v1' and policy['consumer_lanes']['comparison']['group']=='tender-comparison-worker-v1' and engine.task_topic('comparison/1')=='tender.task.comparison.v1'
    record('comparison_lane_real_valid_envelopes',rejected_handlers=4,receipts=0,model_calls=0,separate_topic_group_policy=True,no_consumer_instantiated=True)


@pytest.fixture(scope='session',autouse=True)
def freeze_identity():
    manifest=json.loads((ROOT/'runtime-manifest.json').read_text())
    for row in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    actual=main.app.openapi();encoded=(json.dumps(actual,ensure_ascii=False,indent=2)+'\n').encode();assert hashlib.sha256(encoded).hexdigest()=='3249fec969b04a3bc298bfd77a2f0dda7d1a4f3a1f1213a1a8d6648db6998fe7'
    assert not any(p.endswith('/lifecycle') for p in actual['paths'])
    (ROOT/'actual-main-07.openapi.json').write_bytes(encoded)
    main.app.include_router(lifecycle_router)  # Explicit local composition only, not a published07 interface.
    yield
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        file=getattr(module,'__file__',None)
        if file and name.startswith(('apps.','packages.')):
            file=Path(file).resolve();assert file.is_relative_to(ROOT/'execution-inputs'),name;loaded.append(dict(module=name,path=str(file.relative_to(ROOT/'execution-inputs')),sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    for row in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    out=ROOT/os.environ.get('QA_BACKEND07_EXECUTION','execution');out.mkdir(exist_ok=True)
    (out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,loaded_modules=loaded,paid_calls=0,oss_requests=0,consumers_started=0,lifecycle_router='explicit test-only composition; not main registered'),ensure_ascii=False,indent=2)+'\n')
