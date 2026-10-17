import json,re,hashlib
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from web05_user_helpers import *
f=json.loads(Path('/tmp/web05-user-fixture.json').read_text());events=[];parts=[];completes=[];state={'part_abort':True,'lose_ack':True,'ack_committed':False}
def save():
 (ROOT/'multipart-final-result.json').write_text(json.dumps({'at':stamp(),'parts':parts,'completes':completes,'state':state,'events':events},ensure_ascii=False,indent=2))
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web05-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(15000)
 def route(r):
  req=r.request;path=urlparse(req.url).path;match=re.search(r'/object-transfers/multipart/[^/]+/parts/(\d+)$',path)
  if req.method=='PUT' and match:
   item={'at':stamp(),'part_number':int(match[1]),'path':path,'bytes':len(req.post_data_buffer or b'')};parts.append(item);save()
   if state['part_abort'] and match[1]=='2':state['part_abort']=False;item['injected_abort']=True;save();r.abort('failed');return
  if req.method=='POST' and path.endswith('/complete'):
   item={'at':stamp(),'path':path,'idempotency_key':req.headers.get('idempotency-key'),'if_match':req.headers.get('if-match'),'body_sha':hashlib.sha256((req.post_data or '').encode()).hexdigest()};completes.append(item);save()
   if state['lose_ack']:
    state['lose_ack']=False
    try:
     resp=r.fetch(timeout=60000);item['committed_status']=resp.status;item['injected_ack_loss']=True;state['ack_committed']=True;save();r.abort('failed');return
    except Exception as e:state['intercept_exception']=type(e).__name__;save();r.abort('failed');return
  r.fallback()
 page.route('**/*',route)
 try:
  page.goto(f['source_url'],wait_until='domcontentloaded');page.get_by_role('button',name='确认 1 份资料入库',exact=True).click();page.get_by_text('已入库资料 1',exact=False).wait_for();capture(page,'16-source-committed')
  page.goto(f['region_mismatch_url'],wait_until='domcontentloaded');page.locator('input[type=file]').set_input_files(str(ROOT/'fixtures/user05-large-three-pages.pdf'));page.get_by_label('资料用途',exact=False).select_option('other');page.get_by_role('button',name='上传并核验原件',exact=True).click()
  for _ in range(35):
   page.wait_for_timeout(700)
   if not state['part_abort'] and any(e.get('method')=='PUT' and e.get('status')==200 for e in events):break
  page.wait_for_timeout(1200);capture(page,'17-mobile-part2-aborted');c.storage_state(path='/tmp/web05-user-auth.json');page.reload(wait_until='domcontentloaded');page.get_by_role('button',name='查询已收到的传输状态',exact=True).click();page.get_by_text('已收到片号：1',exact=False).wait_for();capture(page,'18-mobile-refresh-only-part1');state['missing_body']=page.locator('body').inner_text();save();page.locator('input[type=file]').nth(1).set_input_files(str(ROOT/'fixtures/user05-large-three-pages.pdf'))
  for _ in range(70):
   page.wait_for_timeout(700)
   if state['ack_committed']:break
  page.wait_for_timeout(500);capture(page,'19-complete-real-ack-lost');state['ack_loss_body']=page.locator('body').inner_text();save();c.storage_state(path='/tmp/web05-user-auth.json');page.reload(wait_until='domcontentloaded');page.get_by_role('button',name='核对上传完成受理结果',exact=True).click();page.get_by_role('button',name='确认 1 份资料入库',exact=True).wait_for(timeout=45000);capture(page,'20-same-complete-replay-recovered');state['recovery_body']=page.locator('body').inner_text();save()
 except Exception as e:
  state['probe_exception']=type(e).__name__;capture(page,'probe-current');print('PROBE_EXCEPTION',type(e).__name__);print(page.locator('body').inner_text())
 finally:
  c.storage_state(path='/tmp/web05-user-auth.json');save();print('STATE',json.dumps({k:v for k,v in state.items() if not k.endswith('body')},ensure_ascii=False));print('PARTS',parts);print('COMPLETES',completes);page.unroute_all(behavior='ignoreErrors');b.close()
