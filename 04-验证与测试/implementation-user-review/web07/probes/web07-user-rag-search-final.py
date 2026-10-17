import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1100},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(22000)
 try:
  page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_label('知识目标项目',exact=True).select_option(f['target_bid_id']);page.get_by_label('查证问题',exact=True).fill('How should night works protect pedestrian crossings?');page.get_by_role('checkbox',name='仅查证当前打开的知识条目（先从目录选择）',exact=True).check();capture(page,'14-before-explicit-rag-search');result['pre_search_calls']=sum(x.get('method')=='POST'and'knowledge-retrievals'in x.get('path','')for x in events);page.get_by_role('button',name='查证企业知识',exact=True).click();page.get_by_role('button',name='查证企业知识',exact=True).wait_for(timeout=45000);page.wait_for_timeout(300);capture(page,'20-rag-completed-results');f['trace_url']=page.url;Path('/tmp/web07-user-fixture.json').write_text(json.dumps(f,ensure_ascii=False));result['body']=page.locator('body').inner_text();result['url']=page.url;print(result['body']);print('BUTTONS',page.get_by_role('button').all_text_contents());print('FIELDS',page.locator('input,textarea,select').evaluate_all('(es)=>es.map(x=>({type:x.type,label:x.labels?.[0]?.innerText,aria:x.getAttribute("aria-label"),value:x.value}))'));print('LINKS',page.get_by_role('link').evaluate_all('(es)=>es.map(x=>({text:x.innerText,href:x.getAttribute("href")}))'))
 except Exception as e:result['exception']=type(e).__name__;capture(page,'rag-search-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'rag-completed-search-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
