import json
from playwright.sync_api import sync_playwright
from web06_user_helpers import *
a=json.load(open(ROOT/'report-exports-final-result.json'));new=[];seen=set();started=False
for e in a['events']:
 if e['method']=='POST'and e['path'].endswith('/report-exports'):started=True
 if '/operations/'in e['path'] and e['path']not in seen:
  seen.add(e['path'])
  if started:new.append(e['path'])
with sync_playwright()as p:
 req=p.request.new_context(storage_state='/tmp/web06-user-auth.json');rows=[]
 for path in new:
  res=req.get(BASE+path);d=res.json();row={'path':path,'http':res.status,'keys':list(d),'id':d.get('id'),'kind':d.get('kind'),'state':d.get('state'),'status':d.get('status'),'phase':d.get('phase'),'failure_code':d.get('failure_code')};rows.append(row)
 (ROOT/'report-terminal-read-result.json').write_text(json.dumps({'at':stamp(),'operations':rows},ensure_ascii=False,indent=2));print(rows);req.dispose()
