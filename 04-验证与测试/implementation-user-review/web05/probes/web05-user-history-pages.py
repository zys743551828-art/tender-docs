import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
from web05_user_helpers import *
f=json.loads(Path('/tmp/web05-user-fixture.json').read_text());events=[];result={}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web05-user-auth-knowledge.json');guarded(c,events);page=c.new_page();page.set_default_timeout(20000)
 try:
  page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_role('button',name='查看当前版本影响',exact=True).click();page.wait_for_timeout(700);capture(page,'42-current-version-impact-history-discovery');result['impact_body']=page.locator('body').inner_text();print('IMPACT\n'+result['impact_body']);page.get_by_role('button',name='追溯此项目当时引用',exact=True).first.click();page.wait_for_timeout(700);capture(page,'43-withdrawn-historical-citation');result['citation_body']=page.locator('body').inner_text();print('HISTORY\n'+result['citation_body']);page.get_by_role('button',name='核对当时引用原页（2）',exact=True).focus();page.keyboard.press('Enter');page.get_by_role('button',name='打开物理页 1 · 文件版本 1',exact=True).first.focus();page.keyboard.press('Enter');page.get_by_role('dialog').wait_for();page.locator('canvas').wait_for();page.wait_for_timeout(1000);capture(page,'44-mobile-historical-source-canvas');result['dialog_body']=page.get_by_role('dialog').inner_text();result['dialog_selects']=page.get_by_role('dialog').locator('select').evaluate_all('(els)=>els.map(x=>({aria:x.getAttribute("aria-label"),html:x.outerHTML}))');result['dialog_width']=page.get_by_role('dialog').bounding_box();result['viewport_width']=390;result['document_scroll_width']=page.evaluate('document.documentElement.scrollWidth');print('SELECTS',result['dialog_selects']);physical=[]
  for num in ['2','3']:
   page.get_by_role('dialog').locator('select').select_option(num);page.wait_for_timeout(1200);page.screenshot(path=str(ROOT/('46-mobile-physical-page-'+num+'.png')));physical.append({'selected':page.get_by_role('dialog').locator('select').input_value(),'canvas_count':page.locator('canvas').count(),'dialog_text':page.get_by_role('dialog').inner_text()})
  result['physical_pages']=physical;focus=[]
  for _ in range(12):
   page.keyboard.press('Tab');focus.append(page.locator(':focus').evaluate('(x)=>({text:x.textContent,tag:x.tagName,inDialog:!!x.closest("[role=dialog]")})'))
  result['tab_focus']=focus;page.keyboard.press('Escape');result['focus_after_escape']=page.locator(':focus').inner_text();result['dialog_closed']=page.get_by_role('dialog').count()==0;capture(page,'45-mobile-source-escape');print('MOBILE',result['dialog_width'],result['document_scroll_width'],result['dialog_closed'],result['focus_after_escape'])
 except Exception as e:result['exception']=type(e).__name__;capture(page,'history-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'knowledge-history-physical-pages-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/web05-user-auth-knowledge.json');b.close()
