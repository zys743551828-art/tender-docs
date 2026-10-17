"""Pure XML field fragments; no field instruction or renderer execution."""
from pathlib import Path
import sys,json,hashlib
HERE=Path(__file__).resolve().parent;BASE=HERE.parents[1]/'word-intake-development-checks/candidate-02';p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='5dcc4dfad4ee05df0ae326c9bb1f64eff9faaae2abd41e4aa40bb4717e7cb2db';m=json.loads(p.read_text())
for e in m['sources']:assert hashlib.sha256((BASE/e['frozen_path']).read_bytes()).hexdigest()==e['sha256']
sys.path[:0]=[str(BASE/'frozen-inputs'),str(BASE/'frozen-inputs/tests'),str(HERE.parents[1]/'assistant-development-checks/implementation-01/frozen-tree')]
from test_word_intake import make_docx
from packages.word_intake.inspection import inspect_docx
cases=[['DDEAUTO harmless-test-only no-execution'],['DD','EAUTO harmless-test-only no-execution'],['INCLUDE','TEXT harmless-test-only']]
records=[]
for i,segments in enumerate(cases):
    body='<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'+''.join('<w:r><w:instrText xml:space="preserve">'+s+'</w:instrText></w:r>' for s in segments)+'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    data=make_docx(body=body);r=inspect_docx(data);(HERE/f'field-runs-{i}.docx').write_bytes(data)
    records.append({'segments':segments,'joined_instruction':''.join(segments),'conversion_eligible':r['conversion_eligible'],'active_content_gaps':r['active_content_gaps'],'unsupported_layout_features':r['unsupported_layout_features'],'sha256':hashlib.sha256(data).hexdigest()})
assert records[0]['conversion_eligible'] is False
assert all(r['conversion_eligible'] and not r['active_content_gaps'] for r in records[1:])
(HERE/'fragmented-field-counterexample.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':'pure synthetic OOXML inspection; no Docker/field/network execution','records':records},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(records,ensure_ascii=False))
