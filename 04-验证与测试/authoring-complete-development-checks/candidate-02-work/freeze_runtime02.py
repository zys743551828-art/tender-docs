import hashlib,json
from pathlib import Path
LIVE=Path('/workspace/tender-workbench');PARENT=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks');BASE=PARENT/'candidate-01';ROOT=PARENT/'candidate-02-work/runtime'
old=json.loads((BASE/'inputs.json').read_text());records={}
def copy(path,source,layer):
 raw=source.read_bytes();dest=ROOT/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
 records[path]={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'archive':str(dest),'layer':layer}
for item in old['records']:
 source=BASE/'frozen-tree'/item['path']
 if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError('candidate01 drift')
 copy(item['path'],source,'fixed-candidate01-base')
own=sorted(set(old['own_sources'])|{'packages/authoring/generated_docx.py','packages/authoring/generated_verification.py','packages/authoring/generated_renderer.py','packages/authoring/generated_profile.py','tests/test_authoring_generated.py','tests/test_authoring_generated_integration.py'})
for path in own:copy(path,LIVE/path,'authoring-complete02-own')
# Preserve readable public runner metadata needed by actual nonroot sandbox.
(ROOT/'packages/word_intake/sandbox_runner.py').chmod(0o444)
for name in ['renderer.py','inspection.py','sandbox_runner.py']:
 path='packages/word_intake/'+name
 approved=json.loads(Path('/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03/candidate.json').read_text())
 expected=next(i['sha256'] for i in approved['sources'] if i['path']==path)
 if records[path]['sha256']!=expected:raise RuntimeError('Word03 dependency mismatch '+path)
(PARENT/'candidate-02-work/runtime-inputs.json').write_text(json.dumps({'root':str(ROOT),'own_sources':own,'records':sorted(records.values(),key=lambda r:r['path']),'public_runner_mode':'0444','base':str(BASE/'candidate.json')},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'root':str(ROOT),'own':len(own),'total':len(records)},ensure_ascii=False))
