import json
from playwright.sync_api import sync_playwright
from current_user_helpers import *
events=[];r={'started_at':stamp(),'project_id':PROJECT}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(10000);page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');s=page.get_by_role('combobox',name='打开已有正文草稿',exact=True);s.wait_for()
 if not page.locator('[contenteditable="true"]').count():
  o=s.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;o.wait_for(state='attached');s.select_option(o.get_attribute('value'))
 page.locator('[contenteditable="true"]').wait_for();page.get_by_text('查看章节历史修订',exact=True).click();page.wait_for_timeout(600);capture(page,'29-chapter-history');r['history_body']=page.locator('body').inner_text();page.get_by_role('button',name='查看本次快照正文',exact=True).wait_for();page.get_by_role('button',name='查看本次快照正文',exact=True).click();page.wait_for_timeout(500);capture(page,'30-fixed-snapshot-body');r['snapshot_body']=page.locator('body').inner_text();r['requests']=events;r['ended_at']=stamp();(ROOT/'chapter-and-snapshot-history.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(r['snapshot_body']);b.close()
