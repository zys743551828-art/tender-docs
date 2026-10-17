"""Independent current upload/full historical reconstruction, never model execution."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT=Path(os.environ['QA_ASSISTANT_ROOT'])
FIXTURES=ROOT/os.environ.get('QA_HIST_FIXTURES','fixture-inputs')
spec=importlib.util.spec_from_file_location('historical_independent_fixture',ROOT/'qa-helpers/assistant_fixture.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from apps.api import main
from apps.api.historical_routes import router as proofs_router
from apps.api.historical_input_routes import router as inputs_router
from apps.worker import engine,handlers
from packages.domain import storage
from packages.domain.runtime import connection,sha,clean,DomainError
from packages.model_governance import historical_input as repo

OBS=[]
def record(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,code):return helper.expect(r,code)


class RealLocalParser:
    def __init__(self):
        identity=self.run('identity');self.parser_digest=identity['parser_digest'];self.config_digest=identity['config_digest']
    def run(self,mode,data=b''):
        r=subprocess.run(['/workspace/tender-workbench/.venv-parsing/bin/python','-I',str(ROOT/'qa-helpers/historical_cpu_parser.py'),str(ROOT/'execution-inputs'),mode],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,check=True,cwd='/tmp',env=dict(PATH=os.defpath,LANG='C.UTF-8',LC_ALL='C.UTF-8'))
        return json.loads(r.stdout)
    def parse(self,data):return self.run('parse',data)


@pytest.fixture
def world(monkeypatch):
    gen=helper.s.__wrapped__(monkeypatch);s=next(gen)
    monkeypatch.setenv('OBJECT_STORE_BACKEND','s3');monkeypatch.setattr(storage,'sign',lambda *args,**kwargs:'http://memory.invalid/independent-original')
    parser=RealLocalParser();monkeypatch.setattr(main,'configured_parser',lambda:parser);monkeypatch.setattr(handlers,'configured_parser',lambda:parser)
    class Memory:
        def head_object(self,**kw):
            raw=s['objects'][kw['Key']];return dict(ContentLength=len(raw),Metadata=dict(sha256=sha(raw)))
        def get_object(self,**kw):
            raw=s['objects'][kw['Key']];return dict(ContentLength=len(raw),Body=io.BytesIO(raw))
    monkeypatch.setattr(storage,'client',lambda:Memory())
    try:
        with TestClient(main.app) as api:
            s['api']=api;s['parser_digest']=parser.parser_digest;yield s
    finally:gen.close()


def execute(s,accepted):
    op=UUID(accepted['operation']['id'])
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s ORDER BY id LIMIT 1',(op,)).fetchone()['payload']
    lease=engine.acquire(m);assert lease;computed=engine.run_compute(m,lease);assert engine.finish(m,lease,computed)
    with connection() as c:assert c.execute('SELECT state FROM operations WHERE id=%s',(op,)).fetchone()['state']=='succeeded'


@pytest.fixture
def prepared(world):
    s=world;api=s['api'];bid=s['bids'][0];actor=s['actors'][0]
    # Actual ordinary HTTP grants/complete/commit and actual restricted Worker/parser.
    batch=expect(api.post(f'/api/v1/bids/{bid}/upload-batches',json={},headers=s['h']()),201)['id'];uploaded={}
    for entry in json.loads((FIXTURES/'manifest.json').read_text()):
        raw=(FIXTURES/entry['path']).read_bytes();assert sha(raw)==entry['sha256'];role='body' if entry['path']=='body.pdf' else 'tender'
        grant=expect(api.post(f'/api/v1/bids/{bid}/uploads',json=dict(batch_id=batch,filename=entry['path'],role=role,declared_sha256=sha(raw),byte_size=len(raw),target=dict(kind='new_file')),headers=s['h']()),200)
        with connection() as c:row=c.execute('SELECT staging_key FROM uploads WHERE id=%s',(UUID(grant['upload_id']),)).fetchone()
        s['objects'][row['staging_key']]=raw # Explicit in-memory object transport only; no signed native request.
        complete=expect(api.post(f'/api/v1/uploads/{grant["upload_id"]}/complete',json=dict(parts=[],declared_sha256=sha(raw)),headers=s['h']()),202);execute(s,complete)
        state=expect(api.get(f'/api/v1/uploads/{grant["upload_id"]}',headers=s['h']()),200);uploaded[role]=state
    b=api.get(f'/api/v1/bids/{bid}',headers=s['h']());expect(b,200)
    commit=expect(api.post(f'/api/v1/bids/{bid}/upload-batches/{batch}/commit',json=dict(accepted_upload_ids=[x['id'] for x in uploaded.values()],accepted_binding_ids=[x['binding_id'] for x in uploaded.values()],explicit_partial_acceptance=False),headers=s['h'](tag=b.headers['etag'])),202);execute(s,commit)
    with connection() as c:
        docrows=c.execute("SELECT d.id,d.artifact_kind,d.obtained_at,d.created_at,b.digest,n.known_at,p.id AS parse_id,p.page_count,p.coverage FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN retrieval.source_nodes n ON n.id=(SELECT id FROM retrieval.source_nodes WHERE document_version_id=d.id AND EXISTS(SELECT FROM retrieval.lineage_edges e WHERE e.child_id=id) LIMIT 1) WHERE d.bid_id=%s ORDER BY d.id",(bid,)).fetchall()
        assert len(docrows)==2 and all(x['coverage']=='complete' for x in docrows)
        material=c.execute('SELECT current_material_id FROM bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_material_id'];ruleset=uuid4();c.execute("INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",(ruleset,bid,material,sha(str(ruleset))))
        tender=UUID(uploaded['tender']['document_version_id']);tender_ev=c.execute('SELECT e.id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id WHERE p.document_version_id=%s ORDER BY e.id',(tender,)).fetchone()['id']
        for name in ['Complete safety plan','Complete quality controls']:
            c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,max_points,bands) VALUES(%s,%s,%s,'scoring',%s,'[]','[]',%s,30,%s)",(uuid4(),bid,ruleset,name,[tender_ev],Jsonb([dict(label='Complete',description=name,points='30')])));
        c.execute('UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s',(ruleset,bid))
    # Explicit manual scoring rules are test fixture; no model/mock extraction or old review is invented.
    current=api.get(f'/api/v1/bids/{bid}',headers=s['h']());expect(current,200)
    offline=expect(api.post(f'/api/v1/bids/{bid}/submission-facts',json=dict(actual_document_version_id=uploaded['body']['document_version_id'],submitted_at='2019-06-01T00:00:00Z',evidence_ref=uploaded['tender']['document_version_id'],explanation='独立合成历史事实，未运行当年评审或核验真实性'),headers=s['h'](tag=current.headers['etag'])),201);submission=offline['id']
    with connection() as c:
        projection=clean(c.execute('SELECT bidder.id AS bidder_id,bidder.name AS bidder_name,r.id AS round_id,e.id AS engineering_project_id,e.title AS engineering_title,e.region,e.engineering_type,r.lot,r.round_no FROM bid_profiles p JOIN bidders bidder ON bidder.id=p.bidder_id JOIN bid_rounds r ON r.id=p.round_id JOIN engineering_projects e ON e.id=r.engineering_project_id WHERE p.bid_id=%s',(bid,)).fetchone())
        proofs={}
        for role,v in uploaded.items():
            proof=clean(c.execute('SELECT d.id AS document_version_id,b.digest AS blob_digest,p.id AS parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN evidence_blocks e ON e.parse_id=p.id WHERE d.id=%s ORDER BY e.physical_page,e.ordinal LIMIT 1',(UUID(v['document_version_id']),)).fetchone());proof['explanation']='独立合成证明，只验证精确来源链';proofs[role]=proof
    def proof_context():
        r=api.get(f'/api/v1/historical-proof/submissions/{submission}/context',headers=s['h']());expect(r,200);return r
    subject=api.post('/api/v1/historical-proof/subjects',json=dict(submission_id=submission,proposed_identity=projection,proof_refs=[proofs['body']],declared_data_origin='synthetic',reason='独立合成历史主体声明'),headers=s['h'](tag=proof_context().headers['etag']));sv=expect(subject,201)
    expect(api.post(f'/api/v1/historical-proof/subjects/{sv["id"]}/confirm',json=dict(reason='人工明确核对合成主体证明'),headers=s['h'](tag=subject.headers['etag'])),200)
    claims=[]
    for role in ['body','tender']:
        kinds=['content_existed','known_to_bidder']+(['effective'] if role=='tender' else [])
        request=dict(submission_id=submission,document_version_id=uploaded[role]['document_version_id'],historical_subject_authority_id=sv['id'],facts=[dict(fact_kind=kind,claimed_at='2019-01-01T00:00:00Z',proof_refs=[proofs[role]],explanation='合成独立历史事实') for kind in kinds],declared_data_origin='synthetic',reason='独立逐原件历史事实声明')
        response=api.post('/api/v1/historical-proof/source-claims',json=request,headers=s['h'](tag=proof_context().headers['etag']));v=expect(response,201);expect(api.post(f'/api/v1/historical-proof/source-claims/{v["id"]}/confirm',json=dict(reason='明确确认合成精确原件声明'),headers=s['h'](tag=response.headers['etag'])),200);claims.append(v['id'])
    value=dict(submission_id=submission,historical_subject_authority_id=sv['id'],source_claim_ids=claims,material_version_ids=[uploaded['tender']['document_version_id']],rule_set_id=str(ruleset),reason='独立当前创建历史完整包，不是当年预测')
    ctx=api.get(f'/api/v1/historical-inputs/submissions/{submission}/context',headers=s['h']());expect(ctx,200)
    s.update(uploaded=uploaded,source_proofs=proofs,value=value,input_headers=s['h'](tag=ctx.headers['etag']),subject_id=sv['id'],claims=claims,docrows=docrows)
    return s


def freeze(s):
    r=s['api'].post('/api/v1/historical-inputs',json=s['value'],headers=s['input_headers']);return r


def test_current_ordinary_upload_offline_and_full_body_input(prepared):
    s=prepared;api=s['api'];value=expect(freeze(s),201);assert value['current'] and value['origin']=='synthetic' and not value['execution_available'] and value['formal_eligibility']=='unavailable'
    assert value['body_version_id']==s['uploaded']['body']['document_version_id'] and value['historical_as_of'].startswith('2019-06-01')
    assert all(d['known_at'].year>2019 and d['obtained_at'].year>2019 and d['created_at'].year>2019 for d in s['docrows'])
    with connection() as c:
        original=c.execute('SELECT review_id,created_at FROM submission_records WHERE id=%s',(UUID(s['value']['submission_id']),)).fetchone();assert original['review_id'] is None and original['created_at'].year>2019
        chapters=c.execute('SELECT p.raw_content FROM historical_backtest.chapters ch JOIN historical_input.chapter_payloads p ON p.chapter_id=ch.id WHERE ch.input_id=%s',(UUID(value['id']),)).fetchall()
        assert len(chapters)==2
        for ch in chapters:
            package=json.loads(ch['raw_content']);raw=json.dumps(package,ensure_ascii=False);assert 'PHYSICAL_PAGE_TWO_INCLUDED' in raw and 'Full engineering response' in raw
            assert 'official_score' not in raw and 'prediction_score' not in raw
        assert c.execute('SELECT count(*) AS n FROM review_runs WHERE bid_id=%s',(s['bids'][0],)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s',(s['bids'][0],)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM historical_backtest.input_claims WHERE input_id=%s',(UUID(value['id']),)).fetchone()['n']==2
    replay=freeze(s);assert expect(replay,201)==value and replay.headers['Idempotency-Replayed']=='true'
    record('ordinary_upload_to_exact_retrospective_full_input',review_id=None,known_obtained_created_not_backdated=True,body_pages=2,whole_body_in_each_rule=True,rule_packages=2,origin='synthetic',model_calls=0)
    s['saved_input']=value


def test_actual_artifact_bytes_checked_on_get_and_replay(prepared):
    s=prepared;v=expect(freeze(s),201)
    with connection() as c:blobs=repo.metadata(c,UUID(v['id']))['blobs']
    for blob in blobs:
        key=blob['object_key'];original=s['objects'][key];s['objects'][key]=original+b'corruption'
        try:
            for request in [lambda:s['api'].get('/api/v1/historical-inputs/'+v['id'],headers=s['h']()),lambda:freeze(s)]:
                r=request();assert r.status_code==409 and r.json()['code']=='HISTORICAL_INPUT_ARTIFACT_INVALID'
        finally:s['objects'][key]=original
    assert expect(s['api'].get('/api/v1/historical-inputs/'+v['id'],headers=s['h']()),200)['current']
    record('actual_staged_blob_size_sha_checked',artifacts=len(blobs),get_and_replay_rejected_each=True,restored_exact_bytes_usable=True)


@pytest.mark.parametrize('change',['source','proof','parse'])
def test_source_proof_or_parse_change_in_object_window_is_atomic(prepared,monkeypatch,change):
    s=prepared;original=storage.put;triggered=[]
    def put(key,raw,media):
        if media==repo.MEDIA and not triggered:
            with connection() as c:
                assert c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchone()['n']==0
                if change=='parse':
                    old=c.execute('SELECT * FROM parse_runs WHERE id=%s',(UUID(s['source_proofs']['body']['parse_id']),)).fetchone();c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,2,'partial','[\"new parse incomplete\"]')",(uuid4(),old['bid_id'],old['document_version_id'],old['parser_digest'],sha(str(uuid4()))))
                else:
                    proof=s['source_proofs']['body' if change=='proof' else 'tender'];root=helper.UUID(proof['document_version_id'])
                    from packages.rag.indexing import original_source_id
                    c.execute('SELECT pg_advisory_xact_lock(90127,1)');c.execute("UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",(original_source_id(root),))
            triggered.append(True)
        return original(key,raw,media)
    monkeypatch.setattr(storage,'put',put)
    r=freeze(s);assert r.status_code in [409,412] and triggered
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM historical_backtest.inputs WHERE bid_id=%s',(s['bids'][0],)).fetchone()['n']==0
        assert c.execute("SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='historical-input-freeze' AND response IS NOT NULL",(UUID(s['value']['submission_id']),)).fetchone()['n']==0
    record('stage_window_'+change,actual_no_capture_transaction=True,http_status=r.status_code,persisted_inputs=0,successful_command_receipts=0)


def test_withdrawal_history_stale_replay_not_green_and_rr_guard(prepared):
    s=prepared;v=expect(freeze(s),201)
    from packages.rag.indexing import original_source_id
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(90127,1)');c.execute("UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",(original_source_id(UUID(s['uploaded']['tender']['document_version_id'])),))
    old=s['api'].get('/api/v1/historical-inputs/'+v['id'],headers=s['h']());assert expect(old,200)['current'] is False
    r=freeze(s);assert r.status_code==409
    with pytest.raises(DomainError) as e,connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');c.execute('SELECT 1');repo.view(c,UUID(v['id']))
    assert e.value.code=='HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED'
    record('historical_read_and_replay_currentness',old_record_visible_stale=True,replay_rejected=True,old_rr_snapshot_rejected=True)


@pytest.mark.parametrize('mutation',['body_as_material','wrong_body_claim','fake_qualification','wrong_rule_bid'])
def test_exact_scope_requests_reject_before_object_write(prepared,mutation):
    s=prepared;request=copy.deepcopy(s['value']);before=len(s['objects'])
    if mutation=='body_as_material':request['material_version_ids'].append(s['uploaded']['body']['document_version_id'])
    elif mutation=='wrong_body_claim':request['source_claim_ids']=[s['claims'][1]]
    elif mutation=='fake_qualification':request.update(origin='real',historical_as_of='2019-06-01T00:00:00Z',formal_eligibility='qualified',review_id=str(uuid4()))
    else:
        with connection() as c:
            material=uuid4();rules=uuid4();c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,s['bids'][1],sha(str(material))));c.execute("INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",(rules,s['bids'][1],material,sha(str(rules))));request['rule_set_id']=str(rules)
    r=s['api'].post('/api/v1/historical-inputs',json=request,headers=s['input_headers']);assert r.status_code in [404,409,422] and len(s['objects'])==before
    record('scope_'+mutation,status=r.status_code,no_staged_objects=True)


def test_sealed_child_wait_and_non_api_privilege_boundaries(prepared):
    s=prepared;v=expect(freeze(s),201);identity=UUID(v['id'])
    def append_child():
        try:
            with connection() as c:c.execute('INSERT INTO historical_backtest.input_claims SELECT * FROM historical_backtest.input_claims WHERE input_id=%s LIMIT 1',(identity,))
        except psycopg.Error as e:return e.sqlstate
        return 'unexpected-write'
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection() as c:
            c.execute('SELECT id FROM historical_backtest.inputs WHERE id=%s FOR UPDATE',(identity,));pid=c.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'];future=pool.submit(append_child);blocked=False
            for _ in range(100):
                with connection() as observer:blocked=observer.execute('SELECT EXISTS(SELECT FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))) AS v',(pid,)).fetchone()['v']
                if blocked:break
                time.sleep(.02)
            assert blocked and not future.done()
        assert future.result(timeout=10)=='55000'
    roles=[]
    for key in ['WORKER_DATABASE_DSN','RESEARCH_WORKER_DATABASE_DSN','SCORE_READ_DATABASE_DSN']:
        with psycopg.connect(os.environ[key],row_factory=dict_row) as c:
            assert not c.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()['rolsuper']
        for sql,args in [('SELECT * FROM historical_backtest.inputs WHERE id=%s',(identity,)),('SELECT historical_input.current_input(%s)',(identity,)),('SELECT historical_input.seal_input(%s,%s)',(identity,s['actors'][0]))]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege),psycopg.connect(os.environ[key]) as c:c.execute(sql,args)
        roles.append(key.removesuffix('_DATABASE_DSN'))
    record('seal_and_roles',real_parent_lock_wait=True,late_child_sqlstate='55000',non_api_read_current_and_seal_denied_roles=roles)


@pytest.fixture(scope='session',autouse=True)
def evidence():
    rows=json.loads((ROOT/'runtime-manifest.json').read_text())
    for x in rows:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    assert not any(p.startswith('/api/v1/historical-inputs') for p in main.app.openapi()['paths'])
    main.app.include_router(proofs_router);main.app.include_router(inputs_router)
    yield
    for x in rows:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        p=getattr(module,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(ROOT/'execution-inputs'),name;loaded.append(dict(module=name,path=str(p.relative_to(ROOT/'execution-inputs')),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_HIST_EXECUTION','execution');out.mkdir(exist_ok=True);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,loaded_modules=loaded,paid_calls=0,model_calls=0,oss_requests=0,consumers_started=0,composition='Historical proof+input routers explicitly included in local TestClient only; fixed shared main has no registration.'),ensure_ascii=False,indent=2)+'\n')
