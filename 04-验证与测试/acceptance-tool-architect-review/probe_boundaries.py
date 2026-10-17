"""只读工具与无网络离线边界探针；不调用execute。"""
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory
from uuid import uuid4
import hashlib,importlib.util,json,sys
APP=Path('/workspace/tender-workbench');HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(APP));original=Path.read_text
with patch.object(Path,'read_text',lambda p,*a,**kw:'' if p.resolve()==APP/'.env' else original(p,*a,**kw)):
    spec=importlib.util.spec_from_file_location('architect_acceptance',APP/'deploy/review_acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
source=APP/'deploy/review_acceptance.py';source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
with TemporaryDirectory() as d:
    candidate=Path(d)/'candidate.json'
    candidate.write_text(json.dumps({'sources':[{'path':'deploy/review_acceptance.py','sha256':source_sha}]}))
    verify=module.frozen_sources(candidate);before=hashlib.sha256(candidate.read_bytes()).hexdigest()
    candidate.write_text(json.dumps({'sources':[{'path':'deploy/review_acceptance.py','sha256':source_sha}],'changed_candidate_identity':True}))
    after=hashlib.sha256(candidate.read_bytes()).hexdigest();verify();assert before!=after
    changed_file_still_passes=True
clock=[179.0];entered=[]
def upstream(*a,**kw):entered.append(True);clock[0]=181.0;return 'finished_after_deadline'
with patch.object(module.time,'monotonic',lambda:clock[0]):
    boundary=module.AcceptanceBoundary(uuid4(),upstream,lambda:None,deadline=180.0)
    response=boundary({'bid_id':str(boundary.bid_id)}, {},logical_key='extract-rules',messages=[{'role':'user','content':'synthetic'}],json_schema={},max_tokens=1)
assert response=='finished_after_deadline' and entered==[True]
result={'source_sha256':source_sha,'execute_called':False,'paid_calls':0,'network_requests':0,'credentials_read':False,'candidate_json_changed_verify_still_passes':changed_file_still_passes,'incomplete_manifest_only_tool_source_accepted':True,'deadline_semantics':'admission cutoff only; invocation starts at179 finishes181 without rejection','complete_source_dependency_pin_implemented':False}
(HERE/'initial-boundary-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
