import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
events=[];r={'started_at':stamp(),'scope':'list_404_repair_delta_only','project_id':PROJECT}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(12000);page.goto(f'{BASE}/projects',wait_until='domcontentloaded');own=page.get_by_text('用户早期走查-独立人工章节-01',exact=True);own.wait_for();search=page.get_by_role('searchbox');
 if search.count():search.fill('用户早期走查-独立人工章节-01')
 capture(page,'26-project-list-repaired-own-only');own.click();page.get_by_text('项目工作区',exact=True).wait_for();r['own_project_reopened']=PROJECT in page.url;r['url']=page.url;page.set_viewport_size({'width':390,'height':844});page.get_by_role('link',name='项目资料',exact=True).click();page.get_by_role('button',name='查看原件与依据').wait_for();capture(page,'27-materials-mobile-after-list-repair');r['mobile_dimensions']=page.evaluate('({viewport:innerWidth,width:document.documentElement.scrollWidth})');r['file_action_boxes']=[e.bounding_box() for e in page.get_by_role('button',name='查看原件与依据').all()];r['requests']=events;r['ended_at']=stamp();(ROOT/'list-repair-delta.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps(r,ensure_ascii=False));b.close()
