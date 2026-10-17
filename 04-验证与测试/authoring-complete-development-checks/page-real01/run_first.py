"""Approved exact PAGE static01 code, actual local sandbox; public synthetic text only."""
import copy,json,os,sys
from pathlib import Path
from uuid import uuid4
HERE=Path(__file__).resolve().parent
STATIC=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-port-static-01')
ROOT=STATIC/'frozen-tree'
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from packages.authoring import generated_docx,generated_renderer
from packages.domain.runtime import sha
from packages.word_intake import renderer as sandbox
from test_authoring_generated import snapshot as fixture
records=json.loads((STATIC/'inputs.json').read_text())['sources']
for row in records:
 if sha(Path(row['archive']).read_bytes())!=row['sha256']:raise RuntimeError('static input drift')
base=sandbox.build_profile();profile=generated_renderer.profile_for(base)
private=Path('/workspace/tender-workbench/.local/authoring-page-static01-profile.json')
private.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n');private.chmod(0o600)
(HERE/'public-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
snapshot=fixture.__wrapped__()
section=snapshot['sections'][0]
section['attrs']['title']='施工组织与进度安排'
section['content']=[{'type':'paragraph','attrs':{'id':str(uuid4())},'content':[{'type':'text','text':f'第{index+1}项施工安排：保留正文数字1、12、120。施工人员按工序核对材料、质量和安全措施，未确认事实须继续核实。'}]} for index in range(80)]
config=copy.deepcopy(snapshot['authoring_structure']['style_template']['config'])|{'cover_enabled':True,'cover_title':'公开合成多页正文','header_text':'公开合成项目','footer_text':'施工正文'}
extension=snapshot['authoring_structure'];extension['style_template']['config']=config;extension['style_template']['config_digest']=sha(config);extension['digest']=sha({k:v for k,v in extension.items() if k!='digest'})
source=generated_docx.assemble(snapshot);manifest=generated_docx.producer_manifest(source,snapshot)
(HERE/'source.docx').write_bytes(source);(HERE/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n')
result=generated_renderer.render(source,snapshot,manifest,profile)
if result['pdf']:(HERE/'output.pdf').write_bytes(result['pdf'])
report={k:v for k,v in result.items() if k!='pdf'}
report.update(scope='Exact architecture-reviewed PAGE port static01; real local Docker/LibreOffice/PDFium, public synthetic multi-page Chinese, no DB/model/Kafka/OSS',static_candidate_sha256=sha((STATIC/'candidate.json').read_bytes()),inputs_verified_before_after=len(records))
for row in records:
 if sha(Path(row['archive']).read_bytes())!=row['sha256']:raise RuntimeError('static input changed during render')
(HERE/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'pages':result['chain'].get('page_count'),'cleanup':(result['isolation'] or {}).get('own_job_cleanup_confirmed'),'body_digest_match':result.get('correspondence',{}).get('actual_body_digest')==result.get('correspondence',{}).get('expected_body_digest')},ensure_ascii=False))
