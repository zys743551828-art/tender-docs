import json,datetime
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/web06')
BASE='http://127.0.0.1:5176'
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def guarded(c,events):
 def route(r):
  req=r.request;path=urlparse(req.url).path
  if req.method not in ('GET','HEAD','OPTIONS'):
   deny=any(x in path for x in ['/prepare','/research','/predictions','/models','/assistant','/chat']) or path.endswith('/reviews')
   try:data=req.post_data_json
   except Exception:data=None
   if isinstance(data,dict):
    if 'follow_up' in data and data['follow_up']!='none':deny=True
    if str(data.get('kind','')).startswith(('review','research','predict','llm')):deny=True
   if deny:events.append({'at':stamp(),'method':req.method,'path':path,'blocked':True});r.abort();return
  r.continue_()
 c.route('**/*',route)
 def response(r):
  path=urlparse(r.url).path
  if path.startswith('/api/v1/'):
   events.append({'at':stamp(),'method':r.request.method,'path':path,'status':r.status});(ROOT/'latest-request-log.json').write_text(json.dumps(events,ensure_ascii=False,indent=2)+'\n')
 c.on('response',response)
def capture(page,name):
 page.screenshot(path=str(ROOT/(name+'.png')),full_page=True);(ROOT/(name+'.txt')).write_text(page.locator('body').inner_text())
