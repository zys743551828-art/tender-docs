import json,hashlib
from pathlib import Path
from reportlab.pdfgen import canvas
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));events=[];result={};f['company_name']='用户07合成市政主体-'+f['uuid'];root=ROOT/'fixtures';root.mkdir(exist_ok=True);pdf=root/'user07-independent-evidence.pdf';cv=canvas.Canvas(str(pdf));
for i,line in enumerate(['Synthetic municipal evidence USER07 '+f['uuid'],'Keep pedestrian crossings open during staged municipal construction.','Provide lighting and noise barriers for night works.']):cv.drawString(35,780-i*28,line)
cv.showPage();cv.save();result['pdf_sha']=hashlib.sha256(pdf.read_bytes()).hexdigest()
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(18000)
 try:
  page.goto(BASE,wait_until='domcontentloaded');page.get_by_role('button',name='＋ 新建投标',exact=True).click();page.get_by_label('项目名称',exact=True).fill('用户07独立知识与业务结果-'+f['uuid']);page.get_by_role('button',name='明确登记新的真实主体',exact=True).click();page.get_by_label('真实公司完整名称',exact=True).fill(f['company_name']);page.get_by_label('登记依据与理由',exact=True).fill('独立用户角色浏览器测试的合成主体，不代表真实企业。');page.get_by_role('button',name='登记并选用此主体',exact=True).click();f['company_id']=page.get_by_label('本次选择的投标主体',exact=False).input_value();page.get_by_role('checkbox',name='关联工程、标段或轮次（可选）',exact=True).check();page.get_by_label('新工程地区',exact=True).fill('浙江');page.get_by_label('新工程类型',exact=True).fill('市政');page.get_by_role('button',name='创建并上传资料',exact=True).click();page.wait_for_url('**/bids/**');f['own_url']=page.url;f['own_bid_id']=page.url.split('/')[4];Path('/tmp/web07-user-fixture.json').write_text(json.dumps(f,ensure_ascii=False));page.get_by_role('link',name='项目设置',exact=True).click();sw=page.get_by_role('switch').first;sw.wait_for();
  if sw.is_checked():sw.uncheck();page.get_by_role('button',name='保存当前项目设置',exact=True).click();page.wait_for_timeout(500)
  capture(page,'02-own-identity-auto-review-off');page.get_by_role('link',name='项目资料',exact=True).click();page.locator('input[type=file]').first.set_input_files(str(pdf));page.get_by_label('资料用途',exact=False).select_option('body');page.get_by_role('button',name='上传并核验原件',exact=True).click();page.get_by_role('button',name='确认 1 份资料入库',exact=True).wait_for(timeout=45000);page.get_by_role('button',name='确认 1 份资料入库',exact=True).click();page.get_by_text('已入库资料 1',exact=False).wait_for();capture(page,'03-own-source-committed');print('OWN_PROJECT',f['own_bid_id'])
 except Exception as e:result['exception']=type(e).__name__;capture(page,'own-project-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events,'fixtures':f});(ROOT/'own-project-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/web07-user-auth.json');b.close()
