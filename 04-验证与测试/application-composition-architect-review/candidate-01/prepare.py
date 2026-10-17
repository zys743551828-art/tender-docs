from pathlib import Path
import json,hashlib,shutil
out=Path(__file__).parent
p=Path('/workspace/tender-workbench/docs/application-composition-development-checks/candidate-01.json');m=json.loads(p.read_text());assert hashlib.sha256(p.read_bytes()).hexdigest()=='32630d1d1e8572760cc9ca991ebd436caf7cf4f09f66e88c919777f1b2f20df3'
root=Path(m['fixed_runtime_root']);dest=out/'runtime'
for v in m['sources']:
 src=root/v['path'];assert hashlib.sha256(src.read_bytes()).hexdigest()==v['sha256'],str(src)
 q=dest/v['path'];q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,q)
for v in m['artifacts']+m['support_sources']+m['input_candidates']+[m['fresh_initialization']]:
 assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256'],v['path']
r=json.loads(Path('/workspace/tender-workbench/docs/application-composition-development-checks/recipe-01.json').read_text());selection=[]
for v in r['selected_inputs']:
 assert hashlib.sha256(Path(v['input']).read_bytes()).hexdigest()==v['sha256'],v['path']
 if 'shared-dependency' in v['layer']:selection.append(v)
(out/'input-record.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'sources':m['sources'],'recipe_sha256':m['artifacts'][0]['sha256'],'shared_seven_exact':selection,'test_root':str(dest)},ensure_ascii=False,indent=2)+'\n')
# Only own test bootstrap: deny external HTTP, object/provider operations and subprocesses.
(dest/'tests/conftest.py').write_text('''import pytest\n@pytest.fixture(autouse=True)\ndef no_external(monkeypatch):\n    import requests,httpx,subprocess\n    from packages.domain import storage\n    from packages.domain.providers.deepseek import DeepSeekClient\n    def forbidden(*args,**kwargs):\n        raise AssertionError("Composition independent check forbids external I/O")\n    async def forbidden_async(*args,**kwargs):\n        forbidden()\n    monkeypatch.setattr(requests.sessions.Session,"request",forbidden)\n    monkeypatch.setattr(httpx.HTTPTransport,"handle_request",forbidden)\n    monkeypatch.setattr(httpx.AsyncHTTPTransport,"handle_async_request",forbidden_async)\n    monkeypatch.setattr(DeepSeekClient,"from_env",forbidden)\n    monkeypatch.setattr(storage,"client",forbidden)\n    monkeypatch.setattr(storage,"get",forbidden)\n    monkeypatch.setattr(storage,"put",forbidden)\n    monkeypatch.setattr(subprocess,"run",forbidden)\n    monkeypatch.setattr(subprocess,"Popen",forbidden)\n''')
print('399 fixed sources copied and recipe inputs verified; own external-I/O guard only')
