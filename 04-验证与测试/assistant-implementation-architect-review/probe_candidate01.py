"""Run the fixed assistant component with nonowner PG; explicitly block live HTTP/object ports."""
from pathlib import Path
import os,sys,json,hashlib
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'assistant-development-checks/implementation-01'
candidate=BASE/'candidate.json'
assert hashlib.sha256(candidate.read_bytes()).hexdigest()=='9f9ae6f12aa95127458d53e8218918f3d7b2519d2ed70897bd0e94d45d652f97'
manifest=json.loads(candidate.read_text());ROOT=BASE/'frozen-tree'
def verify():
    for entry in manifest['sources']+manifest['dependencies']:
        assert hashlib.sha256((BASE/entry['archive']).read_bytes()).hexdigest()==entry['sha256'],entry['path']
verify()
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.chdir(ROOT)
for key in list(os.environ):
    if any(s in key for s in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
os.environ.update(RUN_ASSISTANT_INTEGRATION='1',RUN_WORK_CONVERSATIONS='1',RUN_AUTHORING_INTEGRATION='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
from packages.domain import runtime,storage
from packages.assistant import actions,global_runtime,coordinator
runtime.ROOT=ROOT
runtime.assert_role();runtime.assert_role(True)
import httpx,pytest
def forbidden(*args,**kwargs):raise AssertionError('Live HTTP/object port forbidden by independent assistant probe')
files=['test_assistant_integration.py','test_work_conversations.py','test_authoring_integration.py','test_assistant.py']
with patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden),patch.object(httpx.HTTPTransport,'handle_request',forbidden),patch.object(httpx.AsyncHTTPTransport,'handle_async_request',forbidden):
    code=pytest.main([str(ROOT/'tests'/name) for name in files]+['-q','-k','not real_worker_build and not real_authenticated_http and not actual_build_http','--assert=plain','--tb=short','--junitxml='+str(HERE/'candidate01-independent-junit.xml')])
verify()
(HERE/'candidate01-independent-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'sources_verified':len(manifest['sources']),'dependencies_verified':len(manifest['dependencies']),'verified_after_execution':True,'pytest_exit':int(code),'actual_nonowner_PG':True,'model_transport':'explicit httpx.MockTransport only; live HTTP blocked','object_transport':'explicit per-test memory only; default ports blocked','consumers_started':False,'DDL_executed':False,'paid_calls':0,'loaded_sources':{m.__name__:m.__file__ for m in (actions,global_runtime,coordinator)}},ensure_ascii=False,indent=2)+'\n')
assert code==0
