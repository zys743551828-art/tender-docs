"""Independent local numeric ownership checks; no provider or database calls."""
from pathlib import Path
import hashlib
import json
import os

from packages.domain.content_checks import prepare, validate_output
from packages.domain import engineering


def main():
    root = Path(os.environ['QA_CONTENT_ROOT'])
    out = root / 'local-quote-execution'
    out.mkdir(exist_ok=False)
    cases = [
        ('other_project_in_same_quote', '工程A标段一工期95天；工程B标段一工期125天', False),
        ('other_lot_in_same_quote', '工程A标段一工期95天；工程A标段二工期125天', False),
        ('nested_other_project_and_lot', '工程A标段一工期95天（工程B标段二工期125天）', False),
        ('same_project_same_lot', '工程A标段一工期125天', True),
        ('explicit_local_connector', '工程A的标段一的施工工期125天', True),
    ]
    checks = []
    for name, quote, should_accept in cases:
        evidence = {'rules': [{'id': 'rule-local-duration', 'kind': 'mandatory', 'statement': '本工程工期必须一致', 'provenance_status': 'effective'}], 'confirmed_facts': []}
        doc = dict(role='body', document_version_id='own-body', coverage='complete', gaps=[], page_count=2,
                   pages=[dict(physical_page=p, quality='usable', gaps=[]) for p in (1, 2)],
                   blocks=[dict(id='left', text_content='工程A标段一工期95天'), dict(id='right', text_content=quote)])
        prepared = prepare('consistency', {'manifest': {'body_version_id': 'own-body'}}, evidence, [doc])
        def assertion(identity, text, value):
            return dict(evidence_id=identity, quote=text, subject='工程A', attribute='工期', scope='标段一', value=value, unit='天', relation='equal')
        candidate = dict(rule_id='rule-local-duration', severity='critical', statement='UNTRUSTED9999',
                         left=assertion('left', doc['blocks'][0]['text_content'], '95'), right=assertion('right', quote, '125'))
        actual = validate_output('consistency', dict(read_evidence_ids=['left', 'right'], issues=[candidate], gaps=[]), prepared, evidence)
        if should_accept:
            ok = (len(actual['issues']) == 1 and actual['check_state'] == 'complete' and not actual['diagnostics']
                  and actual['issues'][0]['right']['normalized'] == '125'
                  and '9999' not in actual['issues'][0]['statement'])
        else:
            ok = (not actual['issues'] and actual['check_state'] == 'partial'
                  and actual['core_affected_rule_ids'] == ['rule-local-duration']
                  and any(d['code'] == 'scope_mismatch' and d['evidence_ids'] == ['right'] for d in actual['diagnostics']))
        checks.append(dict(name=name, passed=ok, expected_acceptance=should_accept, input_quote=quote, actual=actual))
    result = dict(passed=all(c['passed'] for c in checks), checks=checks,
                  scope='Frozen production prepare/validate_output with independently constructed exact full-range dictionaries; no model/PG/parser/HTTP/Kafka or quality claim.',
                  paid_calls=0, provider_calls=0, database_connections=0, consumers_started=0,
                  engineering_source_path=str(Path(engineering.__file__).resolve()),
                  engineering_source_sha256=hashlib.sha256(Path(engineering.__file__).read_bytes()).hexdigest(),
                  executed_probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out / 'executed-probe.py').write_bytes(Path(__file__).read_bytes())
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(dict(passed=result['passed'], checks=len(checks), failed=[c['name'] for c in checks if not c['passed']]))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
