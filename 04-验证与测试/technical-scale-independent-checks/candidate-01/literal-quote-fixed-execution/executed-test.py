"""Independent sourced technical maximum authority, no denominator/score integration."""
import copy,hashlib,importlib.util,json,os,sys
from pathlib import Path
from uuid import UUID,uuid4
from decimal import Decimal
import httpx,requests,psycopg,pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(os.environ['QA_ASSISTANT_ROOT']);RUNTIME=ROOT/'execution-inputs'
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
word=load('independent_word_fixture',ROOT/'qa-helpers/word_bridge.py');hist=load('independent_history_fixture',ROOT/'qa-helpers/historical.py')
from apps.api import main
from apps.api.technical_scale_routes import router
from packages.domain import storage
from packages.domain.runtime import connection,clean,sha,DomainError
from packages.identity.repository import record_binding
from packages.model_governance import scale_repository as repo
from packages.model_governance.scale_contracts import extend_openapi
from packages.word_intake.renderer import build_profile
OBS=[]
def note(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,status):return hist.expect(r,status)
ACTUAL=None

def valid(response,name,status=200):
    v=expect(response,status);Draft202012Validator({'$ref':'#/components/schemas/'+name,'components':ACTUAL['components']},format_checker=FormatChecker()).validate(v);return v
@pytest.fixture(scope='session')
def profile():
    p=ROOT/'actual-word-profile.json';value=build_profile();p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600);return p,value
@pytest.fixture
def w(monkeypatch,profile):
    gen=word.w.__wrapped__(monkeypatch);w=next(gen)
    monkeypatch.setenv('WORD_RENDER_PROFILE_PATH',str(profile[0]))
    def forbidden(*a,**kw):raise AssertionError('Scale QA hard-blocks external HTTP and models')
    async def aforbidden(*a,**kw):forbidden()
    monkeypatch.setattr(requests.sessions.Session,'request',forbidden);monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden);monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',aforbidden)
    from packages.domain.providers.deepseek import DeepSeekClient
    monkeypatch.setattr(DeepSeekClient,'from_env',forbidden);monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    try:
        hist.prepared.__wrapped__(w)
        with connection() as c:record_binding(c,w['bids'][0],w['actors'][0],None,reason='独立合成当前主体显式确认',confirmed=True)
        raw=word.docx('技术总分满分为70分，量尺为未加权原始分。');u=word.upload(w,raw,name='独立技术满分招标.docx',role='tender');computed,_=word.finish(u['verified']['conversion_operation_id']);assert computed['outcome']['coverage']=='complete',computed['outcome']
        accepted=word.commit(w,u);word.finish(accepted['operation']['id'])
        with connection() as c:
            pdf=c.execute("SELECT document_version_id FROM document_relations WHERE source_document_version_id=%s AND kind='controlled_render' AND verification='verified'",(UUID(u['version']),)).fetchone()['document_version_id'];material=c.execute('SELECT current_material_id FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()['current_material_id']
            ref=clean(c.execute('SELECT v.id AS document_version_id,b.digest AS blob_digest,p.id AS parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN parse_runs p ON p.document_version_id=v.id JOIN evidence_blocks e ON e.parse_id=p.id WHERE v.id=%s ORDER BY p.created_at DESC,e.ordinal LIMIT 1',(pdf,)).fetchone());assert ''.join(ref['quote'].split())=='技术总分满分为70分，量尺为未加权原始分。'
            assert c.execute('SELECT count(*) n FROM parse_runs WHERE document_version_id=%s',(UUID(u['version']),)).fetchone()['n']==0
            from packages.rag.indexing import original_source_id
            assert c.execute('SELECT count(*) n FROM retrieval.chunks WHERE source_id=%s',(original_source_id(UUID(u['version'])),)).fetchone()['n']==0
            times=clean(c.execute('SELECT v.id,v.created_at,v.obtained_at,n.known_at FROM document_versions v JOIN retrieval.source_nodes n ON n.id=%s WHERE v.id=%s',(original_source_id(pdf),pdf)).fetchone());assert times['created_at'].startswith('2026') and times['known_at'].startswith('2026')
        with connection() as c:pdfblob=c.execute('SELECT b.object_key,b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s',(pdf,)).fetchone()
        artifact=ROOT/'own-artifacts'/str(w['bid']);artifact.mkdir(parents=True,exist_ok=True);(artifact/'original.docx').write_bytes(raw);(artifact/'derived.pdf').write_bytes(w['objects'][pdfblob['object_key']]);(artifact/'conversion-and-source.json').write_text(json.dumps(dict(outcome=computed['outcome'],source_ref=ref,pdf_times=times),ensure_ascii=False,indent=2)+'\n')
        w.update(word=u,pdf=str(pdf),material=str(material),ref=ref,word_original_has_no_parse_or_chunks=True,pdf_current_times=times)
        w['word_claim']=claim(w,u['version']);yield w
    finally:gen.close()

def claim(w,document,known='2019-01-01T00:00:00Z',supersedes=None):
    api=w['api'];submission=w['value']['submission_id'];ctx=api.get('/api/v1/historical-proof/submissions/'+submission+'/context',headers=w['h']());expect(ctx,200);proof=w['ref']|dict(explanation='独立合成各版事实声明，不能证明真实派生PDF在2019已存在')
    b=dict(submission_id=submission,document_version_id=document,historical_subject_authority_id=w['subject_id'],declared_data_origin='synthetic',facts=[dict(fact_kind=kind,claimed_at=known if kind=='known_to_bidder' else '2019-01-01T00:00:00Z',proof_refs=[proof],explanation='仅独立合成机制') for kind in ['content_existed','known_to_bidder','effective']],reason='逐版本显式合成历史声明，不承接Word证明')
    if supersedes:b['supersedes_id']=supersedes
    r=api.post('/api/v1/historical-proof/source-claims',json=b,headers=w['h'](tag=ctx.headers['etag']));v=expect(r,201);expect(api.post('/api/v1/historical-proof/source-claims/'+v['id']+'/confirm',json=dict(reason='明确仅合成历史证明机制'),headers=w['h'](tag=r.headers['etag'])),200);return v['id']
def ctx(w,historical=False):
    params=dict(purpose='historical_backtest',submission_id=w['value']['submission_id'],material_snapshot_id=w['material']) if historical else {}
    r=w['api'].get(f'/api/v1/bids/{w["bid"]}/technical-scale-context',params=params,headers=w['h']());valid(r,'TechnicalScaleContext');return r

def body(w,historical=False,claims=None):
    scope=dict(purpose='current_prebid')
    if historical:scope=dict(purpose='historical_backtest',submission_id=w['value']['submission_id'],historical_subject_authority_id=w['subject_id'],material_snapshot_id=w['material'],claim_ids=claims or [w['word_claim'],w['pdf_claim']])
    ref={key:w['ref'][key] for key in ['document_version_id','blob_digest','parse_id','physical_page','evidence_id','quote']};ref['supported_fields']=['technical_maximum','scale_description']
    return dict(intent='confirm',context=scope,technical_maximum='70',scale_description='未加权原始分',aggregation=dict(status='unknown',description='官方聚合方法尚不明确，未执行聚合'),evidence_refs=[ref],reason='有据登记合成技术满分，仍无正式模型资格')
def register(w,value,context=None,headers=None):
    context=context or ctx(w,value['context']['purpose']=='historical_backtest')
    return w['api'].post(f'/api/v1/bids/{w["bid"]}/technical-scales',json=value,headers=headers or w['h'](tag=context.headers['etag']))

def test_seven_api_pending_confirm_correct_withdraw_and_stale_replay(w):
    caps=valid(w['api'].get('/api/v1/technical-scale-capabilities',headers=w['h']()),'TechnicalScaleCapabilities');assert not caps['score_input_integration'] and not caps['paid_execution_enabled']
    c=ctx(w);pending=body(w)|dict(intent='save_pending',technical_maximum=None,scale_description='',evidence_refs=[]);r=register(w,pending,c);p=valid(r,'TechnicalScaleView',201);assert p['technical_maximum'] is None and not p['current_applicability']
    correction=body(w)|dict(base_version_id=p['id']);r2=w['api'].post('/api/v1/technical-scales/'+p['id']+'/corrections',json=correction,headers=w['h'](tag=r.headers['etag']));v=valid(r2,'TechnicalScaleView',201);assert Decimal(v['technical_maximum'])==70 and v['current_applicability']
    histold=valid(w['api'].get('/api/v1/technical-scales/'+p['id'],headers=w['h']()),'TechnicalScaleView');assert not histold['is_current_head'] and histold['technical_maximum'] is None
    listing=valid(w['api'].get(f'/api/v1/bids/{w["bid"]}/technical-scales',headers=w['h']()),'TechnicalScaleList');assert len(listing['items'])==2
    correction2=body(w)|dict(base_version_id=v['id']);h=w['h'](tag=r2.headers['etag']);r3=w['api'].post('/api/v1/technical-scales/'+v['id']+'/corrections',json=correction2,headers=h);v3=valid(r3,'TechnicalScaleView',201);assert valid(w['api'].post('/api/v1/technical-scales/'+v['id']+'/corrections',json=correction2,headers=h),'TechnicalScaleView',201)==v3
    wh=w['h'](tag=r3.headers['etag']);withdraw=w['api'].post('/api/v1/technical-scales/'+v3['id']+'/withdrawals',json=dict(reason='独立有理由撤回'),headers=wh);wv=valid(withdraw,'TechnicalScaleView');assert wv['technical_maximum'] is None
    assert w['api'].post('/api/v1/technical-scales/'+v['id']+'/corrections',json=correction2,headers=h).status_code==409
    note('seven_typed_api_lifecycle',all_7_operations=True,pending_null=True,confirmation_once=True,correction_append_history=True,stale_replay_refused=True,formal_eligibility=v3['formal_eligibility'])


def test_native_Word_PDF_full_exact_claims_and_ancestor_withdrawal(w):
    # Only Word claim exists now: newly converted PDF must not inherit it.
    c=ctx(w,True);assert register(w,body(w,True,[w['word_claim']]),c).status_code==409
    w['pdf_claim']=claim(w,w['pdf']);c=ctx(w,True);v=body(w,True)
    negative=[('missing_parent',[w['pdf_claim']]),('extra',v['context']['claim_ids']+[w['claims'][0]]),('mixed',[w['word_claim'],w['claims'][1]])]
    for label,claims in negative:assert register(w,body(w,True,claims),c).status_code==409,label
    h=w['h'](tag=c.headers['etag']);saved=register(w,v,c,headers=h);sv=valid(saved,'TechnicalScaleView',201);assert sv['current_applicability'] and Decimal(sv['technical_maximum'])==70 and sv['formal_eligibility']=='unavailable'
    from packages.rag.indexing import original_source_id
    with connection() as connection_:
        connection_.execute('SELECT pg_advisory_xact_lock(90127,1)');changed=connection_.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(UUID(w['word']['version'])),));assert changed.rowcount==1
    old=valid(w['api'].get('/api/v1/technical-scales/'+sv['id'],headers=w['h']()),'TechnicalScaleView');assert old['technical_maximum'] is None and not old['current_applicability'] and Decimal(old['recorded_maximum'])==70
    assert register(w,v,c,headers=h).status_code==409
    note('real_word_full_claim_closure',Word_no_parse_chunks=True,actual_no_net_Docker_and_CPU_parse=True,pdf_2026_times_preserved=w['pdf_current_times'],only_Word_old_proof_not_PDF=True,missing_parent_extra_mixed_rejected=True,separate_synthetic_claims_required=True,Word_root_withdrawal_null=True,replay_refused=True)


def test_IO_source_change_rolls_back_scale_version_and_allows_graph_lock(w,monkeypatch):
    c=ctx(w);v=body(w);get=storage.get;changed=[]
    def race(key):
        if not changed:
            from packages.rag.indexing import original_source_id
            with connection() as q:
                assert q.execute('SELECT pg_try_advisory_xact_lock(90127,1) free').fetchone()['free'];assert not q.execute("SELECT pid FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchall()
                assert q.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(UUID(w['word']['version'])),)).rowcount==1
            changed.append(True)
        return get(key)
    monkeypatch.setattr(storage,'get',race);failed=register(w,v,c);assert failed.status_code in [409,412] and changed
    with connection() as q:assert q.execute('SELECT count(*) n FROM technical_scale.versions WHERE bid_id=%s',(w['bid'],)).fetchone()['n']==0
    note('object_IO_final_CAS',parent_withdrawn_during_IO=True,graph_lock_obtainable=True,no_capture_transaction_held=True,versions=0)


def test_closed_archive_current_rejected_history_independent_and_time_known_not_public(w):
    w['pdf_claim']=claim(w,w['pdf'])
    with connection() as q:q.execute("UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",(w['bid'],));before=q.execute('SELECT current_body_id,current_review_id,phase,archived_at FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()
    assert register(w,body(w)).status_code==409
    r=register(w,body(w,True));v=valid(r,'TechnicalScaleView',201);assert v['current_applicability']
    with connection() as q:assert q.execute('SELECT current_body_id,current_review_id,phase,archived_at FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()==before
    oldclaim=w['pdf_claim'];w['pdf_claim']=claim(w,w['pdf'],known='2020-01-01T00:00:00Z',supersedes=oldclaim)
    old=valid(w['api'].get('/api/v1/technical-scales/'+v['id'],headers=w['h']()),'TechnicalScaleView');assert old['technical_maximum'] is None
    # A different historical purpose head cannot use post-submission bidder knowledge.
    # Historical correction remains a separate research resource, never reopens archive.
    bad=body(w,True)|dict(base_version_id=v['id']);latest=w['api'].get('/api/v1/technical-scales/'+v['id'],headers=w['h']());assert w['api'].post('/api/v1/technical-scales/'+v['id']+'/corrections',json=bad,headers=w['h'](tag=latest.headers['etag'])).status_code==409
    note('archive_and_historical_time',current_closed_refused=True,historical_no_project_changes=True,late_known_claim_invalidates_old_max=True,post_submit_knowledge_correction_rejected=True)


@pytest.mark.parametrize('mutation',['quote_number','false_max','cross_parse','foreign_document','ambiguity'])
def test_exact_whole_declaration_and_scope_rejected_before_object_IO(w,monkeypatch,mutation):
    c=ctx(w);v=body(w)
    if mutation=='quote_number':v['evidence_refs'][0]['quote']='70'
    elif mutation=='false_max':v['technical_maximum']='90'
    elif mutation=='cross_parse':v['evidence_refs'][0]['parse_id']=str(uuid4())
    elif mutation=='foreign_document':v['evidence_refs'][0]['document_version_id']=w['uploaded']['body']['document_version_id']
    else:v['scale_description']='未加权原始分，但技术总分现为90分'
    called=[]
    def forbidden(key):called.append(key);raise AssertionError('invalid input reached object IO')
    monkeypatch.setattr(storage,'get',forbidden);r=register(w,v,c);assert r.status_code in [409,422] and not called
    note('declaration_'+mutation,rejected_before_objects=True,status=r.status_code)


def test_API_old_RR_and_Worker_SQL_access_denied(w):
    with pytest.raises(DomainError) as error,connection() as q:q.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');q.execute('SELECT 1');repo.lock(q,w['bid'])
    assert error.value.code=='CURRENT_AUTHORITY_REQUIRES_READ_COMMITTED'
    with pytest.raises(psycopg.errors.InsufficientPrivilege),connection(True) as q:q.execute('SELECT * FROM technical_scale.versions LIMIT 1')
    note('actual_roles_and_RC',old_RR_refused=True,ordinary_Worker_authority_select='42501')

@pytest.fixture(scope='session',autouse=True)
def evidence():
    global ACTUAL
    rows=json.loads((ROOT/'runtime-manifest.json').read_text())
    for x in rows:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    main.app.include_router(hist.proofs_router);main.app.include_router(hist.inputs_router);main.app.include_router(router)
    app=FastAPI();app.include_router(router);ACTUAL=extend_openapi(app.openapi());assert ACTUAL==json.loads((ROOT/'actual.openapi.json').read_text())
    yield
    for x in rows:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        p=getattr(module,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(RUNTIME),name;loaded.append(dict(module=name,path=str(p.relative_to(RUNTIME)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_SCALE_ROUND','execution');out.mkdir(exist_ok=True);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,loaded_modules=loaded,actual_matches_frozen_7=True,model_calls=0,cloud_calls=0,consumers=0,composition='Dedicated TestClient explicitly mounts proof/input/TechnicalScale; main/live not mounted'),ensure_ascii=False,indent=2)+'\n')
