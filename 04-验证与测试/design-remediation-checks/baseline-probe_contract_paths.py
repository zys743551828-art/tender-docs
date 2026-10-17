"""Synthetic identity and conflict-recovery counterexamples against frozen contracts."""
from pathlib import Path
import copy
import hashlib
import json
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path('/workspace/tender-docs')
DESIGN = Path('/workspace/tender-docs/04-验证与测试/design-remediation-checks/baseline-inputs')
HERE = Path('/workspace/tender-docs/04-验证与测试/design-remediation-checks') / 'baseline-results'
schema = json.loads((DESIGN / 'protocol.schema.json').read_text())
api = json.loads((DESIGN / 'openapi.json').read_text())
u = '00000000-0000-0000-0000-000000000001'
h = 'a' * 64
manifest = dict(bid_id=u, body_version_id=u, material_snapshot_id=u, material_revision=1,
                rule_set_id=u, fact_set_id=None, parse_run_ids=[u], method_digest=h,
                policy_digest=h, purpose='compliance_and_scoring',
                source_refs=[dict(id=u, digest=h, kind='body', known_at='2026-10-01T00:00:00Z',
                                  lineage_manifest_digest=h)],
                content_checks=dict(consistency=True, scope_residue=True),
                frozen_at='2026-10-16T01:00:00Z')
second = copy.deepcopy(manifest)
second['frozen_at'] = '2026-10-16T01:00:01Z'


def validator(name):
    return Draft202012Validator(dict(schema, **{'$ref': '#/$defs/' + name}),
                               format_checker=FormatChecker())


def digest(value):
    # All keys ASCII, values strings/integer/bool/null; this subset has the same
    # bytes as RFC8785 canonicalization (no floating number or Unicode sort edge).
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


for data in (manifest, second):
    validator('ReviewManifest').validate(data)
assert digest(manifest) != digest(second)

commit = dict(accepted_upload_ids=[u], explicit_partial_acceptance=False)
validator('BatchCommit').validate(commit)
rebase = copy.deepcopy(commit)
rebase['upload_targets'] = [dict(upload_id=u, file_id=u, expected_file_revision=3)]
errors = list(validator('BatchCommit').iter_errors(rebase))
assert errors and any(e.validator == 'additionalProperties' for e in errors)
upload_paths = {p: sorted(m for m in methods if m in ('get', 'post', 'put', 'patch', 'delete'))
                for p, methods in api['paths'].items() if 'upload' in p}
assert all('patch' not in methods and 'put' not in methods for methods in upload_paths.values())

result = dict(scope='独立设计反例；未调用真实API',
              manifest_time=dict(both_schema_valid=True, only_changed_field='frozen_at',
                                 first_sha256=digest(manifest), second_sha256=digest(second),
                                 semantic_digest_changes=True),
              conflict_recovery=dict(valid_commit=commit, attempted_rebase=rebase,
                                     rejection_messages=[e.message for e in errors],
                                     upload_paths=upload_paths,
                                     note='现有complete/auth/abort均无逻辑文件目标或expected_file_revision修改字段；CreateUpload也不接收verified_upload_id。'),
              inputs={n: hashlib.sha256((DESIGN/n).read_bytes()).hexdigest()
                      for n in ['protocol.schema.json','openapi.json','保留功能实现规约.md','AI投标工作台详细技术设计.md']})
(HERE/'contract-counterexamples.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(dict(valid_manifest_pair=True, time_changes_digest=True, upload_rebase_rejected=True), ensure_ascii=False))
