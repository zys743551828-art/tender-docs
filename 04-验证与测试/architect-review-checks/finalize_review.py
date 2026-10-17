"""Bind an independent review to unchanged source/evidence inputs and check links."""
from pathlib import Path
import hashlib
import json
import re
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORT = HERE.parent / '架构师独立审查.md'
original = json.loads(Path('/tmp/architect-review-input-record.json').read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


observed = {}
for name, expected in original['source_hashes'].items():
    actual = sha(ROOT/name)
    assert actual == expected, name
    observed[name] = actual

results = {str(Path('04-验证与测试/development-readiness-checks')/name): value
           for name, value in original.get('result_hashes', {}).items()}
for name, expected in results.items():
    assert sha(ROOT/name) == expected, name

broken = []
for destination in re.findall(r'\]\(([^)]+)\)', REPORT.read_text()):
    if '://' in destination:
        continue
    target = destination.split('#',1)[0]
    if target == 'architect-review-checks/review-record.json':
        continue
    if not (REPORT.parent/target).is_file():
        broken.append(destination)
assert not broken, broken

artifacts = ['probe_architect.py', 'probe_contract_paths.py', 'role-counterexamples.json',
             'contract-counterexamples.json', 'finalize_review.py']
record = dict(review_scope='独立架构审查，主设计和历史证据未改动',
              recorded_at=datetime.now(timezone.utc).isoformat(),
              finding_counts=dict(P1=2, P2=1), conclusion='带条件可开工',
              source_hashes=observed, inherited_result_hashes=results,
              report_sha256=sha(REPORT),
              artifacts={name:sha(HERE/name) for name in artifacts},
              checks=dict(source_inputs_unchanged=True, local_links_valid=True,
                          reproduced_role_counterexamples=5,
                          synthetic_identity_counterexample=True,
                          conflict_recovery_contract_gap=True,
                          product_software_end_to_end_executed=False))
(HERE/'review-record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(dict(sources_unchanged=len(observed), results_unchanged=len(results),
                      report_links_valid=True, findings=3), ensure_ascii=False))
