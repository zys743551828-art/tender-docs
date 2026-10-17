"""检查保留功能目标契约及反例；不调用业务API或模型。"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESIGN = ROOT / '05-技术与设计/detailed-design'


def run():
    schema = json.loads((DESIGN / 'protocol.schema.json').read_text())
    api = json.loads((DESIGN / 'openapi.json').read_text())
    checks = []

    def check(name, success):
        if not success:
            raise AssertionError(name)
        checks.append(name)

    def valid(name, body):
        target = {'$ref': '#/$defs/' + name, '$defs': schema['$defs']}
        return not list(Draft202012Validator(target, format_checker=FormatChecker()).iter_errors(body))

    u = '00000000-0000-0000-0000-000000000001'
    h = 'a' * 64
    t = '2026-10-16T12:00:00Z'
    upload = dict(batch_id=u, role='body', filename='正文.pdf', byte_size=1,
                  declared_sha256=h, target={'kind': 'new_file'})
    part = dict(part_number=1, byte_offset=0, byte_size=1, url='https://storage.example.test/part', expires_at=t)
    grant = dict(upload_id=u, object_ref=u, file_id=u, mode='multipart', upload_url=None,
                 part_size=8388608, part_count=1, parts=[part], expires_at=t, session_expires_at=t, revision=1)
    fact = dict(subject='本项目', scope='全标段', name='工期', value='180', unit='日历天', evidence_ids=[u], reason='核实原文')
    rule = dict(id=u, rule_set_id=u, kind='mandatory', statement='工期要求', conditions=[], exceptions=[],
                maximum=None, evidence_ids=[u], confirmation='pending', decision_revision=1, carryover=None, scope=dict(bid_id=u,description='本标段本轮'),bands=[],origin_kind='model_extracted',manual_origin_rule_id=None,provenance_status='effective')
    matrix = dict(rule=rule, verdict=None, finding_ids=[], evidence_ids=[], check_status='failed', error_code='MODEL_UNAVAILABLE')
    pricing = dict(formula_version_id=u, candidate_price='100', competitor_price_assumptions=['90'],
                   rounding_policy='half_up_2', candidate_prices=['95'], curve_range={'lower':'90','upper':'110','points':21})
    preview = dict(dataset_manifest_blob_id=u, split_manifest_blob_id=u, protocol_digest=h, method_digest=h,
                   profile_digest=h, sample_ids=[u], repeat_count=2, concurrency=2, fee_limit='10.25', currency='CNY')
    run_view = dict(id=u, experiment_id=u, preview_id=u, revision=1, manifest_digest=h, state='running',
                    total=1, succeeded=0, failed=0, pending=1, fee_limit='10', spent_fee='1', reserved_fee='1', currency='CNY', cancel_requested=True)
    positives = [
        ('UploadRequest', upload), ('UploadGrant', grant),
        ('UploadComplete', {'parts':[{'part_number':1,'etag':'opaque-etag','byte_size':1}],'declared_sha256':h}),
        ('RefreshUploadRequest', {'part_numbers':[1,2]}),
        ('BatchRuleConfirmationRequest', {'items':[{'rule_id':u,'expected_decision_revision':1}]}),
        ('RuleCarryoverView', {'from_rule_id':u,'from_decision_id':u,'equivalence_digest':h,'adopted_at':t}),
        ('FactChangeRequest', {'fact':fact,'replaces_fact_id':None}),
        ('FactSetView', {'id':None,'bid_id':u,'facts':[],'revision':0}),
        ('MatrixRow', matrix), ('PricingRequest', pricing),
        ('QualificationMetrics', dict(identified=2,resolved=1,dismissed=1,open_at_submission=0,official_rejections=0,scope_note='冻结口径')),
        ('ShadowComparisonRequest', dict(candidate_model_id=u,expected_input_digest=h,business_prediction_id=u,comparison_protocol_digest=h)),
        ('ResearchPreviewRequest', preview), ('ResearchRunView', run_view),
        ('ResumeResearchRequest', dict(retry_failed=True,reason='恢复暂时错误')),
        ('ValidationDiagnostic', dict(id=u,operation_id=u,step_key='extract',code='unsupported_quantity',field_path='facts[0].value',evidence_ids=[],action='needs_review',message='缺少数值依据')),
    ]
    single = copy.deepcopy(grant)
    single.update(mode='single', upload_url='https://storage.example.test/object', parts=[])
    positives.append(('UploadGrant', single))
    new_version = copy.deepcopy(upload)
    new_version['target'] = dict(kind='new_version',file_id=u,expected_file_revision=2)
    positives.append(('UploadRequest', new_version))
    judged = copy.deepcopy(matrix)
    judged.update(check_status='succeeded',verdict='unable_to_judge',error_code=None)
    positives.append(('MatrixRow', judged))
    for i,(name,body) in enumerate(positives,1):
        check(f'合法契约 {i} {name}', valid(name, body))
    check('历史版本号与当前文件CAS修订分别可读', {'file_revision','file_head_revision','file_head_version_id'} <= set(schema['$defs']['DocumentView']['required']))

    negatives = []
    def bad(name, body, key, value, label):
        payload = copy.deepcopy(body)
        payload[key] = value
        negatives.append((label,name,payload))
    bad('UploadRequest',upload,'target',{'kind':'new_version','file_id':u},'新版本缺少基准revision')
    bad('UploadRequest',upload,'target',{'kind':'new_file','file_id':u},'新文件不能夹带替换目标')
    bad('UploadRequest',upload,'byte_size',0,'空文件')
    bad('UploadGrant',grant,'upload_url','https://storage.example.test/x','分片模式不能使用单次地址')
    bad('UploadGrant',single,'upload_url',None,'单次模式必须有地址')
    bad('RefreshUploadRequest',{'part_numbers':[1]},'part_numbers',[1,1],'重授权片号重复')
    bad('RefreshUploadRequest',{'part_numbers':[1]},'part_numbers',[0],'片号从1开始')
    bad('BatchRuleConfirmationRequest',{'items':[]},'items',[],'批量规则不接受空选择')
    bad('FactInput',fact,'evidence_ids',[],'人工事实必须有依据')
    bad('FactInput',fact,'reason','','更正保留理由')
    bad('MatrixRow',matrix,'verdict','satisfied','失败行不能判满足')
    bad('MatrixRow',matrix,'error_code',None,'失败行必须有原因')
    bad('MatrixRow',judged,'verdict',None,'成功判定不能缺verdict')
    bad('MatrixRow',judged,'error_code','ERROR','业务无法判断不是执行错误')
    bad('PricingRequest',pricing,'curve_range',{'lower':'0','upper':'1','points':1000},'曲线限制点数')
    bad('PricingRequest',pricing,'candidate_price',100,'金额不用JSON浮点数')
    bad('ResearchPreviewRequest',preview,'sample_ids',[u,u],'实验不重复计样本')
    bad('ResearchPreviewRequest',preview,'concurrency',17,'研究并发有上限')
    bad('ResearchPreviewRequest',preview,'fee_limit','NaN','预算拒绝NaN')
    bad('ResearchPreviewRequest',preview,'repeat_count',0,'整轮重复次数有效')
    bad('ResearchPreviewRequest',preview,'chat_history','不允许','研究预览拒绝任意聊天输入')
    bad('ResearchRunView',run_view,'state','cancel_requested_is_done','取消请求不是取消终态')
    for label,name,body in negatives:
        check('非法契约拒绝 '+label, not valid(name, body))

    endpoints = {
        'D01': [('get','/uploads/{upload_id}/parts'),('post','/uploads/{upload_id}/authorizations'),('post','/uploads/{upload_id}/abort')],
        'D02': [('post','/bids/{bid_id}/uploads')],
        'D03': [('post','/rule-sets/{rule_set_id}/confirmations')],
        'D04': [('post','/bids/{bid_id}/facts'),('post','/facts/{fact_id}/withdrawals')],
        'D05': [('get','/reviews/{review_id}/matrix')],
        'D06': [('get','/operations/{operation_id}/diagnostics')],
        'D07': [('get','/bids/{bid_id}/pricing-plans'),('get','/bids/{bid_id}/pricing-plans/{plan_id}')],
        'D08': [('get','/metrics'),('get','/bids/{bid_id}/round-trends')],
        'D09': [('post','/reviews/{review_id}/shadow-comparisons'),('get','/shadow-comparisons/{comparison_id}')],
        'D10': [('post','/research/previews'),('post','/research/runs'),('get','/research/runs/{run_id}/items'),('post','/research/runs/{run_id}/resume'),('post','/research/runs/{run_id}/cancel')],
    }
    conditional = {'refreshUploadAuthorization','abortUpload','confirmPendingRules','changeFacts','withdrawFact','startShadowComparison','resumeResearch','cancelResearch'}
    for item, operations in endpoints.items():
        for method,path in operations:
            check(item+' 端点 '+method+' '+path, method in api['paths'].get(path,{}))
            operation = api['paths'][path][method]
            if operation['operationId'] in conditional:
                check('条件头 '+operation['operationId'], {'$ref':'#/components/parameters/IfMatch'} in operation['parameters'])
    guide = (DESIGN/'保留功能实现规约.md').read_text()
    for i in range(1,11):
        check(f'D{i:02} 完整规约落点',f'（D{i:02}）' in guide)
    inputs = [DESIGN/n for n in ['protocol.schema.json','openapi.json','domain.sql','roles.sql','保留功能实现规约.md']]+[Path(__file__)]
    return dict(passed=True,scope='新增目标协议和设计落点静态检查；不执行软件业务',executed_at=datetime.now(timezone.utc).isoformat(),
                checks=len(checks),positive_examples=len(positives),rejected_counterexamples=len(negatives),followups=10,
                software_end_to_end_executed=False,inputs={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='inputs'},ensure_ascii=False))
