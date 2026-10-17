import json
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(18000)
 try:
  page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_role('button',name='查看知识版本历史',exact=True).click();page.get_by_role('button',name='版本 1 · 待完善候选',exact=True).click();page.wait_for_timeout(500);capture(page,'40-own-historical-knowledge-v1');result['old_url']=page.url;result['old_body']=page.locator('body').inner_text();page.reload(wait_until='domcontentloaded');page.wait_for_timeout(500);capture(page,'41-historical-knowledge-v1-refresh');result['refresh_body']=page.locator('body').inner_text();print('URL',result['old_url']);print(result['refresh_body'])
 except Exception as e:result['exception']=type(e).__name__;capture(page,'old-version-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'knowledge-old-version-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
