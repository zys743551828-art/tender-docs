"""Freeze the reviewed F05 baseline plus only the task draft/actual-contract delta."""
import hashlib
import json
from pathlib import Path
import shutil

root=Path('/workspace/tender-workbench')
out=Path(__file__).resolve().parent
base=Path('/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-05/pricing-candidate-05.json')
baseline=json.loads(base.read_text());fixed=Path(baseline['runtime_root'])
runtime=out/'runtime';runtime.mkdir(exist_ok=False)
rows={}
for row in baseline['sources']:
    source=fixed/row['path']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256']
    target=runtime/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
    rows[row['path']]={'path':row['path'],'sha256':row['sha256'],'classification':'unchanged-fixed-F05'}
delta=('packages/tasks/models.py','packages/tasks/contracts.py','apps/api/task_routes.py',
       'packages/tasks/draft_interpreter.py','packages/tasks/drafts.py','apps/api/task_draft_routes.py',
       'tests/test_task_draft_interpreter.py','tests/test_task_drafts_integration.py','tests/test_task_actual_contracts.py')
references=('tests/test_task_templates_integration.py','tests/test_task_runs_integration.py','tests/test_task_scheduling.py')
for name in (*delta,*references):
    source=root/name;target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
    rows[name]={'path':name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'classification':'owned-task-delta' if name in delta else 'unchanged-task-test-reference'}
manifest={'scope':'Member-private persistent editable bounded Chinese task drafts and actual task route schemas',
          'base_candidate_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),
          'runtime_root':str(runtime),'sources':list(sorted(rows.values(),key=lambda r:r['path'])),
          'delta_sources':list(delta),'test_references':list(references),
          'production_main_mounted':False,'production_exporter_hook_applied':False,
          'general_natural_language_understanding_accepted':False,'real_model_calls':0}
(out/'candidate.json').write_text(json.dumps(manifest,indent=2)+'\n')
print({'candidate_sha256':hashlib.sha256((out/'candidate.json').read_bytes()).hexdigest(),'files':len(rows),'delta':len(delta)})
