"""Exact reviewed PAGE lane; true local conversion variants and explicit timeout injection."""
import copy,json,sys,time
from pathlib import Path
from uuid import uuid4
HERE=Path(__file__).resolve().parent
STATIC=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-port-static-01')
sys.path.insert(0,str(STATIC/'frozen-tree'))
from packages.authoring import generated_docx,generated_renderer
from packages.domain.runtime import sha
profile=json.loads((HERE/'public-profile.json').read_text());base=json.loads((HERE/'snapshot.json').read_text())
def p(text):return {'type':'paragraph','attrs':{'id':str(uuid4())},'content':[{'type':'text','text':text}]}
def li(text):return {'type':'listItem','attrs':{'id':str(uuid4())},'content':[p(text)]}
items=[('one_page',[p('第1项施工：正文保留1、12、120天，页脚必须独立核对。')],{}),('native_lists',[p('正文数字1、2、12不应被页脚核验删除。'),{'type':'orderedList','attrs':{'id':str(uuid4())},'content':[li('第一项质量措施'),li('第二项进度措施')]},{'type':'bulletList','attrs':{'id':str(uuid4())},'content':[li('安全管理'),li('材料核对')]}],{}),('serif_font',[p('宋体样式的中文正文与数字120，保留完整。')],{'font':'Noto Serif CJK SC'}),('missing_glyph',[p('此处故意包含可能缺失的新字形：\U0001fae9，缺字不得授完整对应。')],{}),('tight_regions',[p('页眉页脚与正文区域可能重叠，必须报告缺口。')],{'margin_top_twips':360,'margin_bottom_twips':360}),('timeout',[p('真实沙箱启动后的独立超时故障注入。')],{})]
reports={};original=generated_renderer.sandbox._read_process
for name,body,changes in items:
 snapshot=copy.deepcopy(base);snapshot['sections'][0]['content']=body
 ext=snapshot['authoring_structure'];config=ext['style_template']['config']|{'cover_enabled':False,'toc_enabled':False}|changes
 ext['style_template']['config']=config;ext['style_template']['config_digest']=sha(config);ext['digest']=sha({k:v for k,v in ext.items() if k!='digest'})
 source=generated_docx.assemble(snapshot);manifest=generated_docx.producer_manifest(source,snapshot)
 if name=='timeout':
  generated_renderer.sandbox._read_process=lambda process,deadline:original(process,time.monotonic()+0.001)
 try:result=generated_renderer.render(source,snapshot,manifest,profile)
 finally:generated_renderer.sandbox._read_process=original
 (HERE/(name+'.docx')).write_bytes(source)
 if result['pdf']:(HERE/(name+'.pdf')).write_bytes(result['pdf'])
 report={k:v for k,v in result.items() if k!='pdf'};report.update(scope='Exact static01 PAGE lane, real sandbox on public synthetic input; no DB/model/Kafka/OSS',fault_injection='read deadline 0.001sec test-only, not capacity' if name=='timeout' else None)
 (HERE/(name+'-result.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 reports[name]={'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'pages':result['chain'].get('page_count'),'cleanup':(result['isolation'] or {}).get('own_job_cleanup_confirmed')}
 print(json.dumps({'case':name}|reports[name],ensure_ascii=False),flush=True)
(HERE/'variants-summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
