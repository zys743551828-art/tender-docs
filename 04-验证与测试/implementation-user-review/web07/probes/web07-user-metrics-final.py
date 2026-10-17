import json
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(16000)
 try:
  page.goto(BASE,wait_until='domcontentloaded');page.get_by_label('切换工作区',exact=True).select_option('metrics');page.get_by_text('限定项目（已选0个，最多20个）',exact=True).click();page.wait_for_timeout(300);page.get_by_role('checkbox',name='用户07独立知识与业务结果-'+f['uuid'],exact=True).check();page.get_by_role('button',name='更新统计范围',exact=True).click();page.wait_for_timeout(800);capture(page,'35-metrics-own-final-ledger');result['ledger_body']=page.locator('body').inner_text();page.get_by_label('统计口径',exact=True).select_option('verified_only');page.get_by_role('button',name='更新统计范围',exact=True).click();page.wait_for_timeout(700);capture(page,'36-metrics-own-final-verified-only');result['verified_body']=page.locator('body').inner_text();print('LEDGER\n'+result['ledger_body']);print('VERIFIED\n'+result['verified_body']);
 except Exception as e:result['exception']=type(e).__name__;capture(page,'metrics-own-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'metrics-own-final-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
