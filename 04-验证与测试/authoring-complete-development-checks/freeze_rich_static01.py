import hashlib,json,shutil
from pathlib import Path
live=Path('/workspace/tender-workbench');p=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks');base=p/'candidate-02';old=json.loads((base/'inputs.json').read_text());d=p/'rich-port-static-01';d.mkdir(exist_ok=False);r=d/'frozen-tree';records={}
own=json.loads((p/'rich-port-preflight-01/inputs.json').read_text())['own_sources']
def put(path,source,layer):
 raw=source.read_bytes();target=r/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);records[path]={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'archive':str(target),'layer':layer}
for i in old['records']:
 assert hashlib.sha256(Path(i['archive']).read_bytes()).hexdigest()==i['sha256'];put(i['path'],Path(i['archive']),'fixed-PAGE02-base')
for path in own:put(path,live/path,'rich-port-own')
for path in ['packages/authoring/page_navigation.py','packages/authoring/PAGE_NAVIGATION.md','tests/test_authoring_page_navigation.py','requirements-parsing.lock.txt']:put(path,live/path,'explicit-rich-port-dependency')
assert records['packages/authoring/page_navigation.py']['sha256']=='1413dcbd1de59db949c602da9f913fe9b31ece0f2976fc47c678baecc3765054'
(r/'packages/word_intake/sandbox_runner.py').chmod(0o444)
(d/'inputs.json').write_text(json.dumps({'root':str(r),'own_sources':own,'records':sorted(records.values(),key=lambda i:i['path']),'base_candidate':str(base/'candidate.json'),'runner_mode':'0444','execution_status':'static_only_no_rich_Docker'},ensure_ascii=False,indent=2)+'\n')
(d/'evidence').mkdir();print(json.dumps({'root':str(r),'own':len(own),'total':len(records)}))
