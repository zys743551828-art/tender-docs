"""Read-only live HTTP, process and Kafka membership binding; no provider requests."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import httpx
from confluent_kafka.admin import AdminClient

out = Path(__file__).resolve().parent
backend = Path('/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-05/pricing-candidate-05.json')
frontend = Path('/workspace/tender-workbench/apps/web/qa-previews/pricing-method-08/frontend-candidate.json')
manifest = json.loads(backend.read_text())
runtime = Path(manifest['runtime_root'])
assert all(hashlib.sha256((runtime / row['path']).read_bytes()).hexdigest() == row['sha256'] for row in manifest['sources'])
receipts = [json.loads(path.read_text()) for path in sorted(out.glob('worker-*.json')) + sorted(out.glob('coordinator-*.json'))]
assert len(receipts) == 7
for receipt in receipts:
    os.kill(receipt['pid'], 0)
    assert receipt['provider_key_present'] is False
    assert receipt['candidate_sha256'] == hashlib.sha256(backend.read_bytes()).hexdigest()
processes = subprocess.check_output(['ps', '-o', 'pid,uid,stat,args', '-p', ','.join(str(r['pid']) for r in receipts)], text=True)
assert processes.count('nonpaid_') == 7
with httpx.Client(trust_env=False, timeout=20) as client:
    ready = client.get('http://127.0.0.1:8808/health/ready')
    assert ready.status_code == 200
    actual = client.get('http://127.0.0.1:8808/openapi.json').json()
    assert actual == json.loads((backend.parent/'actual-f05.openapi.json').read_text())
    web = client.get('http://127.0.0.1:5178/')
    assert web.status_code == 200
    asset = '/assets/index-BI74Pw4T.js'
    assert asset in web.text
    served = client.get('http://127.0.0.1:5178' + asset)
    assert served.status_code == 200
    assert hashlib.sha256(served.content).hexdigest() == '4e1b49974ffaf3219474608324a590f6f5a914ff1d4ec6964f174c519d9d8f9c'
admin = AdminClient({'bootstrap.servers':'127.0.0.1:19097'})
groups = admin.list_consumer_groups(request_timeout=10).result()
assert not groups.errors
group_names = {r.group_id for r in groups.valid}
assert group_names == {'tender-document-worker-v1','tender-export-worker-v1','tender-comparison-worker-v1'}
assert all(str(r.state).endswith('STABLE') for r in groups.valid)
result = {'backend_candidate_sha256': hashlib.sha256(backend.read_bytes()).hexdigest(),
          'frontend_candidate_sha256': hashlib.sha256(frontend.read_bytes()).hexdigest(),
          'api_port':8808, 'frontend_port':5178, 'database':'tender_checks_workbench08',
          'broker':'127.0.0.1:19097', 'http_ready':True, 'actual_contract_equal':True,
          'frontend_asset_matches':True, 'source_files_verified':len(manifest['sources']),
          'ordinary_worker_and_index_coordinator_pids':[r['pid'] for r in receipts],
          'startup_provider_keys_absent':True, 'processes':processes,
          'consumer_groups':sorted(group_names), 'paid_consumers_present':False,
          'paid_calls_by_this_probe':0, 'old_environments_changed':False,
          'scope':'Fixed frontend08 + compatible F05 API; existing frozen frontend manifest remains unchanged'}
with (out/'nonpaid-runtime-binding-f05.json').open('x') as stream:
    json.dump(result,stream,indent=2)
print({'http_ready':True,'processes':7,'stable_ordinary_groups':3,'paid_consumers':0})
