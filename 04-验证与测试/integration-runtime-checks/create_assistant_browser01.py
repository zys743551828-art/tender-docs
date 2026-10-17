"""New isolated test DB, no broker or consumers, using the reviewed helper02."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

root=Path('/workspace/tender-workbench')
out=Path('/workspace/tender-docs/04-验证与测试/assistant-browser-environment01')
helper=Path('/workspace/tender-docs/04-验证与测试/fresh-bootstrap-development-checks/candidate03/baseline/bootstrap_fresh.py')
assert hashlib.sha256(helper.read_bytes()).hexdigest() == '44cb845ffc67aee6f147053fdff8b08317d64a1565100ff5c04a4a7b2fc53631'
spec=importlib.util.spec_from_file_location('reviewed_bootstrap02',helper)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
reference=json.loads(Path('/workspace/tender-docs/04-验证与测试/integration-runtime-checks/workbench08-f04/fresh-initialization.json').read_text())
extensions=[(root/row['path'],row['sha256']) for row in reference['ordered_sql'][7:]]
result=module.bootstrap(base_profile=root/'.local/workbench08-init.env',name='assistant_ui01',extensions=extensions)
values=module.file_configuration(root/result['profiles']['business'])
values.pop('DEEPSEEK_API_KEY',None)
values.pop('OBJECT_STORE_ACCESS_KEY',None)
values.pop('OBJECT_STORE_SECRET_KEY',None)
values['APP_ORIGIN']='http://127.0.0.1:5184'
assert values['KAFKA_BOOTSTRAP_SERVERS'] == ''
module.write_private(root/'.local/assistant_ui01-runtime.env',values)
result.update(provider_key_absent_from_runtime=True,kafka_disabled=True,consumers_started=False,
              source_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest())
(out/'fresh-initialization.json').write_text(json.dumps(result,indent=2)+'\n')
print({'database':result['database'],'sql':len(result['ordered_sql']),'restricted_logins':len(result['logins']),'broker_disabled':True})
