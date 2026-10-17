import json,hashlib
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from web06_user_helpers import *
f=json.load(open('/tmp/web06-user-fixture.json'));events=[];writes=[];result={'injected':False}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web06-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(18000)
 def route(r):
  req=r.request;path=urlparse(req.url).path
  if req.method=='POST' and path.startswith('/api/v1/submissions/') and path.endswith('/corrections'):
   row={'path':path,'key':req.headers.get('idempotency-key'),'if_match':req.headers.get('if-match'),'body_sha':hashlib.sha256((req.post_data or '').encode()).hexdigest()};writes.append(row)
   if not result['injected']:
    result['injected']=True
    try:res=r.fetch(timeout=45000);row['committed_status']=res.status;result['committed']=True;r.abort('failed');return
    except Exception as e:row['probe_exception']=type(e).__name__;r.abort('failed');return
  r.fallback()
 page.route('**/*',route)
 try:
  page.goto(f['own_url'].rsplit('/',1)[0]+'/outcomes',wait_until='domcontentloaded');page.get_by_text('追加更正此实交记录',exact=True).click();page.get_by_label('真实提交时间（本地时间）',exact=True).fill('2026-10-16T12:20');page.get_by_role('button',name='使用实交稿自身作为声明附件',exact=True).click();page.get_by_label('本次追加更正理由',exact=True).fill('合成测试第二次有据更正：实际提交更正为12:20；保留所有先前记录与待确认身份，核对失响应恢复。');page.get_by_role('button',name='保存追加更正',exact=True).click()
  for _ in range(70):
   page.wait_for_timeout(500)
   if result.get('committed'):break
  page.wait_for_timeout(300);capture(page,'22-correction-real-201-ack-lost');page.reload(wait_until='domcontentloaded');page.wait_for_timeout(700);capture(page,'23-correction-real-refresh-recovery');result['refresh_body']=page.locator('body').inner_text();names=page.get_by_role('button').all_text_contents();name=next(x.strip() for x in names if x.strip().startswith('恢复原') and '结果'in x);result['restore_label']=name;page.get_by_role('button',name=name,exact=True).click();page.wait_for_timeout(800);capture(page,'19-correction-recovered-old-record-preserved');result['recovered_body']=page.locator('body').inner_text();print(result['recovered_body']);
  downloads=[]
  for label,needle in [('下载此记录的实际提交稿','body'),('下载此记录的声明附件','declaration')]:
   for i in range(page.get_by_role('button',name=label,exact=True).count()):
    with page.expect_download()as info:page.get_by_role('button',name=label,exact=True).nth(i).click()
    d=info.value;target=ROOT/'fixtures'/('submission-'+needle+'-'+str(i)+'.pdf');d.save_as(str(target));downloads.append({'label':label,'index':i,'suggested_name':d.suggested_filename,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
  result['downloads']=downloads;page.reload(wait_until='domcontentloaded');page.wait_for_timeout(500);capture(page,'20-history-after-second-refresh');result['after_second_refresh']=page.locator('body').inner_text()
 except Exception as e:result['exception']=type(e).__name__;capture(page,'correction-lostack-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events,'writes':writes});(ROOT/'correction-real-lostack-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/web06-user-auth.json');page.unroute_all(behavior='ignoreErrors');b.close()
