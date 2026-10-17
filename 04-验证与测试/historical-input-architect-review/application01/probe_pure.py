"""Fixed historical-input application pure checks; no DB/model/object/process ports."""
from pathlib import Path
import hashlib,json,os,sys
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;DOCS=HERE.parents[1]
BASE=DOCS/'research-backend-checks/historical-input/candidate-01'
p=BASE/'historical-input-candidate-01.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='3d917c3190eca381e43d647bbf7a29422faebb1653605a45784a0a76e10fba45';m=json.loads(p.read_text())
ROOT=HERE/'runtime';ROOT.mkdir(exist_ok=True)
parent=DOCS/'backend07-independent-checks';mp=parent/'runtime-manifest.json';assert hashlib.sha256(mp.read_bytes()).hexdigest()=='71fe0fb2c9dbde7d85e62d20bef2da8e188e59ac00e9eb056cc021a1bd75274e'
records={}
for e in json.loads(mp.read_text()):
    source=parent/'execution-inputs'/e['path'];assert hashlib.sha256(source.read_bytes()).hexdigest()==e['sha256'];target=ROOT/e['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes());records[e['path']]={'path':e['path'],'sha256':e['sha256'],'layer':'fixed backend07 dependency supplement'}
for e in m['sources']+m['reference_inputs']:
    source=BASE/'runtime'/e['path'];assert hashlib.sha256(source.read_bytes()).hexdigest()==e['sha256'];target=ROOT/e['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes());records[e['path']]={'path':e['path'],'sha256':e['sha256'],'layer':'candidate own source/reference'}
(HERE/'execution-manifest.json').write_text(json.dumps(list(records.values()),ensure_ascii=False,indent=2)+'\n')
sys.path[:0]=[str(ROOT),str(ROOT/'tests')];os.chdir(ROOT);os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
for key in list(os.environ):
    if any(s in key for s in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY','DATABASE_DSN')):os.environ.pop(key,None)
from packages.domain import runtime,storage
from packages.model_governance import historical_input
import pytest,httpx
def forbidden(*a,**k):raise AssertionError('DB/remote/object port forbidden in pure historical-input review')
with patch.object(runtime,'connection',forbidden),patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden),patch.object(httpx.HTTPTransport,'handle_request',forbidden):
    code=pytest.main([str(ROOT/'tests/test_historical_input.py'),'-q','--assert=plain','--tb=short','-c','/dev/null','--rootdir',str(ROOT),'--junitxml='+str(HERE/'pure-independent-junit.xml')])
for e in records.values():assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
(HERE/'pure-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'own_sources':len(m['sources']),'candidate_refs':len(m['reference_inputs']),'execution_closure_files':len(records),'pytest_exit':int(code),'loaded_source':historical_input.__file__,'DB_queries':0,'object_requests':0,'model_calls':0,'consumers':0,'DDL':0},ensure_ascii=False,indent=2)+'\n');assert code==0
