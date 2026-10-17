"""Frozen release application suite in approved new nonowner checks DB; no native ports."""
import os,sys,json,hashlib,importlib
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'research-backend-checks/fr029-release/candidate-01';EXEC=HERE/'application01-runtime';EXEC.mkdir(exist_ok=True)
p=BASE/'model-release-candidate-01.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='f4306991dafab46f6e1c9364132c0617ecfaeda16692bf2f02828df6da76e865';m=json.loads(p.read_text())
for section,root in [('reference_inputs',BASE/'reference-inputs'),('sources',BASE/'frozen-inputs')]:
 for e in m[section]:
  b=(root/e['path']).read_bytes();assert hashlib.sha256(b).hexdigest()==e['sha256'];dest=EXEC/e['path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b)
schema=APP/'packages/contracts/schemas/protocol.schema.json'
raw=schema.read_bytes();dst=EXEC/'packages/contracts/schemas/protocol.schema.json';dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
(HERE/'application01-public-schema-auxiliary.json').write_text(json.dumps({'path':str(schema),'sha256':hashlib.sha256(raw).hexdigest(),'purpose':'public OutcomeRequest validator used by synthetic research fixture; absent from candidate reference list'},ensure_ascii=False,indent=2)+'\n')
sys.path[:0]=[str(EXEC),str(APP)]
for key in list(os.environ):
 if any(x in key for x in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
os.environ.update(RUN_MODEL_RELEASE_INTEGRATION='1',RUN_MODEL_GOVERNANCE_INTEGRATION='1',RUN_RESEARCH_INTEGRATION='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
for name in ('apps.api','packages.domain','packages.research','packages.identity','packages.model_governance','packages.rag','tests'):
 mod=importlib.import_module(name);mod.__path__=[str(EXEC/name.replace('.','/')),*mod.__path__]
from packages.domain import runtime,storage
runtime.ROOT=EXEC;runtime.assert_role()
from packages.model_governance import registry,purpose_profiles
from apps.api import model_registry_routes
original=purpose_profiles.read_profile
# Only selected profile location changes: jobs, API and CPU all use the PM-approved
# delivery_protocols01 environment, never the developer's model_release01 database.
def approved_profile(role,path):
 return original(role,str(path).replace('.local/model_release01-model_','.local/delivery_protocols01-model_'))
purpose_profiles.read_profile=approved_profile
import pytest
def forbidden(*a,**k):raise AssertionError('native object port forbidden in architect release probe')
with patch.object(storage,'client',forbidden):
 code=pytest.main([str(EXEC/'tests'/name) for name in ('test_model_governance_artifacts.py','test_model_governance_purpose_profiles.py','test_model_governance_registry_contracts.py','test_model_governance_registry_integration.py')]+['-q','--assert=plain','--tb=short','--junitxml='+str(HERE/'application01-independent-junit.xml')]);assert code==0
for section,root in [('reference_inputs',BASE/'reference-inputs'),('sources',BASE/'frozen-inputs')]:
 for e in m[section]:assert hashlib.sha256((root/e['path']).read_bytes()).hexdigest()==e['sha256']
(HERE/'application01-independent-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'primary_files':len(m['sources']),'reference_files':len(m['reference_inputs']),'actual_nonowner_PG_ASGI_and_isolated_CPU':True,'profile_location_only_substituted':'delivery_protocols01 for API and CPU train/evaluate','object_port':'explicit in-memory','model_port':'explicit httpx.MockTransport, not paid provider','loaded_source':{'registry':registry.__file__,'routes':model_registry_routes.__file__,'purpose_profiles':purpose_profiles.__file__},'paid_calls':0,'native_OSS':0,'consumers_started':False},ensure_ascii=False,indent=2)+'\n')
