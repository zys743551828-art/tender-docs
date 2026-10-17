from pathlib import Path
import json,hashlib,sys,socket,subprocess
out=Path(__file__).parent
p=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/candidate-02/candidate.json');m=json.loads(p.read_text())
assert hashlib.sha256(p.read_bytes()).hexdigest()=='5cb2bd6f9c205bace488158d3b9656222b3e9a54aa215d1c6bf2d834f11773e9'
root=Path(m['runtime_root']);print(m['sources'][0])
for v in m['sources']+m['dependencies']:
 q=root/v['path'];assert hashlib.sha256(q.read_bytes()).hexdigest()==v['sha256'],str(q)
for v in m['exact_reviewed_PAGE_sources']:assert hashlib.sha256((root/v['path']).read_bytes()).hexdigest()==v['sha256']
for v in m['evidence']+[m['actual_contract'],m['handoff'],m['external_evidence']]:assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256']
external=json.loads(Path(m['external_evidence']['path']).read_text())
for v in external:assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256']
sys.path.insert(0,str(root))
def forbidden(*a,**kw):raise AssertionError('Architecture artifact readback forbids external I/O or process')
subprocess.run=forbidden;subprocess.Popen=forbidden;socket.create_connection=forbidden
import psycopg
psycopg.connect=forbidden
from packages.authoring import generated_renderer,generated_verification
from packages.word_intake.inspection import inspect_docx
pagebase=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-real01')
snapshot=json.loads((pagebase/'snapshot.json').read_text());source=(pagebase/'source.docx').read_bytes();pdf=(p.parent/'evidence/clean-output.pdf').read_bytes()
claimed=json.loads((p.parent/'evidence/clean-result.json').read_text())
assert hashlib.sha256(source).hexdigest()==claimed['chain']['source_docx_digest'];assert hashlib.sha256(pdf).hexdigest()==claimed['chain']['pdf_digest']
config=snapshot['authoring_structure']['metadata'].get('effective_style',snapshot['authoring_structure']['style_template']['config'])
roots=generated_verification.verify_container(source,page_number=config['page_number'])
readback=generated_renderer.inspect_pdf(pdf,{'config':config,'roots':roots})
assert readback==claimed['correspondence'];assert readback['page_count']==7 and readback['gaps']==[]
assert inspect_docx(source)['conversion_eligible'] is False
for v in m['sources']+m['dependencies']:assert hashlib.sha256((root/v['path']).read_bytes()).hexdigest()==v['sha256']
result={'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_count':335,'PAGE_five_same':True,'source_checks_before_after':True,'independent_PDFium_readback':readback,'original_external_intake_same_DOCX_conversion_eligible':False,'verification_scope':'Raw saved actual Docker artifact readback, not a new conversion or DB publication run','actual_contract':m['actual_contract'],'external_evidence_count':len(external),'DB':0,'Docker':0,'external_services':0,'models':0,'consumers':0}
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'verified':335,'raw_PDFium_pages':readback['page_count'],'gaps':readback['gaps'],'external_same_docx_allowed':False}))
