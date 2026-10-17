import json
from pathlib import Path
import sys
sys.path.insert(0,'/workspace/tender-workbench')
from packages.word_intake.renderer import configured_profile,render
from packages.authoring.build import assemble_docx
from packages.authoring.content import empty_content
HERE=Path(__file__).resolve().parent
samples={'word03_original_positive':Path('/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03/real-word-source.docx').read_bytes(),'minimal_authoring':assemble_docx({'sections':[empty_content('合成生成正文',0)]})}
profile=configured_profile();results={}
for name,raw in samples.items():
 (HERE/(name+'.docx')).write_bytes(raw)
 result=render(raw,profile)
 if result['pdf']:(HERE/(name+'.pdf')).write_bytes(result['pdf'])
 results[name]={k:v for k,v in result.items() if k!='pdf'}
 print(json.dumps({'case':name,'state':result['state'],'gaps':result['gaps'],'coverage':result['coverage']},ensure_ascii=False),flush=True)
(HERE/'static-word03-comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
