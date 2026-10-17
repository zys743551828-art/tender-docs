import json,hashlib,zipfile,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from current_user_helpers import *
events=[];r={'started_at':stamp(),'scope':'late_local_build_download_and_return_delta','project_id':PROJECT}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json',accept_downloads=True);guarded(c,events);page=c.new_page();page.set_default_timeout(14000);page.goto(f'{BASE}/bids/{PROJECT}/authoring',wait_until='domcontentloaded');s=page.get_by_role('combobox',name='打开已有正文草稿',exact=True);s.wait_for()
 if not page.locator('[contenteditable="true"]').count():
  o=s.locator('option').filter(has_text='用户人工草稿-道路保通-01').first;o.wait_for(state='attached');s.select_option(o.get_attribute('value'))
 page.locator('[contenteditable="true"]').wait_for();before=page.locator('[contenteditable="true"]').inner_text();page.get_by_role('link',name='项目概览',exact=True).click();page.get_by_text('当前主要下一步',exact=True).wait_for();page.get_by_role('link',name='章节编制',exact=True).click();page.wait_for_timeout(650);r['return_auto_restored_editor']=page.locator('[contenteditable="true"]').count()>0
 if not r['return_auto_restored_editor']:
  s=page.get_by_role('combobox',name='打开已有正文草稿',exact=True);s.select_option(label=next(t for t in s.locator('option').all_text_contents() if '用户人工草稿-道路保通-01' in t))
 page.locator('[contenteditable="true"]').wait_for();r['return_kept_text']=page.locator('[contenteditable="true"]').inner_text()==before;page.reload(wait_until='domcontentloaded');page.get_by_role('combobox',name='打开已有正文草稿',exact=True).wait_for();page.wait_for_timeout(700);r['refresh_auto_restored_editor']=page.locator('[contenteditable="true"]').count()>0
 for label,ext in [('下载投标正文（Word）','docx'),('下载投标正文（PDF）','pdf')]:
  button=page.get_by_role('button',name=label,exact=True);button.wait_for(timeout=20000)
  with page.expect_download() as d:button.click()
  download=d.value;dest=ROOT/f'local-built-snapshot3.{ext}';download.save_as(str(dest));r.setdefault('downloaded',[]).append({'button':label,'filename':download.suggested_filename,'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
 capture(page,'28-late-authoring-restored-and-build-downloads');r['current_body']=page.locator('body').inner_text();r['requests']=events;r['ended_at']=stamp();(ROOT/'late-return-and-download-delta.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ['requests','current_body']},ensure_ascii=False));b.close()
with zipfile.ZipFile(ROOT/'local-built-snapshot3.docx') as z:
 t=z.read('word/document.xml').decode();print('word_snapshot_markers',{'contains_manual_patch':'并设置临时提示' in t,'contains_later_response_loss_note':'失响应' in t,'contains_later_remote_note':'远端A' in t})
