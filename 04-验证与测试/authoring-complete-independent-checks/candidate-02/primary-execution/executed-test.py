"""Independent actual PAGE export: restricted PG/ASGI, complete Memory, no-network Docker."""
import copy,hashlib,importlib.util,io,json,os,re,sys,unicodedata,zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID,uuid4
from xml.etree import ElementTree as ET
import httpx,requests,pypdfium2,pytest
from fastapi.testclient import TestClient
ROOT=Path(os.environ['QA_ASSISTANT_ROOT']);RUNTIME=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/candidate-02/frozen-tree')
spec=importlib.util.spec_from_file_location('independent_authoring_fixture',ROOT/'qa-helpers/fixture.py');fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
from apps.api import main
from apps.api.authoring_structure import router as structure_router
from apps.worker import engine,export_handlers
from packages.authoring import generated_renderer,snapshot_structure
from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.repository import create_draft,save_draft,freeze,latest_sections,snapshot_content
from packages.domain.runtime import connection,etag,sha,DomainError
from packages.domain import storage
from packages.word_intake import renderer as sandbox
OBS=[]
def note(name,**detail):OBS.append(dict(name=name,**detail))
def expect(r,status):return fixture.expect(r,status)
@pytest.fixture(scope='session')
def profile():
    value=generated_renderer.profile_for(sandbox.build_profile());p=ROOT/'actual-generated-profile.json';p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600);return p,value
@pytest.fixture
def w(monkeypatch,profile):
    gen=fixture.s.__wrapped__(monkeypatch);w=next(gen)
    def blocked(*a,**kw):raise AssertionError('Independent PAGE QA blocks external HTTP/model')
    async def async_blocked(*a,**kw):blocked()
    monkeypatch.setattr(requests.sessions.Session,'request',blocked);monkeypatch.setattr(httpx.HTTPTransport,'handle_request',blocked);monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',async_blocked)
    from packages.domain.providers.deepseek import DeepSeekClient
    monkeypatch.setattr(DeepSeekClient,'from_env',blocked);monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False);monkeypatch.delenv('PARSING_RUNTIME_TOKEN',raising=False)
    monkeypatch.setenv('AUTHORING_GENERATED_RENDER_PROFILE_PATH',str(profile[0]));w['profile']=profile[1]
    class Memory:
        def get_object(self,*,Bucket,Key):
            raw=w['objects'][Key];return dict(Body=io.BytesIO(raw),ContentLength=len(raw))
        def head_object(self,*,Bucket,Key):return dict(ContentLength=len(w['objects'][Key]))
    monkeypatch.setattr(storage,'client',Memory)
    with connection() as c:
        bid=w['bids'][0];actor=w['actors'][0];draft,old=create_draft(c,bid,actor,'独立正文页码验收');section=c.execute('SELECT * FROM section_revisions WHERE draft_id=%s',(draft,)).fetchone();content=copy.deepcopy(section['content']);content['content']=[dict(type='paragraph',content=[dict(type='text',text=f'独立施工措施序号{2000+n}；工程年度2027，承诺工期90天，质量合格，逐项检查安全措施与现场管理。')]) for n in range(110)]
        saved=save_draft(c,bid,draft,actor,dict(base_draft_revision=1,changes=[dict(section_id=str(section['section_id']),base_section_revision=1,schema_digest=SCHEMA_DIGEST,content=content)]),etag('draft',draft,1))
        state=snapshot_structure.reconcile(None,latest_sections(c,bid,draft,include_deleted=True));c.execute('UPDATE drafts SET revision=3 WHERE id=%s',(draft,));saved=freeze(c,bid,draft,3,actor=actor,structure_state=state)
    w.update(bid=bid,actor=actor,draft=draft,section=section,saved=saved,content=content)
    try:
        with TestClient(main.app) as api:w['api']=api;yield w
    finally:assert not w['calls'];gen.close()
def queue(w,follow='none'):
    with connection() as c:snap=snapshot_content(c,w['bid'],UUID(w['saved']['snapshot_id']))
    ext=snap['authoring_structure'];body=dict(draft_id=str(w['draft']),draft_revision=w['saved']['draft_revision'],directory_template_version_id=ext['directory_template']['id'],style_template_version_id=ext['style_template']['id'],formats=['docx','pdf'],follow_up=follow)
    ctx=w['api'].get(f'/api/v1/bids/{w["bid"]}',headers=w['h']());expect(ctx,200)
    accepted=expect(w['api'].post(f'/api/v1/bids/{w["bid"]}/builds',json=body,headers=w['h'](tag=ctx.headers['etag'])),202)
    with connection() as c:m=c.execute("SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.export.v1'",(UUID(accepted['operation']['id']),)).fetchone()['payload']
    lease=engine.acquire(m);assert lease;return UUID(accepted['resource_id']),m,lease

def compute(w,message,lease,monkeypatch):
    opened=[];render=generated_renderer.render;original=export_handlers.connection
    @contextmanager
    def observed(*a,**kw):
        with original(*a,**kw) as c:opened.append(c);yield c
    def actual(*a,**kw):
        assert opened and all(x.closed for x in opened)
        with connection(True) as c:assert not c.execute("SELECT pid FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchall()
        return render(*a,**kw)
    monkeypatch.setattr(export_handlers,'connection',observed);monkeypatch.setattr(generated_renderer,'render',actual)
    result=engine.run_compute(message,lease);assert result['render_coverage']=='complete',result;w['computed']=result;return result

def inspect_outputs(w,result,label):
    outputs={x['format']:x for x in result['outputs']};pdf=w['objects'][outputs['pdf']['object_key']];docx=w['objects'][outputs['docx']['object_key']]
    d=ROOT/'own-artifacts'/label;d.mkdir(parents=True,exist_ok=True);(d/'body.pdf').write_bytes(pdf);(d/'body.docx').write_bytes(docx)
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(docx)) as z:root=ET.fromstring(z.read('word/document.xml'));expected=''.join(x.text or '' for x in root.findall('.//w:body//w:t',ns))
    pages=[];body=[]
    document=pypdfium2.PdfDocument(pdf)
    try:
        assert len(document)>=4
        for index in range(len(document)):
            page=document[index];text=page.get_textpage();main_text=[];footer=[]
            for pos in range(text.count_chars()):
                ch=text.get_text_range(index=pos,count=1)
                if not ch or ch.isspace():continue
                left,bottom,right,top=text.get_charbox(pos)
                if top<55:footer.append(ch)
                elif bottom<page.get_height()-55:main_text.append(ch)
            assert ''.join(footer)==str(index+1)
            pages.append(dict(physical_page=index+1,footer=''.join(footer)));body+=main_text;text.close();page.close()
    finally:document.close()
    compact=lambda x:''.join(unicodedata.normalize('NFKC',x).split())
    assert compact(''.join(body))==compact(expected)
    assert Counter(c for c in ''.join(body) if c.isdigit())==Counter(c for c in expected if c.isdigit())
    evidence=dict(physical_pages=pages,docx_sha256=sha(docx),pdf_sha256=sha(pdf),body_text_sha256=sha(compact(''.join(body))),body_digits=len([x for x in expected if x.isdigit()]),expected_digest=sha(compact(expected)))
    (d/'readback.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n');return evidence


def test_real_http_registered_export_PAGE_full_body_and_no_auto_adoption(w,monkeypatch):
    build,m,lease=queue(w);result=compute(w,m,lease,monkeypatch);evidence=inspect_outputs(w,result,'positive');assert engine.finish(m,lease,result);assert engine.acquire(m) is None
    with connection() as c:
        rows=c.execute('SELECT format,state,document_version_id,gaps FROM build_outputs WHERE build_id=%s',(build,)).fetchall();assert len(rows)==2 and all(x['state']=='succeeded' for x in rows);assert c.execute('SELECT current_body_id FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()['current_body_id'] is None
        relation=c.execute("SELECT kind,verification FROM document_relations WHERE document_version_id=%s",(next(x['document_version_id'] for x in rows if x['format']=='pdf'),)).fetchone();assert dict(relation)==dict(kind='controlled_render',verification='verified')
        gaps={g for x in rows for g in x['gaps']};assert 'TOC_PAGE_NUMBERS_UNKNOWN' in gaps and 'ACTUAL_SECTION_PAGE_RANGES_UNKNOWN' in gaps
    expect(w['api'].get('/api/v1/builds/'+str(build),headers=w['h']()),200)
    note('actual_HTTP_registered_export',**evidence,connections_closed_before_Docker=True,one_effect_and_duplicate_not_acquired=True,no_automatic_body_adoption=True,explicit_unknown_gaps=sorted(gaps))


def test_actual_cancel_after_compute_does_not_publish(w,monkeypatch):
    build,m,lease=queue(w);result=compute(w,m,lease,monkeypatch);op=m['operation_id'];current=w['api'].get('/api/v1/operations/'+op,headers=w['h']());expect(current,200)
    expect(w['api'].post('/api/v1/operations/'+op+'/cancel',json=dict(reason='独立构建后取消不得迟到发布',cancel_pending_same_input=False),headers=w['h'](tag=current.headers['etag'])),200);assert not engine.finish(m,lease,result)
    with connection() as c:
        assert not c.execute('SELECT 1 FROM build_outputs WHERE build_id=%s',(build,)).fetchone();assert not c.execute('SELECT 1 FROM effect_receipts WHERE operation_id=%s',(UUID(op),)).fetchone()
    note('cancel_late',actual_render_complete=True,outputs=0,effects=0)


def test_edit_after_compute_keeps_original_artifacts_only_history(w,monkeypatch):
    from packages.domain.build_followups import tick
    build,m,lease=queue(w,'review');result=compute(w,m,lease,monkeypatch)
    new=copy.deepcopy(w['content']);new['content']=[dict(type='paragraph',content=[dict(type='text',text='后续修改9999，不能由旧构建自动替换。')])]
    request=dict(base_draft_revision=w['saved']['draft_revision'],changes=[dict(section_id=str(w['section']['section_id']),base_section_revision=2,schema_digest=SCHEMA_DIGEST,content=new)])
    expect(w['api'].patch(f'/api/v1/bids/{w["bid"]}/drafts/{w["draft"]}',json=request,headers=w['h'](tag=etag('draft',w['draft'],w['saved']['draft_revision']))),200)
    assert engine.finish(m,lease,result);out=tick(build);assert out['state']=='needs_selection' and out['adopted_document_version_id'] is None
    with connection() as c:
        assert c.execute("SELECT count(*) n FROM build_outputs WHERE build_id=%s AND state='succeeded'",(build,)).fetchone()['n']==2
        assert c.execute('SELECT current_body_id FROM bid_profiles WHERE bid_id=%s',(w['bid'],)).fetchone()['current_body_id'] is None
    note('late_editor_revision',frozen_outputs_retained=2,new_body_not_adopted=True,follow_up=out['state'])


def test_plan_source_ancestor_withdrawal_after_real_compute_no_output_publish(w,monkeypatch):
    from packages.authoring.structure_repository import save_plan
    from packages.rag.indexing import ensure_parse_sources
    raw=Path('/workspace/tender-docs/04-验证与测试/historical-input-independent-checks/application01/fixed-page-inputs/body.pdf').read_bytes();blob,doc,parse=uuid4(),uuid4(),uuid4();key='independent-A2-source/'+uuid4().hex;w['objects'][key]=raw
    with connection() as c:
        c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",(blob,sha(raw),key,len(raw)));c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'独立计划依据.pdf',clock_timestamp(),'source_original',%s)",(doc,w['bid'],blob,w['actor']));c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,2,'complete','[]')",(parse,w['bid'],doc,sha('explicit independent PDFium source fixture'),sha({})))
        pdf=pypdfium2.PdfDocument(raw);evidence=None
        for index in range(len(pdf)):
            page=pdf[index];textpage=page.get_textpage();text=textpage.get_text_range();ev=uuid4();c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,%s,%s,0,'usable','[]')",(w['bid'],parse,index+1,page.get_width(),page.get_height()));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,%s,0,'text',%s,%s)",(ev,w['bid'],parse,index+1,text,sha(text.encode())));evidence=evidence or ev;textpage.close();page.close()
        pdf.close();root,source=ensure_parse_sources(c,w['bid'],parse,publish_readable=True);revision=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(source,)).fetchone()['revision'];ref=dict(source_id=str(source),source_revision=revision,evidence_id=str(evidence),document_version_id=str(doc))
        w['saved']=save_plan(c,w['bid'],w['draft'],w['section']['section_id'],w['actor'],dict(base_draft_revision=w['saved']['draft_revision'],base_plan_revision=0,state='drafting',goal='独立最终来源守卫',key_points=[],rule_ids=[],source_refs=[ref],pending_facts=[],planned_pages=None,budget_basis='manual',budget_rule_id=None,budget_note=''),etag('draft',w['draft'],w['saved']['draft_revision']))
    build,m,lease=queue(w);result=compute(w,m,lease,monkeypatch)
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(90127,1)');changed=c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(root,));assert changed.rowcount==1
    with pytest.raises(DomainError) as error:engine.finish(m,lease,result)
    assert error.value.code=='AUTHORING_SOURCE_STALE'
    with connection() as c:
        assert not c.execute('SELECT 1 FROM build_outputs WHERE build_id=%s',(build,)).fetchone();assert not c.execute('SELECT 1 FROM effect_receipts WHERE operation_id=%s',(UUID(m['operation_id']),)).fetchone()
    note('source_withdrawal_late',real_pdf_original_bytes=True,source_fixture='actual PDFium local parse explicitly published metadata, not Parser03 accuracy acceptance',outputs=0,effects=0)

@pytest.fixture(scope='session',autouse=True)
def identity():
    candidate=json.loads((ROOT/'candidate.json').read_text());rows=candidate['sources']+candidate['dependencies'];assert len(rows)==335
    for x in rows:assert hashlib.sha256(Path(x['archive']).read_bytes()).hexdigest()==x['sha256']
    main.app.include_router(structure_router)
    from apps.api.export_openapi import implemented_openapi
    from packages.authoring.structure_contracts import extend_openapi
    actual=extend_openapi(implemented_openapi(main.app));assert actual==json.loads((ROOT/'actual.openapi.json').read_text())
    yield
    for x in rows:assert hashlib.sha256(Path(x['archive']).read_bytes()).hexdigest()==x['sha256']
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        path=getattr(module,'__file__',None)
        if path and name.startswith(('apps.','packages.')):
            p=Path(path).resolve();assert p.is_relative_to(RUNTIME),name;loaded.append(dict(module=name,path=str(p.relative_to(RUNTIME)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_AUTHORING_ROUND','execution');out.mkdir(exist_ok=True);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'observations.json').write_text(json.dumps(dict(observations=OBS,loaded_modules=loaded,actual_matches_frozen=True,remote_http='hard blocked',model_calls=0,cloud_calls=0,consumers=0,composition='Fixed main plus explicit structure router in TestClient; no deployed/live claim'),ensure_ascii=False,indent=2)+'\n')
