import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
events=[];r={'started_at':stamp(),'project_id':PROJECT,'injection':'server real PATCH commit then browser route abort once'}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(12000);page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');select=page.get_by_role('combobox').first;select.wait_for()
 if not page.locator('[contenteditable="true"]').count():
  option=select.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;option.wait_for(state='attached');select.select_option(option.get_attribute('value'))
 editor=page.locator('[contenteditable="true"]');editor.wait_for();done=[]
 def lost(route):
  if route.request.method=='PATCH' and '/drafts/' in route.request.url and not done:
   done.append(True);response=route.fetch();r['server_commit_status']=response.status;r['server_committed_at']=stamp();route.abort('failed')
  else:route.fallback()
 page.route('**/api/v1/bids/'+PROJECT+'/drafts/*',lost);editor.click();editor.press('Control+End');editor.press('Enter');editor.type('用户走查：失响应保存后仍保留这段文字。');save=page.get_by_role('button',name='立即保存',exact=True)
 if save.is_enabled():save.click()
 page.get_by_text('暂时无法连接服务',exact=False).wait_for(timeout=18000);capture(page,'19-save-response-lost');r['failure_body']=page.locator('body').inner_text();r['retained_editor_text']='失响应保存后仍保留这段文字' in editor.inner_text()
 print(json.dumps({'at':stamp(),'failure_body':r['failure_body']},ensure_ascii=False),flush=True)
 retry=page.get_by_role('button',name=re.compile('重试保存|立即保存'))
 retry.first.click();page.wait_for_timeout(1300);capture(page,'20-save-retry-result');r['retry_body']=page.locator('body').inner_text();r['requests']=events;r['ended_at']=stamp();(ROOT/'save-response-loss.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'server_commit_status':r.get('server_commit_status'),'retained_text':r['retained_editor_text'],'retry_body':r['retry_body']},ensure_ascii=False));b.close()
