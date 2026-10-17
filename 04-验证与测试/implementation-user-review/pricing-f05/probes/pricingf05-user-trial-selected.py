import json
from playwright.sync_api import sync_playwright,expect
from pricingf05_user_helpers import *
f=json.load(open('/tmp/pricingf05-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/pricingf05-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(20000)
 try:
  page.goto(f['pricing_url'],wait_until='domcontentloaded');sel=page.get_by_label('选择已确认公式',exact=False);sel.locator('option').filter(has_text=f['uuid']).wait_for(state='attached');f['formula_id']=sel.locator('option').filter(has_text=f['uuid']).get_attribute('value');sel.select_option(f['formula_id']);__import__('pathlib').Path('/tmp/pricingf05-user-fixture.json').write_text(json.dumps(f,ensure_ascii=False));page.get_by_label('本方候选报价',exact=True).fill('100');page.get_by_label('对手报价假设（每行一个，最多200项）',exact=True).fill('80\n120');page.get_by_label('补充本方候选（每行一个，最多50项）',exact=True).fill('90\n110');page.get_by_role('button',name='试算当前情景',exact=True).click();page.wait_for_timeout(500);expect(page.get_by_role('button',name='试算当前情景',exact=True)).to_be_enabled();capture(page,'07-trial-not-saved');result['body']=page.locator('body').inner_text();print(result['body']);print('FIELDS',page.locator('input,select,textarea').evaluate_all('(es)=>es.map(x=>({type:x.type,label:x.labels?.[0]?.innerText,aria:x.getAttribute("aria-label"),value:x.value}))'));print('BUTTONS',page.get_by_role('button').all_text_contents());
 except Exception as e:result['exception']=str(e);capture(page,'trial-probe-current');print(str(e));print(page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'trial-explicit-formula-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/pricingf05-user-auth.json');b.close()
