"""Pure synthetic OPC parts; no renderer or active field is executed."""
from pathlib import Path
import hashlib,sys,json,io,zipfile

HERE=Path(__file__).resolve().parent
BASE=HERE.parents[1]/'word-intake-development-checks/candidate-01'
p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='199d31e0bde9a9cccb8f9893127e7962ead4b035c3bd8e045327474bd8bbf98a'
m=json.loads(p.read_text());ROOT=BASE/'frozen-inputs'
for e in m['sources']:assert hashlib.sha256((BASE/e['frozen_path']).read_bytes()).hexdigest()==e['sha256']
DEPS=HERE.parents[1]/'assistant-development-checks/implementation-01/frozen-tree'
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(DEPS)]
from test_word_intake import make_docx
from packages.word_intake.inspection import inspect_docx

records=[]
for name in ['header1.xml','header1.XML','header1.dat']:
    raw=make_docx(body='<w:p><w:r><w:t>合成普通正文</w:t></w:r></w:p><w:sectPr><w:headerReference xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" w:type="default" r:id="rHeader"/></w:sectPr>')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:entries={key:z.read(key) for key in z.namelist()}
    entries['[Content_Types].xml']=entries['[Content_Types].xml'].replace(b'</Types>',f'<Override PartName="/word/{name}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>'.encode())
    entries['word/_rels/document.xml.rels']=f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="{name}"/></Relationships>'.encode()
    entries['word/'+name]=b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>DDEAUTO harmless-test-only no-execution</w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:hdr>'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for key,data in entries.items():z.writestr(key,data)
    source=buffer.getvalue();result=inspect_docx(source)
    (HERE/(name+'.docx')).write_bytes(source)
    records.append({'part':name,'sha256':hashlib.sha256(source).hexdigest(),'conversion_eligible':result['conversion_eligible'],'active_content_gaps':result['active_content_gaps'],'unsupported_layout_features':result['unsupported_layout_features'],'xml_part_count':result['xml_part_count']})
assert records[0]['conversion_eligible'] is False
assert all(row['conversion_eligible'] and not row['active_content_gaps'] for row in records[1:])
(HERE/'hidden-xml-counterexample.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':'pure synthetic OOXML inspection only; no field/render/PG/object execution','records':records,'paid_calls':0,'renderer_jobs':0},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(records,ensure_ascii=False))
