"""Frozen lifecycle scope; nonowner PG and explicit in-memory objects only."""
from pathlib import Path
import hashlib,json,os,sys
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
BASE=HERE.parents[1]/'lifecycle-development-checks/authoring-knowledge-02'
p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='a733281214f64553094a9800f0719581ff103796a8002aee2b373432b8c534cf'
m=json.loads(p.read_text());ROOT=Path(m['runtime_root'])
def verify():
    for e in m['sources']:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256'],e['path']
verify();sys.path[:0]=[str(ROOT),str(ROOT/'tests')];os.chdir(ROOT)
os.environ.update(RUN_AUTHORING_KNOWLEDGE_LIFECYCLE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
for key in list(os.environ):
    if any(s in key for s in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
from packages.domain import runtime,storage
from packages.authoring import repository,build_repository
from packages.knowledge import bridge,rag_search
runtime.ROOT=ROOT;runtime.assert_role();runtime.assert_role(True)
text=(ROOT/'tests/test_authoring_knowledge_lifecycle.py').read_text()+r'''

@pytest.mark.parametrize('mode',['closed','terminated','archived'])
def test_architect_late_worker_publish_has_no_metadata_effect_after_close(draft,monkeypatch,mode):
    from packages.authoring import build_repository as br
    from packages.domain.runtime import Command,enqueue_plan
    actor,bid,draft_id,saved,section=draft
    with connection() as c:
        command=Command(c,actor,'architect-late-build',bid,uuid4().hex,{})
        input_id,manifest=br.freeze_build(c,bid,{'draft_id':str(draft_id),'draft_revision':1,'formats':['docx'],'follow_up':'none','template_version_id':str(br.DEFAULT_TEMPLATE_ID)},actor)
        operation=enqueue_plan(c,bid,command.id,'build',{'id':str(input_id),'manifest':manifest},stages=[{'key':'build-document','depends_on':[]}],handler_version='export/1',topic='tender.task.export.v1')
        build_id=uuid4();c.execute('INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)',(build_id,bid,input_id,UUID(operation['id'])))
        command.finish({'build_id':str(build_id)},202)
        before=c.execute('SELECT count(*) AS n FROM document_versions WHERE bid_id=%s',(bid,)).fetchone()['n']
    close(bid,mode)
    computed={'input_id':str(input_id),'manifest':manifest,'outputs':[{'format':'docx','state':'succeeded','gaps':[],'object_key':'architect-not-a-live-object','digest':'a'*64,'byte_size':32,'media_type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}]}
    with pytest.raises(DomainError) as error,connection(True) as worker:
        br.publish_build(worker,bid,build_id,computed)
    assert error.value.code=='PROJECT_WORK_CLOSED'
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM document_versions WHERE bid_id=%s',(bid,)).fetchone()['n']==before
        assert c.execute('SELECT count(*) AS n FROM build_outputs WHERE build_id=%s',(build_id,)).fetchone()['n']==0

def test_architect_direct_ports_reject_old_repeatable_read_before_work(draft):
    actor,bid,draft_id,_,section=draft
    for call in (lambda c:create_draft(c,bid,actor,'不得在旧快照创建'),lambda c:save_draft(c,bid,draft_id,actor,change(section,'旧快照保存'),etag('draft',draft_id,1))):
        with pytest.raises(DomainError) as error,connection() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            call(c)
        assert error.value.code=='CURRENT_AUTHORITY_REQUIRES_READ_COMMITTED'
    with connection() as c:assert draft_view(c,bid,draft_id)['revision']==1
'''
(HERE/'fixed-plus-delta-tests.py').write_text(text)
import httpx,pytest
def forbidden(*a,**k):raise AssertionError('External HTTP/object port forbidden')
with patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden),patch.object(httpx.HTTPTransport,'handle_request',forbidden):
    code=pytest.main([str(HERE/'fixed-plus-delta-tests.py'),'-q','--assert=plain','--tb=short','--junitxml='+str(HERE/'independent-junit.xml')])
verify()
(HERE/'result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'runtime_files':len(m['sources']),'reverified_after_execution':True,'pytest_exit':int(code),'actual_nonowner_PG':True,'editor_auth':'explicit fixture stub; not new auth acceptance','objects':'explicit memory only; default ports blocked','dense':'explicit synthetic mechanism vector; not model quality','paid_calls':0,'object_requests':0,'consumers':0,'DDL':0,'loaded_sources':{mod.__name__:mod.__file__ for mod in (repository,build_repository,bridge,rag_search)}},ensure_ascii=False,indent=2)+'\n')
assert code==0
