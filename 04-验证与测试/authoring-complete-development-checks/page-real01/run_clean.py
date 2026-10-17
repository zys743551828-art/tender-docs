"""Clean exact static01 PAGE port after public runner mode correction; no wrappers."""
import json,sys,stat
from pathlib import Path
HERE=Path(__file__).resolve().parent
STATIC=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-port-static-01')
sys.path.insert(0,str(STATIC/'frozen-tree'))
from packages.authoring import generated_docx,generated_renderer
from packages.domain.runtime import sha
records=json.loads((STATIC/'inputs.json').read_text())['sources']
for item in records:
 if sha(Path(item['archive']).read_bytes())!=item['sha256']:raise RuntimeError('static source mismatch')
source=(HERE/'source.docx').read_bytes();snapshot=json.loads((HERE/'snapshot.json').read_text());profile=json.loads((HERE/'public-profile.json').read_text())
result=generated_renderer.render(source,snapshot,generated_docx.producer_manifest(source,snapshot),profile)
if result['pdf']:(HERE/'clean-output.pdf').write_bytes(result['pdf'])
report={k:v for k,v in result.items() if k!='pdf'}
report.update(scope='Exact architecture-reviewed static01 PAGE sources; actual clean Docker/LibreOffice/PDFium, no wrappers, public synthetic 80-paragraph Chinese, no DB/model/Kafka/OSS',static_candidate_sha256=sha((STATIC/'candidate.json').read_bytes()),runner_mode=oct(stat.S_IMODE((STATIC/'frozen-tree/packages/word_intake/sandbox_runner.py').stat().st_mode)))
for item in records:
 if sha(Path(item['archive']).read_bytes())!=item['sha256']:raise RuntimeError('static source changed')
(HERE/'clean-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
correspondence=result.get('correspondence',{})
print(json.dumps({'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'pages':result['chain'].get('page_count'),'all_footers':all(p['footer_matches'] for p in correspondence.get('pages',[])) if correspondence.get('pages') else None,'body_digest_equal':correspondence.get('actual_body_digest')==correspondence.get('expected_body_digest') if correspondence.get('actual_body_digest') else None,'cleanup':(result['isolation'] or {}).get('own_job_cleanup_confirmed')},ensure_ascii=False))
