import json,hashlib
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from web05_user_helpers import *
f=json.loads(Path('/tmp/web05-user-fixture.json').read_text());events=[];result={}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web05-user-auth-knowledge.json');guarded(c,events);page=c.new_page();page.set_default_timeout(15000)
 def facts():
  res=c.request.get(BASE+'/api/v1/bids/'+f['target_url'].split('/')[4]+'/facts');return {'status':res.status,'sha256':hashlib.sha256(res.body()).hexdigest(),'body':res.json()}
 try:
  page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_label('知识目标项目',exact=True).select_option(f['region_mismatch_url'].split('/')[4]);page.get_by_role('button',name='查找知识',exact=True).click();page.get_by_label('本次使用的实际摘录',exact=True).fill('Maintain pedestrian access during staged municipal works.');page.get_by_label('为何适用于此项目与用途',exact=True).fill('合成测试：同主体但地区不一致，验证服务器如实拒绝，不应登记有效参考。');page.get_by_role('button',name='登记本次项目参考',exact=True).click();page.wait_for_timeout(700);capture(page,'37-region-rejected-mobile');result['region_rejected_body']=page.locator('body').inner_text();print('REGION_REJECTED\n'+result['region_rejected_body']);
  page.get_by_label('知识目标项目',exact=True).select_option(f['target_url'].split('/')[4]);page.get_by_role('button',name='查找知识',exact=True).click();page.get_by_label('本次使用的实际摘录',exact=True).fill('Maintain pedestrian access during staged municipal works.');page.get_by_label('为何适用于此项目与用途',exact=True).fill('同一合成投标主体、浙江市政，仅作施工方法查证参考，不作为工程事实。');result['facts_before']=facts();page.get_by_role('button',name='登记本次项目参考',exact=True).click();page.wait_for_timeout(700);capture(page,'38-reference-registered-mobile');result['facts_after']=facts();result['facts_unchanged']=result['facts_before']==result['facts_after'];result['registered_body']=page.locator('body').inner_text();print('REGISTERED\n'+result['registered_body']);print('BUTTONS',page.get_by_role('button').all_text_contents())
 except Exception as e:result['exception']=type(e).__name__;capture(page,'reference-use-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events,'url':page.url});(ROOT/'knowledge-reference-use-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/web05-user-auth-knowledge.json');b.close()
