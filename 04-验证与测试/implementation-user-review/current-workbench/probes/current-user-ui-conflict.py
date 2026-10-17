import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
events=[];r={'started_at':stamp(),'project_id':PROJECT,'method':'real_two_context_save_conflict_with_delayed_local_request'}
def open_draft(page):
 page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');page.get_by_role('combobox').first.wait_for()
 if not page.locator('[contenteditable="true"]').count():
  s=page.get_by_role('combobox').first;o=s.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;o.wait_for(state='attached');s.select_option(o.get_attribute('value'))
 page.locator('[contenteditable="true"]').wait_for()
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);cs=[b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json') for _ in range(2)];pages=[]
 for c in cs:guarded(c,events);page=c.new_page();page.set_default_timeout(12000);open_draft(page);pages.append(page)
 a,bp=pages;pending=[]
 def hold(route):
  if route.request.method=='PATCH' and '/drafts/' in route.request.url:pending.append(route)
  else:route.fallback()
 bp.route('**/api/v1/bids/'+PROJECT+'/drafts/*',hold);editor=bp.locator('[contenteditable="true"]');editor.click();editor.press('Control+End');editor.press('Enter');editor.type('用户走查本地B：待合并内容。');save=bp.get_by_role('button',name='立即保存',exact=True)
 if save.is_enabled():save.click()
 for _ in range(30):
  if pending:break
  bp.wait_for_timeout(100)
 assert pending,'expected own local PATCH to be delayed'
 editor=a.locator('[contenteditable="true"]');editor.click();editor.press('Control+End');editor.press('Enter');editor.type('用户走查远端A：另一窗口已保存内容。');save=a.get_by_role('button',name='立即保存',exact=True)
 if save.is_enabled():save.click()
 expect(a.get_by_text(re.compile('已保存.*草稿修订 6'))).to_be_visible(timeout=15000);r['remote_saved_at']=stamp();pending.pop(0).continue_();bp.get_by_text('REVISION_CONFLICT',exact=False).first.wait_for(timeout=15000);capture(bp,'21-real-412-conflict');r['conflict_body']=bp.locator('body').inner_text();r['local_text_retained']='用户走查本地B' in bp.locator('[contenteditable="true"]').inner_text();r['requests']=events;r['ended_at']=stamp();(ROOT/'save-real-412.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(r['conflict_body']);b.close()
