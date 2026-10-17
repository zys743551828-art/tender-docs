"""Frozen06 dismissal basis, real nonowner PG mechanisms; no provider/storage I/O."""
from pathlib import Path
import os,sys,json,hashlib
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=APP/'docs/content-decision-applicability-development-checks';p=BASE/'candidate-06.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='b86d5c3c818c18b09ad42720f318b61852a99737a82bac017637d7aec08cd60f';m=json.loads(p.read_text());ROOT=Path(m['fixed_runtime_root'])
for e in m['runtime_sources']:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
for e in m['sources']:assert hashlib.sha256(Path(e['archive']).read_bytes()).hexdigest()==e['sha256']
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.update(RUN_CONTENT_DECISION_APPLICABILITY_INTEGRATION='1',RUN_CONTENT_FINDINGS_INTEGRATION='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
for key in list(os.environ):
 if any(x in key for x in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
from packages.domain import runtime,storage,engineering
from packages.domain import content_decision_basis,finding_lifecycle
from apps.worker import content_handlers
runtime.assert_role();runtime.assert_role(True)
assert Path(engineering.__file__).is_relative_to(ROOT)
from uuid import uuid4
quote='该说明并非工程A标段一工期90天，而是工程B标段二工期120天。';id=str(uuid4());item={'evidence_id':id,'quote':quote,'subject':'工程A','attribute':'工期','scope':'标段一','value':'90','unit':'天','relation':'equal'}
accepted=engineering.claim(item,{id:{'text_content':quote}},'assertion');assert accepted['normalized']=='90'
(HERE/'candidate06-negation-observation.json').write_text(json.dumps({'reproduced':True,'kind':'P2 bounded local grounding observation, not dismissal P1 regression','quote':quote,'accepted_subject':'工程A','accepted_scope':'标段一','accepted_value':'90','expected':'explicit negated context should be diagnostic/partial','mode':'pure synthetic, no model quality claim'},ensure_ascii=False,indent=2)+'\n')
import pytest
def forbidden(*a,**k):raise AssertionError('external object port forbidden in architect06')
with patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden):
 code=pytest.main([str(ROOT/'tests'/name) for name in ('test_content_decision_applicability_integration.py','test_content_findings_integration.py','test_engineering.py','test_content_checks.py')]+['-q','--assert=plain','--tb=short','--junitxml='+str(HERE/'candidate06-independent-junit.xml')]);assert code==0
for e in m['runtime_sources']:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
(HERE/'candidate06-independent-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'sources':len(m['sources']),'fixed_runtime_files':len(m['runtime_sources']),'actual_nonowner_PG':True,'synthetic_SQL_evidence':True,'loaded_sources':{m.__name__:m.__file__ for m in (engineering,content_decision_basis,finding_lifecycle,content_handlers)},'paid_calls':0,'object_requests':0,'consumers_started':False,'DDL_executed':False},ensure_ascii=False,indent=2)+'\n')
