"""Public static DOCX only; Word03 existing sandbox, no dynamic PAGE execution."""
import io,json,zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
import sys
sys.path.insert(0,'/workspace/tender-workbench')
from packages.word_intake.renderer import configured_profile,render
HERE=Path(__file__).resolve().parent
raw=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/candidate-01/evidence/synthetic-tree-plan-templates.docx').read_bytes()
with zipfile.ZipFile(io.BytesIO(raw)) as z: original={n:z.read(n) for n in z.namelist()}
def normalize(data,namespace):
 root=ET.fromstring(data)
 for item in root.iter():
  if not item.tag.startswith('{'+namespace+'}'):raise RuntimeError('unexpected namespace')
  item.tag=item.tag.split('}',1)[1]
 root.set('xmlns',namespace)
 return ET.tostring(root,encoding='utf-8',xml_declaration=True)
results={};profile=configured_profile()
for mode in ['content_types_default','content_types_and_relationships_default']:
 files=deepcopy(original)
 files['[Content_Types].xml']=normalize(files['[Content_Types].xml'],'http://schemas.openxmlformats.org/package/2006/content-types')
 if mode.endswith('relationships_default'):
  files['word/_rels/document.xml.rels']=normalize(files['word/_rels/document.xml.rels'],'http://schemas.openxmlformats.org/package/2006/relationships')
 output=io.BytesIO()
 with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
  for name,data in sorted(files.items()):archive.writestr(name,data)
 source=output.getvalue();(HERE/(mode+'.docx')).write_bytes(source)
 result=render(source,profile)
 if result['pdf']:(HERE/(mode+'.pdf')).write_bytes(result['pdf'])
 results[mode]={k:v for k,v in result.items() if k!='pdf'}
 print(json.dumps({'case':mode,'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'pages':result['chain'].get('page_count')},ensure_ascii=False),flush=True)
(HERE/'opc-namespace-comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
