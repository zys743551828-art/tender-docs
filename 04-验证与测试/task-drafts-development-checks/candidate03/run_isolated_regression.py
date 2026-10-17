"""Frozen-source regression on a separate no-broker DB, with all external HTTP blocked."""
import hashlib
import json
import os
from pathlib import Path

out=Path(__file__).resolve().parent
manifest=json.loads((out/'candidate.json').read_text());root=Path(manifest['runtime_root'])
assert all(hashlib.sha256((root/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in manifest['sources'])
assert not os.environ.get('KAFKA_BOOTSTRAP_SERVERS')
os.environ.pop('DEEPSEEK_API_KEY',None)
# Presence-only configuration for the existing plan-freeze test. No real key, no model transport.
os.environ['DEEPSEEK_API_KEY']='explicit-plan-freeze-test-marker-no-provider'
os.environ['RUN_TASK_DRAFTS']='1';os.environ['RUN_TASK_TEMPLATES']='1';os.environ['RUN_TASK_RUNS']='1'
import httpx
import requests
attempts=[]
def refuse(*args,**kwargs):
    attempts.append('external_http_refused')
    raise AssertionError('This isolated regression permits only in-process ASGI/explicit MockTransport, no external HTTP')
httpx.HTTPTransport.handle_request=refuse
httpx.AsyncHTTPTransport.handle_async_request=refuse
requests.Session.request=refuse
from packages.domain.runtime import assert_role,connection
assert_role()
with connection() as c:
    identity=c.execute('SELECT current_database() AS name,current_user AS role').fetchone()
    assert identity['name']=='tender_checks_task_drafts01'
from packages.tasks import drafts
assert Path(drafts.__file__).resolve().is_relative_to(root)
import pytest
code=pytest.main(['-c','/dev/null','--import-mode=importlib','-q',*[str(root/'tests'/name) for name in (
    'test_task_draft_interpreter.py','test_task_drafts_integration.py','test_task_actual_contracts.py',
    'test_task_templates_integration.py','test_task_runs_integration.py','test_task_scheduling.py')],
    '--junitxml='+str(out/'isolated-frozen-regression.xml')])
assert all(hashlib.sha256((root/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in manifest['sources'])
result={'exit_code':code,'database':identity['name'],'nonowner_role':identity['role'],
        'source_files_verified':len(manifest['sources']),'external_http_attempts':len(attempts),
        'provider_key':'explicit_synthetic_presence_marker_only','kafka_disabled':True,
        'consumers_started':False,'ddl_by_regression':0,'real_model_requests':0}
(out/'isolated-frozen-regression.json').write_text(json.dumps(result,indent=2)+'\n')
print(result)
raise SystemExit(code)
