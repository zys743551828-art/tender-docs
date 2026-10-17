import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
from current_user_helpers import *
events=[];r={'started_at':stamp(),'scope':'download_filename_and_mobile_hint_delta_only','project_id':PROJECT}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json',accept_downloads=True);guarded(c,events);page=c.new_page();page.set_default_timeout(12000);page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');s=page.get_by_role('combobox',name='打开已有正文草稿',exact=True);s.wait_for()
 if not page.locator('[contenteditable=\"true\"]').count():
  o=s.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;o.wait_for(state='attached');s.select_option(o.get_attribute('value'))
 page.get_by_role('button',name='下载投标正文（Word）',exact=True).wait_for(timeout=20000)
 for label,ext in [('下载投标正文（Word）','docx'),('下载投标正文（PDF）','pdf')]:
  with page.expect_download() as d:page.get_by_role('button',name=label,exact=True).click()
  download=d.value;dest=ROOT/f'delta-named-snapshot3.{ext}';download.save_as(str(dest));sha=hashlib.sha256(dest.read_bytes()).hexdigest();old=hashlib.sha256((ROOT/f'local-built-snapshot3.{ext}').read_bytes()).hexdigest();r.setdefault('downloads',[]).append({'button':label,'suggested_filename':download.suggested_filename,'sha256':sha,'identical_to_prior_snapshot':sha==old})
 page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(300);capture(page,'31-mobile-nav-hint-delta');r['mobile_swipe_hint_visible']=page.get_by_text('左右滑动可查看全部工作面',exact=False).count()>0;r['mobile_dimensions']=page.evaluate('({viewport:innerWidth,width:document.documentElement.scrollWidth})');r['requests']=events;r['ended_at']=stamp();(ROOT/'download-name-and-nav-delta.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='requests'},ensure_ascii=False));b.close()
