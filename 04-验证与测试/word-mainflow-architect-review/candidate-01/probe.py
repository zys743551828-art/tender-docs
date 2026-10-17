from pathlib import Path
import json,hashlib,sys,socket,subprocess,os
out=Path(__file__).parent
p=Path('/workspace/tender-workbench/docs/word-mainflow-development-checks/candidate-01.json');m=json.loads(p.read_text())
assert hashlib.sha256(p.read_bytes()).hexdigest()=='9b609a89975c31ab945aacbbe8f63639edd37d51b622ee92c788146270cd7168'
mp=Path(m['runtime_manifest']['path']);assert hashlib.sha256(mp.read_bytes()).hexdigest()==m['runtime_manifest']['sha256'];sm=json.loads(mp.read_text());root=Path(m['fixed_runtime_root'])
for v in sm['sources']:assert hashlib.sha256((root/v['path']).read_bytes()).hexdigest()==v['sha256'],v['path']
for v in m['own_sources']:assert hashlib.sha256((p.parent/v['frozen_path']).read_bytes()).hexdigest()==v['sha256']
qa=Path('/workspace/tender-docs/04-验证与测试/word-mainflow-independent-checks/candidate-01/final-result.json');qm=json.loads(qa.read_text());assert qm['candidate_sha256']==hashlib.sha256(p.read_bytes()).hexdigest()
for v in qm['executions']:assert hashlib.sha256((qa.parent/v['path']).read_bytes()).hexdigest()==v['sha256']
for k in ['actual','renderer_profile_public','word03','word_request_order04','base','handoff']:
 v=m[k];assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256']
# All runtime imports resolve solely to exact frozen root, no process/network/database allowed.
sys.path.insert(0,str(root))
def forbidden(*a,**kw):raise AssertionError('No remote, process or DB calls in structural review')
subprocess.Popen=forbidden;subprocess.run=forbidden;socket.create_connection=forbidden
import psycopg
psycopg.connect=forbidden
from packages.domain import word_materials,storage
from packages.domain.runtime import DomainError,sha
raw=b'%PDF-1.4\nexplicit synthetic offline bytes only'
value={'object_key':'architect-local-fixture','byte_size':len(raw),'digest':sha(raw)}
checks=[]
for name,payload,size in [('exact',raw,len(raw)),('same_size_changed',raw[:-1]+b'X',len(raw)),('different_size',raw+b'X',len(raw)),('wrong_magic',b'WRONG'+raw[5:],len(raw))]:
 storage.get=lambda key,payload=payload:payload
 expected='WORD_RENDERED_PDF_CORRUPTED' if name!='exact' else None
 try:
  checked=value | ({'digest':sha(payload)} if name=='wrong_magic' else {})
  word_materials.verify_candidate(checked);got=None
 except DomainError as e:got=e.code
 assert got==expected,(name,got)
 checks.append({'name':name,'error':got})
os.environ.pop('WORD_INTAKE_ENABLED',None)
try:word_materials.require_enabled();raise AssertionError('Default capability open')
except DomainError as e:assert e.code=='WORD_INTAKE_DISABLED' and e.status==503
checks.append({'name':'default_admission_disabled','http':503})
for v in sm['sources']:assert hashlib.sha256((root/v['path']).read_bytes()).hexdigest()==v['sha256']
result={'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'runtime_manifest_sha256':m['runtime_manifest']['sha256'],'sources_verified_before_after':len(sm['sources']),'own_sources':len(m['own_sources']),'static_read_scope':'21 own sources diff against F05 plus called frozen Word repository/handler and lifecycle guards','own_offline_checks':checks,'QA_evidence':{'path':str(qa),'sha256':hashlib.sha256(qa.read_bytes()).hexdigest(),'read_and_hash_bound':True,'own_executions':False,'unique_cases':qm['unique_cases']},'no_DDL_DB_cloud_Docker_model_Kafka':True}
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
