"""Existing approved Word03 generic port; no new generated PAGE port is executed."""
import hashlib
import json
from pathlib import Path
import sys

LIVE=Path('/workspace/tender-workbench')
sys.path.insert(0,str(LIVE))
HERE=Path(__file__).resolve().parent
APPROVED=Path('/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03/candidate.json')
CANDIDATE=json.loads(APPROVED.read_text())
records={r['path']:r for r in CANDIDATE['sources']}
used=['packages/word_intake/renderer.py','packages/word_intake/inspection.py','packages/word_intake/sandbox_runner.py']
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for name in used:
    if digest(LIVE/name)!=records[name]['sha256']:raise RuntimeError('Word03 byte mismatch '+name)
from packages.word_intake.renderer import configured_profile,render
profile=configured_profile()
source=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/candidate-01/evidence/synthetic-tree-plan-templates.docx').read_bytes()
# This source contains static header/footer and no fields; complex-layout partial remains honest.
result=render(source,profile)
if result['pdf'] is not None:(HERE/'static-word03-output.pdf').write_bytes(result['pdf'])
for name in used:
    if digest(LIVE/name)!=records[name]['sha256']:raise RuntimeError('Word03 changed during execution '+name)
report={key:value for key,value in result.items() if key!='pdf'}
report.update(scope='Existing Word03 port on public synthetic static A2 DOCX only; new generated PAGE port not executed; no DB/model/Kafka/OSS',word03_candidate_sha256=digest(APPROVED),source_digest=hashlib.sha256(source).hexdigest(),word03_source_before_after={name:records[name]['sha256'] for name in used})
(HERE/'static-word03-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'page_count':result['chain'].get('page_count'),'cleanup':(result['isolation'] or {}).get('own_job_cleanup_confirmed')},ensure_ascii=False))
