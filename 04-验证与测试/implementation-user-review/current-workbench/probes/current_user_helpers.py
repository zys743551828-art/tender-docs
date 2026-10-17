import json,datetime
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/current-workbench')
BASE='http://127.0.0.1:5175'
PROJECT=json.loads(Path('/tmp/current-user-project.json').read_text())['url'].split('/bids/')[1].split('/')[0]
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def guarded(c,events):
 def guard(route):
  req=route.request;path=urlparse(req.url).path
  blocked=False
  if req.method not in ('GET','HEAD','OPTIONS'):
   forbidden=['/reviews','/research','/predictions','/models','/chat','/assistant','/prepare','/rules/extract']
   blocked=any(s in path for s in forbidden)
   try:data=req.post_data_json
   except Exception:data=None
   if isinstance(data,dict):
    if 'build' in path and req.method=='POST' and data.get('follow_up')!='none':blocked=True
    if 'follow_up' in data and data['follow_up']!='none':blocked=True
    if str(data.get('kind','')).startswith(('review','research','predict','llm')):blocked=True
   if blocked:
    events.append({'at':stamp(),'method':req.method,'path':path,'blocked_by_user_review_guard':True});route.abort();return
  route.continue_()
 c.route('**/*',guard)
 def response(r):
  path=urlparse(r.url).path
  if path.startswith('/api/v1/'):
   entry={'at':stamp(),'method':r.request.method,'path':path,'status':r.status}
   if 'build' in path and r.request.method=='POST':
    try:entry['follow_up']=r.request.post_data_json.get('follow_up')
    except Exception:pass
   events.append(entry);(ROOT/'latest-live-request-log.json').write_text(json.dumps(events,ensure_ascii=False,indent=2)+'\n')
 c.on('response',response)
def capture(page,name):
 page.screenshot(path=str(ROOT/(name+'.png')),full_page=True)
 (ROOT/(name+'.txt')).write_text(page.locator('body').inner_text())
