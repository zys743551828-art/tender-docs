import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from current_user_helpers import *
events=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(12000);page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');select=page.get_by_role('combobox').first;select.wait_for()
 if not page.locator('[contenteditable="true"]').count():
  option=select.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;option.wait_for(state='attached');select.select_option(option.get_attribute('value'))
 page.locator('[contenteditable="true"]').wait_for();summary=page.locator('summary').filter(has_text='本章要求与来源');
 if summary.get_attribute('aria-expanded')!='true' and not summary.evaluate('(e)=>e.parentElement.open'):summary.click()
 select=page.get_by_label('已入库来源文件',exact=False);select.wait_for();option=select.locator('option').filter(has_text='用户合成-交通导改依据.pdf').first;option.wait_for(state='attached');select.select_option(option.get_attribute('value'));page.wait_for_timeout(350);capture(page,'17b-source-before-association');page.get_by_role('button',name='核验并关联此原页',exact=True).nth(1).click();page.wait_for_timeout(700);capture(page,'18-source-associated');r={'at':stamp(),'body':page.locator('body').inner_text(),'requests':events};(ROOT/'source-associated.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(r['body']);b.close()
