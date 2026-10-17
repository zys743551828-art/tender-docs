"""Observation wrappers only on the public fixture; no bypass or altered outcome."""
import json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-port-static-01/frozen-tree')
sys.path.insert(0,str(ROOT))
from packages.authoring import generated_docx,generated_renderer
source=(HERE/'source.docx').read_bytes();snapshot=json.loads((HERE/'snapshot.json').read_text());profile=json.loads((HERE/'public-profile.json').read_text())
observed={}
original_read=generated_renderer.sandbox._read_process
original_inspect=generated_renderer.inspect_pdf
original_fonts=generated_renderer.sandbox._font_gaps
def read(*args,**kwargs):
 data=original_read(*args,**kwargs)
 header,pdf=data.split(b'\n',1) if b'\n' in data else (data,b'')
 observed['runner_header']=header.decode(errors='replace')[:1024]
 observed['returned_pdf_bytes']=len(pdf)
 if pdf.startswith(b'%PDF-'):(HERE/'first-diagnostic-raw.pdf').write_bytes(pdf)
 return data
def inspect(*args,**kwargs):
 try:
  result=original_inspect(*args,**kwargs);observed['inspection_result']=result;return result
 except Exception as error:
  observed['inspection_exception']={'type':type(error).__name__,'message':str(error)[:1024]};raise
def fonts(*args,**kwargs):
 try:
  result=original_fonts(*args,**kwargs);observed['font_result']=result;return result
 except Exception as error:
  observed['font_exception']={'type':type(error).__name__,'message':str(error)[:1024]};raise
generated_renderer.sandbox._read_process=read;generated_renderer.inspect_pdf=inspect;generated_renderer.sandbox._font_gaps=fonts
result=generated_renderer.render(source,snapshot,generated_docx.producer_manifest(source,snapshot),profile)
observed['outcome']={k:v for k,v in result.items() if k!='pdf'}
observed['scope']='Observation wrappers around unchanged static01 implementations, public fixture only, no bypass; not independent pass evidence.'
(HERE/'first-diagnostic.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in observed.items() if k not in {'outcome','inspection_result'}},ensure_ascii=False))
