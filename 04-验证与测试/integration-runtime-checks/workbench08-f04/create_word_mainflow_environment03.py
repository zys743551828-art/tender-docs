"""Initialize a separate Word bridge DB using frozen reviewed helper02 and reviewed SQL only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

root = Path('/workspace/tender-workbench')
out = Path('/workspace/tender-docs/04-验证与测试/word-mainflow-environment03')
out.mkdir(exist_ok=False)
helper = Path('/workspace/tender-docs/04-验证与测试/fresh-bootstrap-development-checks/candidate03/baseline/bootstrap_fresh.py')
assert hashlib.sha256(helper.read_bytes()).hexdigest() == '44cb845ffc67aee6f147053fdff8b08317d64a1565100ff5c04a4a7b2fc53631'
spec = importlib.util.spec_from_file_location('reviewed_bootstrap02', helper)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
receipt = json.loads((Path(__file__).resolve().parent/'fresh-initialization.json').read_text())
extensions = [(root/item['path'], item['sha256']) for item in receipt['ordered_sql'][7:]]
extensions.append((root/'packages/word_intake/request_order.sql','8a379cb1b7d4c8bb807892937e03f77d3f75304ebd3abe256a1cf44e06319277'))
result = module.bootstrap(base_profile=root/'.local/workbench08-init.env', name='word_mainflow03', extensions=extensions, model_purposes=False)
values = module.file_configuration(root/result['profiles']['business'])
values.pop('DEEPSEEK_API_KEY', None)
assert values['KAFKA_BOOTSTRAP_SERVERS'] == ''
assert values['OBJECT_STORE_BACKEND'] == 'oss'
assert values['OBJECT_STORE_PREFIX'] != module.file_configuration(root/'.local/workbench08-checks.env')['OBJECT_STORE_PREFIX']
module.write_private(root/'.local/word_mainflow03-runtime.env', values)
result.update(runtime_profile='.local/word_mainflow03-runtime.env', provider_key_removed_from_runtime=True,
              kafka_disabled=True, source_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
              scope='Fresh Word mainflow with architecture-reviewed request-order04 DEFAULT; no Kafka, no complete intake qualification')
(out/'fresh-initialization.json').write_text(json.dumps(result,indent=2)+'\n')
print({'database':result['database'],'sql_count':len(result['ordered_sql']),'restricted_logins':len(result['logins']),'kafka_started':False,'provider_key_in_runtime':False})
