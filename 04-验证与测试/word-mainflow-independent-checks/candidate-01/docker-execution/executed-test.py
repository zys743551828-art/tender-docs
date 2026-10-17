"""Independent bridge acceptance. PG/ASGI/Memory; actual no-network Docker where stated."""
import hashlib, importlib.util, io, json, os, subprocess, sys, zipfile
from pathlib import Path
from uuid import UUID,uuid4
from xml.sax.saxutils import escape
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(os.environ['QA_ASSISTANT_ROOT'])
RUNTIME=Path('/workspace/tender-workbench/docs/word-mainflow-development-checks/runtime-preview-02')
spec=importlib.util.spec_from_file_location('independent_fixture',ROOT/'qa-helpers/independent_fixture.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from apps.api import main,word_coordinator
from apps.worker import engine,handlers
from packages.domain import storage
from packages.domain.runtime import connection,sha,DomainError,Command
from packages.word_intake import renderer,repository
from packages.word_intake.inspection import DOCX,PDF
OBS=[]
def note(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,code):return helper.expect(r,code)
def docx(text,field=False,external=False):
    result=io.BytesIO();p='<w:p><w:r><w:rPr><w:rFonts w:ascii="Noto Sans CJK SC" w:eastAsia="Noto Sans CJK SC"/></w:rPr><w:t>'+escape(text)+'</w:t></w:r></w:p>'
    if field:p+='<w:p><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>'
    entries={'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>', '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'+('<Relationship Id="r2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" TargetMode="External" Target="http://127.0.0.1:9/never-request"/>' if external else '')+'</Relationships>', 'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+p+'</w:body></w:document>'}
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as z:
        for n,v in entries.items():z.writestr(n,v)
    raw=result.getvalue();directory=ROOT/'own-originals';directory.mkdir(exist_ok=True);(directory/(sha(raw)+'.docx')).write_bytes(raw);return raw
class CPUParser:
    def __init__(self):
        identity=self.run('identity');self.parser_digest=identity['parser_digest'];self.config_digest=identity['config_digest']
    def run(self,mode,data=b''):
        p=subprocess.run(['/workspace/tender-workbench/.venv-parsing/bin/python','-I',str(ROOT/'qa-helpers/cpu_parser.py'),str(RUNTIME),mode],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,check=True,cwd='/tmp',env=dict(PATH=os.defpath,LANG='C.UTF-8',LC_ALL='C.UTF-8'));return json.loads(p.stdout)
    def parse(self,data):return self.run('parse',data)
@pytest.fixture
def w(monkeypatch):
    gen=helper.s.__wrapped__(monkeypatch);s=next(gen)
    monkeypatch.setenv('WORD_INTAKE_ENABLED','1');monkeypatch.setenv('OBJECT_STORE_BACKEND','oss');monkeypatch.setenv('WORD_RENDER_PROFILE_PATH','/workspace/tender-workbench/.local/word-bridge-runtime-preview02-profile.json')
    parser=CPUParser();monkeypatch.setattr(main,'configured_parser',lambda:parser);monkeypatch.setattr(handlers,'configured_parser',lambda:parser);monkeypatch.setattr(word_coordinator,'configured_parser',lambda:parser)
    class Memory:
        def head_object(self,**kw):return dict(ContentLength=len(s['objects'][kw['Key']]))
        def get_object(self,**kw):
            raw=s['objects'][kw['Key']];return dict(ContentLength=len(raw),Body=io.BytesIO(raw))
    monkeypatch.setattr(storage,'client',lambda:Memory())
    def put(key,raw,media):
        s['objects'][key]=raw;return {'ETag':sha(raw)}
    monkeypatch.setattr(storage,'put',put)
    try:
        with TestClient(main.app) as api:
            s['api']=api;s['bid']=s['bids'][0];yield s
    finally:
        assert not s['calls'];gen.close()
def msg(op):
    with connection(True) as c:return c.execute("SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' ORDER BY id LIMIT 1",(UUID(str(op)),)).fetchone()['payload']
def begin(op):
    m=msg(op);lease=engine.acquire(m);assert lease;return m,lease
def finish(op):
    m,lease=begin(op);result=engine.run_compute(m,lease);assert engine.finish(m,lease,result)
    with connection(True) as c:
        assert c.execute('SELECT state FROM operations WHERE id=%s',(UUID(str(op)),)).fetchone()['state']=='succeeded'
        effect=c.execute('SELECT result FROM effect_receipts WHERE operation_id=%s AND effect_key=%s',(UUID(str(op)),m['step_key'])).fetchone()['result']
    return result,effect
def upload(w,raw=None,name='独立施工方案.docx',target=None,source=None):
    raw=raw or docx('独立施工组织设计。施工工期90天，质量目标合格。'+uuid4().hex);api=w['api'];bid=w['bid'];batch=expect(api.post(f'/api/v1/bids/{bid}/upload-batches',json={},headers=w['h']()),201)['id']
    body=dict(batch_id=batch,filename=name,role='body',declared_sha256=sha(raw),byte_size=len(raw),target=target or dict(kind='new_file'))
    if source:body['declared_source_document_version_id']=source
    g=expect(api.post(f'/api/v1/bids/{bid}/uploads',json=body,headers=w['h']()),200);assert g['mode']=='single'
    sent=api.put(g['upload_url'],content=raw,headers=w['h']()|{'Content-Type':DOCX if name.endswith('.docx') else PDF});assert sent.status_code==200
    a=expect(api.post(f'/api/v1/uploads/{g["upload_id"]}/complete',json=dict(parts=[],declared_sha256=sha(raw)),headers=w['h']()),202);_,verified=finish(a['operation']['id']);state=expect(api.get(f'/api/v1/uploads/{g["upload_id"]}',headers=w['h']()),200)
    return g|dict(batch=batch,version=verified['document_version_id'],binding=state['binding_id'],verified=verified,raw=raw)
def commit(w,u):
    api=w['api'];bid=w['bid'];r=api.get(f'/api/v1/bids/{bid}',headers=w['h']());expect(r,200)
    return expect(api.post(f'/api/v1/bids/{bid}/upload-batches/{u["batch"]}/commit',json=dict(accepted_upload_ids=[u['upload_id']],accepted_binding_ids=[u['binding']],explicit_partial_acceptance=False),headers=w['h'](tag=r.headers['etag'])),202)
def material(w,u):return w['api'].get(f'/api/v1/bids/{w["bid"]}/word-materials/{u["version"]}',headers=w['h']())
def original(w,u):return w['api'].get(f'/api/v1/bids/{w["bid"]}/word-originals/{u["version"]}',headers=w['h']())

def test_default_disabled_and_cross_bid_no_original_leak(w,monkeypatch):
    api=w['api'];bid=w['bid'];batch=expect(api.post(f'/api/v1/bids/{bid}/upload-batches',json={},headers=w['h']()),201)['id'];before=len(w['objects']);monkeypatch.delenv('WORD_INTAKE_ENABLED')
    r=api.post(f'/api/v1/bids/{bid}/uploads',json=dict(batch_id=batch,filename='closed.docx',role='body',declared_sha256='a'*64,byte_size=123,target=dict(kind='new_file')),headers=w['h']());assert r.status_code==503 and r.json()['code']=='WORD_INTAKE_DISABLED' and len(w['objects'])==before
    monkeypatch.setenv('WORD_INTAKE_ENABLED','1');u=upload(w)
    for suffix in ['word-originals','word-materials']:
        expect(api.get(f'/api/v1/bids/{w["bids"][1]}/{suffix}/{u["version"]}',headers=w['h']()),404)
    note('disabled_and_cross_bid',disabled_before_storage=True,wrong_bid_reads=2)

@pytest.mark.parametrize('risk',['field','external'])
def test_active_word_saved_byte_exact_without_invoking_renderer(w,monkeypatch,risk):
    raw=docx('独立危险能力反例仍保留原件。',field=risk=='field',external=risk=='external');u=upload(w,raw);calls=[]
    def forbidden(*args,**kwargs):calls.append(1);raise AssertionError('Unsafe document executed conversion')
    monkeypatch.setattr(renderer,'_docker',forbidden)
    finish(u['verified']['conversion_operation_id']);v=expect(original(w,u),200);assert v['original_saved'] and not v['conversion']['controlled_render_verified'];assert not calls
    with connection() as c:
        b=c.execute('SELECT b.object_key FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s',(UUID(u['version']),)).fetchone();assert w['objects'][b['object_key']]==raw
        r=c.execute('SELECT outcome,pdf_document_version_id FROM word_render_runs WHERE operation_id=%s',(UUID(u['verified']['conversion_operation_id']),)).fetchone();assert r['pdf_document_version_id'] is None
    note('active_'+risk,original_sha256=sha(raw),actual_docker_calls=0,no_controlled_pdf=True,gaps=v['conversion']['gaps'])

@pytest.mark.parametrize('timing',['early','late'])
def test_actual_docker_pdf_adoption_real_parse_and_byte_download(w,timing):
    u=upload(w);bid=w['bid']
    if timing=='late': commit(w,u)
    computed,_=finish(u['verified']['conversion_operation_id']);assert computed['outcome']['coverage']=='complete',computed['outcome']
    if timing=='early': parseop=commit(w,u)['operation']['id']
    else:
        adopted=word_coordinator.tick(bid,UUID(u['version']));assert adopted['state']=='adopted';assert word_coordinator.tick(bid,UUID(u['version']))==adopted;parseop=adopted['parse_operation_id']
    view=expect(material(w,u),200);assert view['is_current_converted_pdf'] and view['adoption_state']=='adopted';pdf=UUID(view['adopted_pdf_document_version_id']);finish(parseop)
    with connection() as c:
        row=c.execute('SELECT p.id,p.coverage,p.page_count,b.object_key,b.digest FROM parse_runs p JOIN document_versions d ON d.id=p.document_version_id JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s ORDER BY p.created_at DESC LIMIT 1',(pdf,)).fetchone();assert row['coverage']=='complete' and row['page_count']>0
        quote=' '.join(x['text_content'] for x in c.execute('SELECT text_content FROM evidence_blocks WHERE parse_id=%s',(row['id'],)));assert '90' in quote
        from packages.rag.indexing import original_source_id,parse_source_id
        root=original_source_id(UUID(u['version']));assert c.execute('SELECT 1 FROM retrieval.source_ancestors WHERE source_id=%s AND ancestor_id=%s',(parse_source_id(row['id']),root)).fetchone();assert c.execute('SELECT count(*) n FROM retrieval.chunks WHERE source_id=%s',(root,)).fetchone()['n']==0
    for identity,expected in [(u['version'],u['raw']),(str(pdf),w['objects'][row['object_key']])]:
        grant=expect(w['api'].get(f'/api/v1/artifacts/{identity}/download',headers=w['h']()),200);download=w['api'].get(grant['url'],headers=w['h']());expect_status=download.status_code;assert expect_status==200 and download.content==expected
    note('controlled_'+timing,actual_no_network_docker=True,actual_cpu_parser=True,coverage=row['coverage'],physical_pages=row['page_count'],pdf_sha256=row['digest'],original_and_pdf_download_exact=True,word_root_no_chunks=True,word_pdf_parse_ancestry=True)


def test_corrupted_rendered_bytes_cannot_be_adopted(w):
    u=upload(w);commit(w,u);computed,_=finish(u['verified']['conversion_operation_id']);key=computed['pdf_object_key'];before=expect(material(w,u),200);raw=w['objects'][key];w['objects'][key]=raw+b'tampered'
    with pytest.raises(DomainError) as e:word_coordinator.tick(w['bid'],UUID(u['version']))
    assert e.value.code=='WORD_RENDERED_PDF_CORRUPTED';assert expect(material(w,u),200)['current_body_version_id'] is None
    w['objects'][key]=raw;assert word_coordinator.tick(w['bid'],UUID(u['version']))['state']=='adopted'
    note('adoption_byte_sha',tampered_actual_bytes_rejected=True,restored_exact_bytes_adopted=True)


def test_late_old_render_does_not_replace_new_word(w):
    first=upload(w);commit(w,first);m,lease=begin(first['verified']['conversion_operation_id']);computed=engine.run_compute(m,lease);assert computed['outcome']['coverage']=='complete'
    second=upload(w,target=dict(kind='new_version',file_id=first['file_id'],expected_file_revision=2));commit(w,second);assert engine.finish(m,lease,computed)
    adoption=word_coordinator.tick(w['bid'],UUID(first['version']));assert adoption['state']=='needs_selection';assert expect(material(w,first),200)['current_body_version_id'] is None
    with connection() as c:
        from packages.domain.word_materials import pending_body
        assert str(pending_body(c,w['bid'])['document_version_id'])==second['version']
        assert c.execute('SELECT pdf_document_version_id FROM word_render_runs WHERE operation_id=%s',(UUID(first['verified']['conversion_operation_id']),)).fetchone()['pdf_document_version_id'] is not None
    note('late_old_render',new_word_remains_selected=True,old_pdf_preserved_history=True,no_new_body_adoption=True)


def test_word_ancestor_withdrawal_prevents_adoption(w):
    u=upload(w);commit(w,u);finish(u['verified']['conversion_operation_id'])
    from packages.rag.indexing import original_source_id
    with connection() as c:
        c.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(w['bid'],));c.execute('SELECT pg_advisory_xact_lock(90127,1)');c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(UUID(u['version'])),))
    result=word_coordinator.tick(w['bid'],UUID(u['version']));assert result['state']=='needs_selection';assert expect(material(w,u),200)['current_body_version_id'] is None
    note('withdrawn_ancestor',actual_pg_head_withdrawn=True,adoption=result)


def test_cancel_and_closed_projects_reject_late_publish(w,monkeypatch):
    monkeypatch.delenv('WORD_RENDER_PROFILE_PATH');u=upload(w);m,lease=begin(u['verified']['conversion_operation_id']);computed=engine.run_compute(m,lease);op=m['operation_id'];r=w['api'].get(f'/api/v1/operations/{op}',headers=w['h']());expect(r,200)
    expect(w['api'].post(f'/api/v1/operations/{op}/cancel',json=dict(reason='独立取消，迟到不得发布',cancel_pending_same_input=False),headers=w['h'](tag=r.headers['etag'])),200);assert not engine.finish(m,lease,computed)
    other=upload(w);m2,l2=begin(other['verified']['conversion_operation_id']);c2=engine.run_compute(m2,l2)
    with connection() as c:c.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(w['bid'],));c.execute("UPDATE bid_profiles SET phase='closed' WHERE bid_id=%s",(w['bid'],))
    assert engine.finish(m2,l2,c2)
    with connection() as c:
        for identity in [op,m2['operation_id']]:
            row=c.execute('SELECT outcome,pdf_document_version_id FROM word_render_runs WHERE operation_id=%s',(UUID(str(identity)),)).fetchone();assert row['outcome'] is None and row['pdf_document_version_id'] is None
    note('cancel_close',cancelled_finish_rejected=True,closed_worker_failed_no_pdf=True,profile='explicit disabled renderer for race mechanism')


def test_manual_pdf_recovery_unverified_and_old_pdf_not_reused(w,monkeypatch):
    monkeypatch.delenv('WORD_RENDER_PROFILE_PATH');u=upload(w);commit(w,u);finish(u['verified']['conversion_operation_id']);assert expect(material(w,u),200)['current_body_version_id'] is None
    raw=Path('/workspace/tender-docs/04-验证与测试/historical-input-independent-checks/application01/fixed-page-inputs/body.pdf').read_bytes();pdf=upload(w,raw,name='人工补传同稿.pdf',source=u['version']);commit(w,pdf)
    with connection() as c:
        rel=c.execute('SELECT kind,verification FROM document_relations WHERE document_version_id=%s',(UUID(pdf['version']),)).fetchone();assert dict(rel)==dict(kind='user_declared_same_draft',verification='unverified')
    v=expect(original(w,u),200);assert not v['conversion']['controlled_render_verified'];new=upload(w);commit(w,new);assert expect(material(w,new),200)['current_body_version_id'] is None
    with connection() as c:assert c.execute('SELECT id FROM document_versions WHERE id=%s',(UUID(pdf['version']),)).fetchone()
    note('manual_pdf_and_new_word',manual_pdf_relation='unverified',new_word_clears_current_pdf=True,prior_pdf_preserved_history=True)

@pytest.fixture(scope='session',autouse=True)
def identities():
    manifest=json.loads((ROOT/'source-preview-02.json').read_text())['sources'];assert len(manifest)==335
    for x in manifest:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    from apps.api.export_openapi import implemented_openapi
    actual=implemented_openapi(main.app);assert actual==json.loads((ROOT/'actual-02.openapi.json').read_text())
    yield
    for x in manifest:assert hashlib.sha256((RUNTIME/x['path']).read_bytes()).hexdigest()==x['sha256']
    loaded=[]
    for name,mod in sorted(sys.modules.items()):
        p=getattr(mod,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(RUNTIME),name;loaded.append(dict(module=name,path=str(p.relative_to(RUNTIME)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_WORD_ROUND','execution');out.mkdir(exist_ok=True);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,loaded_modules=loaded,actual_matches_candidate=True,model_calls=0,cloud_requests=0,consumers=0),ensure_ascii=False,indent=2)+'\n')
