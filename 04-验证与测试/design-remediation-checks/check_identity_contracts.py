"""独立固定向量与恢复契约验证；不调用真实业务API。"""
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
from jsonschema import Draft202012Validator, FormatChecker

HERE=Path(__file__).resolve().parent
DESIGN=HERE.parents[1]/'05-技术与设计/detailed-design'
FILES=['protocol.schema.json','openapi.json','identity_reference.py','review-identity-vectors.json']

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def sha(value):return hashlib.sha256(canonical(value)).hexdigest()

def normtime(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    assert dt.tzinfo is not None
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

# 测试方独立按规范投影，不调用被检查参考程序作为预期。
def oracle(manifest,nonce=None):
    whole=deepcopy(manifest)
    whole['parse_run_ids']=sorted(whole['parse_run_ids'])
    whole['source_refs']=sorted(whole['source_refs'],key=lambda v:v['id'])
    for source in whole['source_refs']:source['known_at']=normtime(source['known_at'])
    for f in ['as_of','frozen_at']:whole[f]=normtime(whole[f])
    content=deepcopy(whole)
    del content['frozen_at']
    input_digest=sha({'identity_version':'review-content/1','input':content})
    return dict(manifest_digest=sha(whole),input_digest=input_digest,execution_digest=sha({'identity_version':'review-execution/1','input_digest':input_digest,'rerun_nonce':nonce}))

def run(snapshot,output):
    snapshot.mkdir(parents=True,exist_ok=True)
    (snapshot/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    blobs={f:(DESIGN/f).read_bytes() for f in FILES}
    for f,b in blobs.items():(snapshot/f).write_bytes(b)
    schema=json.loads(blobs['protocol.schema.json']);api=json.loads(blobs['openapi.json'])
    spec=importlib.util.spec_from_file_location('frozen_identity',snapshot/'identity_reference.py')
    ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    vectors=json.loads(blobs['review-identity-vectors.json'])['vectors']
    checks=[]
    def valid(kind,value):
        Draft202012Validator(dict(schema,**{'$ref':'#/$defs/'+kind}),format_checker=FormatChecker()).validate(value)
    def reject(label,kind,value):
        errors=list(Draft202012Validator(dict(schema,**{'$ref':'#/$defs/'+kind}),format_checker=FormatChecker()).iter_errors(value))
        assert errors,label
        checks.append(label)
    actual={}
    for v in vectors:
        valid('ReviewManifest',v['manifest'])
        expected=oracle(v['manifest'],v['rerun_nonce'])
        assert expected==v['expected'],(v['name'],expected,v['expected'])
        assert ref.identities(v['manifest'],v['rerun_nonce'])==expected
        actual[v['name']]=expected;checks.append('独立固定向量:'+v['name'])
    a=actual['initial'];b=actual['later_request'];c=actual['explicit_rerun']
    assert a['manifest_digest']!=b['manifest_digest'] and a['input_digest']==b['input_digest'] and a['execution_digest']==b['execution_digest']
    checks.append('只改变冻结审计时间不改变内容与执行')
    assert a['input_digest']==c['input_digest'] and a['execution_digest']!=c['execution_digest'];checks.append('nonce仅创建独立执行')
    for k in ['effective_time_changed','source_effect_changed','method_changed','rules_changed']:
        assert actual[k]['input_digest']!=a['input_digest'];checks.append('实际业务输入变化:'+k)
    base=deepcopy(vectors[0]['manifest'])
    reorder=dict(reversed(list(base.items())))
    reorder['parse_run_ids']=list(reversed(base['parse_run_ids']))
    reorder['as_of']='2026-10-16T09:00:00+08:00'
    reorder['frozen_at']='2026-10-16T01:00:00.000000Z'
    reorder['source_refs'][0]['known_at']='2026-10-01T08:00:00+08:00'
    valid('ReviewManifest',reorder)
    assert oracle(reorder)==a and ref.identities(reorder)==a;checks.append('键序/解析集合顺序/等价UTC格式规范化')
    bad=deepcopy(base);bad['parse_run_ids']*=2;reject('重复parse身份拒绝','ReviewManifest',bad)
    bad=deepcopy(base);bad['frozen_at']='2026-10-16T01:00:00';reject('缺时区拒绝','ReviewManifest',bad)
    bad=deepcopy(base);del bad['as_of'];reject('不能省略业务评估时点','ReviewManifest',bad)
    bad=deepcopy(base);bad['unknown_business_field']='new';reject('未知业务字段不能静默漏出摘要','ReviewManifest',bad)
    bad=deepcopy(base);bad['source_refs']*=2
    try:ref.identities(bad)
    except ValueError:checks.append('重复来源身份参考算法拒绝')
    else:raise AssertionError('duplicate source accepted')
    bid='00000000-0000-4000-8000-000000000001'
    req=dict(target=dict(kind='new_version',file_id=bid,expected_file_revision=3),reason='当前head已更新，确认基于此稿继续')
    valid('UploadBindingRequest',req);checks.append('重绑定目标+最新修订+理由有机器契约')
    valid('UploadBindingRequest',dict(target=dict(kind='new_file'),reason='另建文件保留分支'));checks.append('另建文件可显式选择')
    for label,change in [('缺少目标',{'reason':'x'}),('缺少理由',{'target':req['target']}),('旧式顶层revision',dict(req,expected_file_revision=3))]:reject(label,'UploadBindingRequest',change)
    commit=dict(accepted_upload_ids=[bid],accepted_binding_ids=[bid],explicit_partial_acceptance=False)
    valid('BatchCommit',commit);checks.append('批次明确绑定用户选择')
    old=deepcopy(commit);del old['accepted_binding_ids'];reject('不带binding的旧式commit拒绝','BatchCommit',old)
    endpoint=api['paths']['/uploads/{upload_id}/bindings']['post']
    headers={p.get('$ref','') for p in endpoint['parameters']}
    assert all('#/components/parameters/'+p in headers for p in ['IdempotencyKey','CSRF','IfMatch'])
    assert all(str(n) in endpoint['responses'] for n in [200,409,410,412,428]);checks.append('重绑定具备幂等/CAS/CSRF及冲突/过期响应')
    layer=dict(state='complete',planned=10,completed=10,denominator_basis='已识别且生效核心规则',gaps=[])
    coverage={k:deepcopy(layer) for k in ['source_scope','core_extraction','judgment','scoring']}
    coverage.update(extraction_quality='not_measured',quality_report_id=None,human_confirmation_pending=0)
    valid('ReviewCoverage',coverage);checks.append('覆盖四层与未知检测质量可独立表达')
    missing=deepcopy(coverage);del missing['core_extraction'];reject('提取层不能被执行10/10替代','ReviewCoverage',missing)
    addition=dict(kind='mandatory',statement='工期不超过180天',conditions=[],exceptions=[],scope=dict(bid_id=bid,description='本标段本轮'),maximum=None,bands=[],evidence_quotes=[dict(evidence_id=bid,quote='工期不超过180天')])
    correction=dict(base_rule_set_id=bid,reason='人工发现核心条款漏提',additions=[addition])
    valid('RuleSetCorrectionRequest',correction);checks.append('人工补漏有据范围与条件例外均可显式提交')
    bad=deepcopy(correction);bad['additions'][0]['evidence_quotes']=[];reject('人工补漏不能缺依据','RuleSetCorrectionRequest',bad)
    bad=deepcopy(correction);del bad['additions'][0]['exceptions'];reject('人工补漏不能省略例外声明','RuleSetCorrectionRequest',bad)
    bad=deepcopy(correction);bad['additions'][0]['maximum']='100';reject('强制条款不能伪造评分满分','RuleSetCorrectionRequest',bad)
    bad=deepcopy(correction);bad['additions'][0]['origin_kind']='human_added';reject('客户端不能伪造人工来源身份','RuleSetCorrectionRequest',bad)
    assert {'origin_kind','manual_origin_rule_id','provenance_status'}<=set(schema['$defs']['RuleView']['required']);checks.append('规则视图可持续表达人工来源链与待核验状态')
    op=api['paths']['/bids/{bid_id}/rule-set-corrections']['post']
    refs={p.get('$ref','') for p in op['parameters']}
    assert all('#/components/parameters/'+p in refs for p in ['IdempotencyKey','CSRF','IfMatch'])
    assert '201' in op['responses'] and '412' in op['responses'] and '422' in op['responses'];checks.append('人工补漏具备幂等/CAS/整笔校验接口')
    result=dict(passed=True,scope='独立摘要固定向量及Schema/OpenAPI恢复契约；无真实业务API执行',checks=len(checks),check_names=checks,vector_results=actual,input_sha256={f:hashlib.sha256(b).hexdigest() for f,b in blobs.items()},probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),software_end_to_end_executed=False)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,checks=len(checks))))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.snapshot,a.output)
