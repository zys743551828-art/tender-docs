import hashlib,json,shutil
from pathlib import Path
LIVE=Path('/workspace/tender-workbench');HERE=Path(__file__).resolve().parent;DEST=HERE/'frozen-tree'
own=['packages/authoring/generated_docx.py','packages/authoring/generated_verification.py','packages/authoring/generated_renderer.py','packages/authoring/structured_docx.py','tests/test_authoring_generated.py']
deps=['packages/authoring/__init__.py','packages/authoring/build.py','packages/authoring/content.py','packages/authoring/snapshot_structure.py','packages/authoring/templates.py','packages/authoring/structure_model.py','packages/authoring/structure_schema.py','packages/domain/runtime.py','packages/word_intake/__init__.py','packages/word_intake/renderer.py','packages/word_intake/inspection.py','packages/word_intake/sandbox_runner.py','pyproject.toml','requirements.lock.txt']
records=[]
for path in own+deps:
 source=LIVE/path;target=DEST/path;target.parent.mkdir(parents=True,exist_ok=True);raw=source.read_bytes();target.write_bytes(raw)
 records.append({'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'archive':str(target),'kind':'own' if path in own else 'dependency'})
(HERE/'external-inputs').mkdir()
for name in ['candidate.json','renderer-profile.json']:
 source=Path('/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03')/name
 shutil.copyfile(source,HERE/'external-inputs'/('word03-'+name))
source=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/可信生成正文页码与转换协议草案01.md')
shutil.copyfile(source,HERE/'external-inputs/protocol01.md')
(HERE/'inputs.json').write_text(json.dumps({'root':str(DEST),'sources':records,'external_inputs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((HERE/'external-inputs').iterdir())]},ensure_ascii=False,indent=2)+'\n')
(HERE/'evidence').mkdir()
print(json.dumps({'own':len(own),'dependencies':len(deps),'sources':len(records)}))
