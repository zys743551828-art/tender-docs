import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
def choose_own_draft(page,tag):
 page.get_by_role('combobox').first.wait_for()
 if page.locator('[contenteditable=\"true\"]').count()==0:
  capture(page,tag)
  option=page.get_by_role('combobox').first.locator('option').filter(has_text='用户人工草稿-道路保通-01').first
  option.wait_for(state='attached')
  page.get_by_role('combobox').first.select_option(option.get_attribute('value'))
 page.locator('[contenteditable=\"true\"]').wait_for()
events=[];result={'started_at':stamp(),'project_id':PROJECT,'method':'actual_browser_early_walkthrough'}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(12000)
 page.goto(f'{BASE}/bids/{PROJECT}/settings');page.get_by_role('switch').first.wait_for();first=page.get_by_role('switch').first
 if first.is_checked():first.uncheck();page.get_by_role('button',name='保存当前项目设置').click();page.wait_for_timeout(600)
 result['own_auto_review_disabled']=not first.is_checked();assert result['own_auto_review_disabled']
 capture(page,'07-settings-auto-confirmed-off')
 page.get_by_role('link',name='章节编制',exact=True).click();choose_own_draft(page,'08a-return-reselect-draft');page.get_by_label('章节名称',exact=True).fill('交通导改与保通')
 editor=page.locator('[contenteditable="true"]');editor.fill('本章为用户走查合成内容。\n施工期间保留沿街出入口通行。\n分阶段围挡与夜间照明由现场确认。')
 save=page.get_by_role('button',name='立即保存',exact=True)
 if save.is_enabled():save.click()
 expect(page.get_by_text(re.compile(r'已保存.*章节修订 [2-9]'))).to_be_visible(timeout=15000)
 capture(page,'08-chapter-saved');result['saved_body']=page.locator('body').inner_text();page.reload();choose_own_draft(page,'09a-refresh-reselect-draft');expect(page.locator('[contenteditable="true"]')).to_contain_text('施工期间保留沿街出入口通行')
 result['refresh_kept_saved_text']=True;capture(page,'09-chapter-refreshed')
 page.get_by_text('本章要求与来源',exact=False).first.click();page.wait_for_timeout(250);capture(page,'10-source-details-empty')
 page.get_by_text('手动段落修改预览',exact=True).click();page.wait_for_timeout(300);capture(page,'11-manual-patch-open')
 result['patch_body']=page.locator('body').inner_text();result['patch_controls']=page.locator('input,textarea,button').evaluate_all('(es)=>es.map(e=>({tag:e.tagName,role:e.getAttribute("role"),text:(e.innerText||e.getAttribute("aria-label")||"").slice(0,140),placeholder:e.getAttribute("placeholder")}))');result['requests']=events;result['ended_at']=stamp();(ROOT/'editing-initial.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['saved_body','requests']},ensure_ascii=False));b.close()
