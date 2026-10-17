"""Reviewed fresh helper03 plus reviewed score F, initializing one new isolated test DB."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

root=Path('/workspace/tender-workbench')
out=Path('/workspace/tender-docs/04-验证与测试/score-execution-environment02')
out.mkdir(exist_ok=False)
candidate=Path('/workspace/tender-docs/04-验证与测试/fresh-bootstrap-development-checks/candidate03/candidate.json')
assert hashlib.sha256(candidate.read_bytes()).hexdigest()=='632f7419d561b00e1d060e87cd9ccca1a99f86185e6ba20653ac12ffb68b54ab'
manifest=json.loads(candidate.read_text());fixed=Path(manifest['runtime_root'])
for row in manifest['sources']:
    assert hashlib.sha256((fixed/row['path']).read_bytes()).hexdigest()==row['sha256']
    if row['path']=='deploy/initialize_database.py':
        assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
spec=importlib.util.spec_from_file_location('reviewed_bootstrap03',fixed/'deploy/bootstrap_fresh.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
reference=json.loads(Path('/workspace/tender-docs/04-验证与测试/integration-runtime-checks/workbench08-f04/fresh-initialization.json').read_text())
extensions=[(root/row['path'],row['sha256']) for row in reference['ordered_sql'][7:]]
extensions.append((root/'deploy/sql/score-execution.sql','28e9a2f5042e29078c24a20d2191ada9935034b745627e8b1a5c865b487ec5be'))
result=module.bootstrap(base_profile=root/'.local/workbench08-init.env',name='score_execution02',
    extensions=extensions,model_purposes=True,model_score_purpose=True)
values=module.file_configuration(root/result['profiles']['business']);values.pop('DEEPSEEK_API_KEY',None)
assert not values['KAFKA_BOOTSTRAP_SERVERS']
assert not any(key in values for key in ('MODEL_SCORE_DATABASE_DSN','MODEL_TRAIN_DATABASE_DSN','MODEL_EVALUATE_DATABASE_DSN','OWNER_DATABASE_DSN'))
module.write_private(root/'.local/score_execution02-runtime.env',values)
score=module.file_configuration(root/result['profiles']['model_score'])
assert set(score)=={'MODEL_SCORE_DATABASE_DSN'}
result.update(helper_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    sql_f_sha256='28e9a2f5042e29078c24a20d2191ada9935034b745627e8b1a5c865b487ec5be',
    runtime_profile='.local/score_execution02-runtime.env',provider_key_in_runtime=False,
    model_purpose_dsns_in_business_runtime=False,kafka_disabled=True,consumers_started=False,
    scope='Initialization only; score RPC permissions, real PG/ASGI and MockTransport still require execution')
(out/'fresh-initialization.json').write_text(json.dumps(result,indent=2)+'\n')
print({'database':result['database'],'sql':len(result['ordered_sql']),'restricted_logins':len(result['logins']),
       'score_private_single_dsn':True,'broker_disabled':True,'provider_key_in_business_runtime':False})
