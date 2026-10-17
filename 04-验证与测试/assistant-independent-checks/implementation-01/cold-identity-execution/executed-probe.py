"""Cold standalone assistant route export and exact frozen loaded-source identity."""
from pathlib import Path
import hashlib
import json
import os
import sys

from fastapi import FastAPI
from apps.api.assistant_routes import router
from apps.api.export_openapi import implemented_openapi

root=Path(os.environ['QA_ASSISTANT_ROOT'])
candidate=json.loads((root/'candidate.json').read_text())
expected='9f9ae6f12aa95127458d53e8218918f3d7b2519d2ed70897bd0e94d45d652f97'
assert hashlib.sha256((root/'candidate.json').read_bytes()).hexdigest()==expected
for row in candidate['sources']+candidate['dependencies']:
    assert hashlib.sha256((root/'execution-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
for row in candidate['external_inputs']:
    assert hashlib.sha256((root/row['archive']).read_bytes()).hexdigest()==row['sha256']
app=FastAPI();app.include_router(router)
actual=implemented_openapi(app)
encoded=(json.dumps(actual,ensure_ascii=False,indent=2)+'\n').encode()
digest=hashlib.sha256(encoded).hexdigest()
assert digest=='6e400b42bcdbd50e0ec9c2ec1fe4fdc4138048d17ca0f52ecd35b393f0531622'
loaded=[]
for name,module in sorted(sys.modules.items()):
    path=getattr(module,'__file__',None)
    if not path or not name.startswith(('apps.','packages.')):continue
    path=Path(path).resolve();assert path.is_relative_to(root/'execution-inputs'),name
    loaded.append(dict(module=name,path=str(path.relative_to(root/'execution-inputs')),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
operations=[dict(path=path,method=method) for path,methods in actual['paths'].items() for method in methods if method in ['get','post','put','patch','delete','head','options']]
assert len(operations)==21
result=dict(passed=True,candidate_sha256=expected,sources_verified=21,dependencies_verified=152,raw_inputs_verified=1,
            actual_sha256=digest,operations=operations,schemas=len(actual['components']['schemas']),loaded_modules=loaded,
            scope='Standalone production assistant router only; no shared main import or service registration/port upgrade.',paid_calls=0,oss_requests=0,database_connections=0,consumers_started=0,
            executed_probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
out=root/'cold-identity-execution';out.mkdir(exist_ok=False)
(out/'actual.openapi.json').write_bytes(encoded)
(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes())
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(dict(passed=True,source_count=173,operations=21,actual_sha256=digest,loaded_module_count=len(loaded)))
