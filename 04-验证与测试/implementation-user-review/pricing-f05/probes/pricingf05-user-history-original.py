import json,hashlib
from playwright.sync_api import sync_playwright
from pricingf05_user_helpers import *
f=json.load(open('/tmp/pricingf05-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/pricingf05-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(22000)
 try:
  page.goto(f['pricing_url'],wait_until='domcontentloaded');buttons=page.get_by_role('button',name='回看此固定情景方案',exact=True);buttons.first.wait_for();buttons.first.focus();page.keyboard.press('Enter');page.wait_for_timeout(500);capture(page,'17-old-planA-fixed-readonly');result['old_plan_body']=page.locator('body').inner_text();print('OLD\n'+result['old_plan_body']);print('BUTTONS',page.get_by_role('button').all_text_contents());refs=page.get_by_role('button',name='核对本次公式原页（6）',exact=True)
  if refs.count()==0:refs=page.get_by_role('button',name='查看已确认公式全部原页（6）',exact=True)
  if refs.count()==0:refs=page.get_by_role('button').filter(has_text='原页（6）')
  refs.last.focus();page.keyboard.press('Enter');page.get_by_role('button',name='打开物理页 1 · 文件版本 1',exact=True).first.focus();page.keyboard.press('Enter');page.get_by_role('dialog').wait_for();page.locator('canvas').wait_for();page.wait_for_timeout(800);capture(page,'18-legal-pricing-source-real-canvas');result['dialog_body']=page.get_by_role('dialog').inner_text()
  with page.expect_download()as info:page.get_by_role('button',name='下载资料原件',exact=True).click()
  d=info.value;out=ROOT/'fixtures/downloaded-original-ratio.pdf';d.save_as(str(out));result['download_sha']=hashlib.sha256(out.read_bytes()).hexdigest();result['original_sha']=hashlib.sha256((ROOT/'fixtures/original-ratio.pdf').read_bytes()).hexdigest();result['download_matches']=result['download_sha']==result['original_sha'];page.keyboard.press('Escape');result['focus_after_escape']=page.locator(':focus').inner_text();print('ORIGINAL',result['download_matches'],result['download_sha'],result['focus_after_escape'])
 except Exception as e:result['exception']=str(e);capture(page,'history-original-probe-current');print(str(e));print(page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'history-original-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
