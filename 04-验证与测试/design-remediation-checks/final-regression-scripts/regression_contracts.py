"""验证目标协议形状、OpenAPI及设计追踪；不连接产品或调用模型。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from importlib.metadata import version
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec

HERE=Path('/workspace/tender-docs/04-验证与测试/detailed-design-checks')
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'
DOC=ROOT/'05-技术与设计/AI投标工作台详细技术设计.md'


def run():
    schema=json.loads((DESIGN/'protocol.schema.json').read_text())
    api=json.loads((DESIGN/'openapi.json').read_text())
    policy=json.loads((DESIGN/'execution-policy.json').read_text())
    retrieval=json.loads((DESIGN/'retrieval-policy.json').read_text())
    checks=[]
    def check(label,condition):
        if not condition: raise AssertionError(label)
        checks.append({'name':label,'passed':True})
    for name,shape in schema['$defs'].items():
        Draft202012Validator.check_schema(shape)
    check('全部JSON Schema定义合法',True)
    validate_spec(api)
    check('OpenAPI 3.1规范校验',True)
    def convert(value):
        if isinstance(value,dict):return {k:(v.replace('#/$defs/','#/components/schemas/') if k=='$ref' else convert(v)) for k,v in value.items()}
        if isinstance(value,list):return [convert(x) for x in value]
        return value
    check('OpenAPI组件与协议主记录精确一致',api['components']['schemas']==convert(schema['$defs']))
    def validate(name,payload):
        target={'$schema':schema['$schema'],'$ref':'#/$defs/'+name,'$defs':schema['$defs']}
        return list(Draft202012Validator(target,format_checker=FormatChecker()).iter_errors(payload))
    u='00000000-0000-0000-0000-000000000001';h='a'*64;t='2026-10-16T08:00:00Z'
    source={'id':u,'digest':h,'kind':'body','known_at':t,'lineage_manifest_digest':h}
    score={'bid_id':u,'review_id':u,'body_version_id':u,'rubric_digest':h,'chapter_package_ids':[u],'prebid_fact_ids':[],'source_refs':[source],'as_of':t,'method_digest':h,'purpose':'formal_score','policy_digest':h}
    operation={'id':u,'bid_id':u,'kind':'review','state':'running','input_digest':h,'handler_version':'handler-1','result_ref':None}
    build={'draft_id':u,'draft_revision':2,'template_version_id':u,'formats':['docx','pdf'],'follow_up':'review'}
    saved={'base_draft_revision':1,'changes':[{'section_id':u,'base_section_revision':1,'schema_digest':h,'content':{'type':'doc','content':[]}}]}
    outcome={'submission_id':u,'score_kind':'technical_total','value':'0','maximum':'100','source_version_id':u,'known_at':t}
    manifest={'bid_id':u,'body_version_id':u,'material_snapshot_id':u,'material_revision':1,'rule_set_id':u,'fact_set_id':None,'parse_run_ids':[u],'method_digest':h,'policy_digest':h,'purpose':'compliance_and_scoring','source_refs':[source],'content_checks':{'consistency':True,'scope_residue':True},'frozen_at':t,'as_of':t,'evaluation_context_digest':h,'evidence_manifest_digest':h}
    judgment={'score_item_id':u,'chapter_package_id':u,'replicate':1,'status':'valid','band_id':'a','coverage':'complete','evidence_ids':[u],'reason':'正文有对应内容','method_digest':h}
    task={'schema_version':1,'event_id':u,'event_type':'step.requested','bid_id':u,'operation_id':u,'step_key':'judge-item-1-replicate-1','input_digest':h,'generation':1,'handler_version':'handler-1','payload_ref':u,'created_at':t}
    search={'query':'管网施工的临时排水措施','context':'body_evidence','bid_id':u,'limit':8}
    examples=[('TaskEnvelope',task),('RetrievalRequest',search),('ScoreInput',score),('Operation',operation),('BuildRequest',build),('SaveDraft',saved),('OutcomeRequest',outcome),('ReviewManifest',manifest),('ItemJudgment',judgment)]
    for name,payload in examples:check('合法夹具 '+name,not validate(name,payload))
    negatives=[]
    for key in ['chat_history','official_result','reviewer_feedback','conversation_summary']:
        payload=copy.deepcopy(score);payload[key]='事后内容';negatives.append(('正式评分拒绝 '+key,'ScoreInput',payload))
    payload=copy.deepcopy(score);payload['source_refs'][0]['kind']='official_result';negatives.append(('嵌套来源拒绝成绩类型','ScoreInput',payload))
    payload=copy.deepcopy(score);payload['source_refs'][0]['digest']='not-a-digest';negatives.append(('来源摘要必须合法','ScoreInput',payload))
    for replicate in [0,4]:
        payload=copy.deepcopy(judgment);payload['replicate']=replicate;negatives.append(('三次判断编号限制 '+str(replicate),'ItemJudgment',payload))
    payload=copy.deepcopy(build);payload['draft_revision']=0;negatives.append(('构建拒绝无效已保存修订','BuildRequest',payload))
    payload=copy.deepcopy(build);payload['formats']=['pdf','pdf'];negatives.append(('构建格式不能重复','BuildRequest',payload))
    payload=copy.deepcopy(operation);payload['state']='cancel_request_is_done';negatives.append(('操作拒绝混淆取消请求与终态','Operation',payload))
    payload=copy.deepcopy(outcome);payload['value']=0;negatives.append(('分数交换必须十进制字符串','OutcomeRequest',payload))
    payload=copy.deepcopy(outcome);payload['value']='NaN';negatives.append(('分数拒绝NaN','OutcomeRequest',payload))
    payload=copy.deepcopy(saved);payload['changes']=[];negatives.append(('保存拒绝空改动','SaveDraft',payload))
    payload=copy.deepcopy(manifest);payload['purpose']='casual_chat';negatives.append(('冻结评审禁止错误用途','ReviewManifest',payload))
    negatives.append(('报告导出不能冒充正文','ReportRequest',{'artifact_kind':'bid_body','format':'pdf','presentation_revision':1,'source_impact_revision':1}))
    payload=copy.deepcopy(task);payload['pdf_bytes']='inline-data';negatives.append(('Kafka消息拒绝夹带整份文件','TaskEnvelope',payload))
    payload=copy.deepcopy(task);payload.pop('generation');negatives.append(('Kafka消息必须有执行代次','TaskEnvelope',payload))
    payload=copy.deepcopy(search);payload['context']='formal_score';negatives.append(('公开检索不能授予正式评分用途','RetrievalRequest',payload))
    payload=copy.deepcopy(search);payload['allowed_manifest']=[u];negatives.append(('客户端不能覆盖来源允许集','RetrievalRequest',payload))
    payload=copy.deepcopy(search);payload['limit']=1000;negatives.append(('检索限制候选数量','RetrievalRequest',payload))
    for label,name,payload in negatives:check(label,bool(validate(name,payload)))
    operations=[(path,method,op) for path,methods in api['paths'].items() for method,op in methods.items()]
    for path,method,op in operations:
        for code,response in op['responses'].items():
            if code.startswith('2'):
                name=response.get('content',{}).get('application/json',{}).get('schema',{}).get('$ref','').split('/')[-1]
                check('成功响应有具体业务形状 '+op['operationId'],name not in {'Reference','ListPage'})
    check('全新接口前缀冻结',api['servers'][0]['url']=='/api/v1')
    hidden={'review_id':u,'mode':'hidden','maximum':'100','reasons':[],'method_digest':h}
    check('隐藏预测只保留资格与满分',not validate('PredictionView',hidden))
    exposed=dict(hidden,value='88')
    check('隐藏预测不得夹带数值',bool(validate('PredictionView',exposed)))
    check('创建项目只需名称',not validate('CreateBid',{'title':'合成测试项目'}))
    ids=[op['operationId'] for _,_,op in operations]
    check('operationId全局唯一',len(ids)==len(set(ids)))
    for path,method,op in operations:
        if method in ['post','patch','delete'] and op['operationId']!='login':
            refs={p.get('$ref') for p in op.get('parameters',[])}
            check('写入幂等与CSRF '+op['operationId'],{'#/components/parameters/IdempotencyKey','#/components/parameters/CSRF'} <= refs)
    operations_by_id={op['operationId']:op for _,_,op in operations}
    http_examples=json.loads((DESIGN/'http-examples.json').read_text())['examples']
    for example in http_examples:
        check('接口示例合法 '+example['operation_id'],not validate(example['schema'],example['body']))
        op=operations_by_id[example['operation_id']]
        if example['direction']=='request':
            bound=op['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
            check('请求示例绑定真实契约 '+example['operation_id'],bound==example['schema'])
        else:
            bound={resp.get('content',{}).get('application/json',{}).get('schema',{}).get('$ref','').split('/')[-1] for code,resp in op['responses'].items() if code.startswith('2')}
            check('响应示例绑定真实契约 '+example['operation_id'],example['schema'] in bound)
    check('全局会话认证',api['security']==[{'SessionCookie':[]}])
    check('指定Kafka与独立Python执行',policy['message_infrastructure']=='kafka' and policy['worker_runtime']=='python')
    check('Kafka禁止自动提交和提前store',policy['consumer']['enable.auto.commit'] is False and policy['consumer']['enable.auto.offset.store'] is False)
    check('起步每分区一个在途步骤',policy['consumer']['max_inflight_per_partition']==1)
    check('重试持久保存于PG',policy['retry_store']=='postgresql_outbox.available_at')
    check('四类任务topic与group独立',len({v['topic'] for v in policy['topic_bindings'].values()})==4 and len({v['consumer_group'] for v in policy['topic_bindings'].values()})==4)
    check('RAG为必需能力且明确向量存储',retrieval['required_capability'] is True and retrieval['vector_store']=='postgresql_pgvector')
    check('正式评分扩展完整章节',retrieval['formal_score_assembly']=='expand_retrieved_chapters_to_full_ranges')
    check('全新系统无旧系统迁移要求',retrieval['legacy_migration_required'] is False)
    transitions=policy['operation_transitions']
    check('三个终态无出边',all(transitions[k]==[] for k in ['succeeded','failed','cancelled']))
    check('取消请求之后禁止发布成功',transitions['cancel_requested']==['cancelled'])
    check('所有状态引用存在',all(v in transitions for values in transitions.values() for v in values))
    trace=json.loads((HERE/'requirements-trace.json').read_text())
    required={f'FR-{n:03}' for n in range(1,41)}|{f'NFR-{n:03}' for n in range(1,15)}
    check('54项产品需求都有详细设计落点',{r['requirement'] for r in trace['trace']}==required and len(trace['trace'])==54)
    doc=DOC.read_text();sections=set(re.findall(r'^#{2,3} (\d+(?:\.\d+)?)\.',doc,re.M))
    # Subsection headings use a number followed by a space, while chapter headings use a period.
    sections |= set(re.findall(r'^#{2,3} (\d+\.\d+) ',doc,re.M))
    for row in trace['trace']:check('落点存在 '+row['requirement'],all(section in sections for section in row['sections']))
    linked=0
    files=[DOC,DESIGN/'README.md',HERE/'README.md']+list(DESIGN.glob('*实现规约.md'))+[DESIGN/'部署与交付手册.md',ROOT/'04-验证与测试/implementation-design-checks/README.md']
    for file in files:
        for href in re.findall(r'\]\(([^)]+)\)',file.read_text()):
            if re.match(r'^(https?:|app:|#)',href):continue
            target=(file.parent/href.split('#')[0]).resolve()
            check('本地链接 '+target.name,target.exists());linked+=1
    inputs=[DOC,DESIGN/'core.sql',DESIGN/'domain.sql',DESIGN/'retrieval.sql',DESIGN/'worker-functions.sql',DESIGN/'roles.sql',DESIGN/'protocol.schema.json',DESIGN/'http-examples.json',DESIGN/'openapi.json',DESIGN/'execution-policy.json',DESIGN/'retrieval-policy.json',DESIGN/'README.md',HERE/'requirements-trace.json',HERE/'README.md',HERE/'requirements.lock.txt',DESIGN/'数据与事务实现规约.md',DESIGN/'接口与任务实现规约.md',DESIGN/'RAG与评分实现规约.md',DESIGN/'部署与交付手册.md',DESIGN/'deploy/compose.yaml',DESIGN/'deploy/init_topics.py',ROOT/'04-验证与测试/implementation-design-checks/README.md',Path(__file__)]
    return {'passed':True,'scope':'目标契约规范、合法／反例夹具、需求落点与本地文件链接；不调用产品','executed_at':datetime.now(timezone.utc).isoformat(),'schema_definitions':len(schema['$defs']),'api_paths':len(api['paths']),'api_operations':len(operations),'positive_examples':len(examples),'rejected_counterexamples':len(negatives),'requirements_traced':len(trace['trace']),'local_links_checked':linked,'checks':len(checks),'dependencies':{n:version(n) for n in ['jsonschema','openapi-spec-validator']},'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'software_end_to_end_executed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['inputs']},ensure_ascii=False))
