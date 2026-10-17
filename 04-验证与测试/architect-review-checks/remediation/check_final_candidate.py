"""Bind independent architectural closure to the exact final candidate and test inputs."""
from pathlib import Path
import hashlib
import json

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DESIGN=ROOT/'05-技术与设计/detailed-design'
QA=ROOT/'04-验证与测试/design-remediation-checks'
candidate=ROOT/'04-验证与测试/design-remediation/technical-candidate-02.json'
expected='d9e53659504a3f5dcb527266582495294de738fc92c036c97987e669d12175e4'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(candidate)==expected
frozen=json.loads(candidate.read_text())
for row in frozen['sources']:assert sha(ROOT/row['path'])==row['sha256'],row['path']
products={
 '02-业务与产品/AI投标工作台PRD.md':'a8f2349ce87418fc7249af0ec3ca5ac7ef3a3444d8be650ec27e2d597c4a1f9e',
 '05-技术与设计/交互与视觉设计.md':'2ee6809cc12fbb4450ff1b8b038d71c4e5fb7f8e4f361def215743de83150dc9',
}
for name,digest in products.items():assert sha(ROOT/name)==digest,name
checks={}
retrieval=(DESIGN/'retrieval.sql').read_text()
assert 'eligible_chunks(ARRAY[s.source_id],i.as_of,' in retrieval
assert 'eligible_chunks(ARRAY[s.source_id],i.frozen_at,' not in retrieval
checks['formal_score_uses_business_as_of']=True
worker=(DESIGN/'worker-functions.sql').read_text()
for name in ['request_digest','profile_digest','research_run_id','reserved_amount']:
    assert 'existing.'+name+' IS DISTINCT FROM ' in worker,name
checks['nullable_call_identity_is_null_safe']=True
schema=json.loads((DESIGN/'protocol.schema.json').read_text())['$defs']
api=json.loads((DESIGN/'openapi.json').read_text())
assert 'post' in api['paths']['/uploads/{upload_id}/bindings']
assert 'accepted_binding_ids' in schema['BatchCommit']['required']
assert 'post' in api['paths']['/bids/{bid_id}/rule-set-corrections']
assert all(n in schema['RuleAddition']['required'] for n in ['scope','conditions','exceptions','evidence_quotes'])
assert all(n in schema['RuleView']['required'] for n in ['origin_kind','manual_origin_rule_id','provenance_status'])
checks['binding_and_manual_rule_contracts_exist']=True
test_files=[
 'final-worker-login-result.json','final-identity-contract-result.json',
 'final-identity-upload-result.json','score-asof-result.json',
 'final-contract-regression-result.json','final-rule-origin-database-result.json',
 'final-product-spec-result.json',
]
test_records={}
for name in test_files:
    data=json.loads((QA/name).read_text())
    assert data['passed'] is True,name
    verified=0
    for field in ['input_sha256','inputs','sql_sha256']:
        for relative,digest in data.get(field,{}).items():
            p=ROOT/relative if '/' in relative else DESIGN/relative
            assert sha(p)==digest,(name,relative)
            verified+=1
    test_records[name]={'sha256':sha(QA/name),'checked_input_hashes':verified,
                        'scope':data['scope']}
own_result=json.loads((HERE/'candidate02/score-asof-result.json').read_text())
assert own_result['matches_business_as_of'] is True
for name,digest in own_result['inputs'].items():assert sha(DESIGN/name)==digest,name
original=json.loads((HERE/'rejected-candidate.json').read_text())
for name,digest in original['artifacts'].items():assert sha(HERE/name)==digest,name
checks['independent_as_of_regression_passed']=True
checks['earlier_counterexample_artifacts_unchanged']=True
result={'conclusion':'通过：架构设计无剩余阻塞；应用实现尚未验收',
        'candidate_sha256':expected,'source_hashes':frozen['sources'],'product_hashes':products,
        'architectural_checks':checks,'reviewed_test_evidence':test_records,
        'independent_pg_result':'candidate02/score-asof-result.json',
        'independent_pg_result_sha256':sha(HERE/'candidate02/score-asof-result.json'),
        'prior_rejection_record_sha256':sha(HERE/'rejected-candidate.json'),
        'software_end_to_end_executed':False}
(HERE/'final-candidate-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'candidate':expected,'frozen_sources':len(frozen['sources']),
                  'product_inputs':len(products),'test_evidence_records':len(test_records),
                  'design_blockers':0},ensure_ascii=False))
