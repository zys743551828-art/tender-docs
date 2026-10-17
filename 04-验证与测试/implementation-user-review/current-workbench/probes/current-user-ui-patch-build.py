import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
events=[];result={'started_at':stamp(),'project_id':PROJECT}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(12000);page.goto(f'{BASE}/bids/{PROJECT}/authoring');select=page.get_by_role('combobox').first;select.wait_for();option=select.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;option.wait_for(state='attached');select.select_option(option.get_attribute('value'));editor=page.locator('[contenteditable="true"]');editor.wait_for();editor.locator('p').filter(has_text='施工期间保留沿街出入口通行').click();page.get_by_text('手动段落修改预览',exact=True).click();page.get_by_label('替换后的段落文字',exact=True).fill('施工期间保留沿街出入口通行，并设置临时提示。');page.get_by_role('button',name='预览这次段落替换',exact=True).click();page.wait_for_timeout(900);capture(page,'12b-manual-patch-preview-before-apply');page.get_by_role('button',name='应用这次修改并保存',exact=True).click();expect(page.locator('[contenteditable=\"true\"]')).to_contain_text('并设置临时提示');capture(page,'13-manual-patch-applied');result['after_apply']=page.locator('body').inner_text();
 for box in page.get_by_role('checkbox').all():
  if not box.is_checked():box.check()
 page.get_by_role('button',name='保存确认后生成文件',exact=True).click();page.wait_for_timeout(1200);capture(page,'14-build-accepted');page.wait_for_timeout(9000);capture(page,'15-build-status');result['body']=page.locator('body').inner_text();result['requests']=events;result['ended_at']=stamp();(ROOT/'patch-and-build.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(result['body']);b.close()
