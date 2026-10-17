import json
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(18000)
 try:
  page.goto(f['trace_url'],wait_until='domcontentloaded');page.get_by_role('button',name='登记为本项目参考',exact=True).nth(2).wait_for();capture(page,'27-fixed-trace-restored-mobile');page.get_by_role('button',name='登记为本项目参考',exact=True).nth(2).click();page.get_by_label('本次用途与适用理由',exact=True).fill('独立合成主体浙江市政在办项目，引用行人通行方法供查证，不自动确认事实或解除风险。');before=c.request.get(BASE+'/api/v1/bids/'+f['target_bid_id']+'/facts');result['facts_before_sha']=__import__('hashlib').sha256(before.body()).hexdigest();page.get_by_role('button',name='保存项目参考',exact=True).click();page.wait_for_timeout(800);capture(page,'30-rag-unit-citation-saved');after=c.request.get(BASE+'/api/v1/bids/'+f['target_bid_id']+'/facts');result['facts_after_sha']=__import__('hashlib').sha256(after.body()).hexdigest();result['facts_unchanged']=result['facts_before_sha']==result['facts_after_sha'];result['body']=page.locator('body').inner_text();print(result['body']);print('LINKS',page.get_by_role('link').evaluate_all('(es)=>es.map(x=>({text:x.innerText,href:x.getAttribute("href")}))'));print('BUTTONS',page.get_by_role('button').all_text_contents());
 except Exception as e:result['exception']=type(e).__name__;capture(page,'rag-citation-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'rag-unit-citation-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
