import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from web05_user_helpers import *
f=json.loads(Path('/tmp/web05-user-fixture.json').read_text());events=[];result={}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1100},storage_state='/tmp/web05-user-auth-knowledge.json');guarded(c,events);page=c.new_page();page.set_default_timeout(20000)
 try:
  page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_role('button',name='完善并追加候选版本',exact=True).click();page.get_by_role('checkbox',name='施工方法',exact=True).check();page.get_by_role('checkbox',name='项目辅助查证',exact=True).check();page.get_by_role('checkbox',name=f['company_name'],exact=True).check();page.get_by_label('明确地区范围',exact=True).fill('浙江');page.get_by_label('明确工程类型',exact=True).fill('市政');page.get_by_label('知识有效性方式',exact=True).select_option('no_expiry_confirmed');page.get_by_label('特殊条件与范围说明',exact=True).fill('只作合成施工方法辅助参考，须结合当前现场条件核实；不作为已确认事实。');page.get_by_label('期限核实依据',exact=True).fill('合成测试施工方法，仅在本次测试环境经人工核实，不宣称真实规范效力。');page.get_by_role('checkbox',name='明确不设额外事实前提',exact=True).check();
  print('SOURCE_BUTTONS',page.get_by_role('checkbox',name='使用此真实出处',exact=True).count());page.get_by_role('checkbox',name='使用此真实出处',exact=True).nth(2).check();print('AFTER_ONE',page.get_by_role('checkbox',name='使用此真实出处',exact=True).count());page.get_by_role('checkbox',name='使用此真实出处',exact=True).nth(1).check();capture(page,'30-authoritative-sources-selected');page.get_by_role('button',name='保存候选新版本',exact=True).click();page.wait_for_timeout(1000);capture(page,'31-candidate-v2-saved');print(page.locator('body').inner_text());result['body']=page.locator('body').inner_text()
 except Exception as e:result['probe_exception']=type(e).__name__;capture(page,'knowledge-version-probe-current');print('EXCEPTION',str(e));print(page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'knowledge-version-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/web05-user-auth-knowledge.json');print({k:v for k,v in result.items() if k not in ['events','body','dialog_body']});b.close()
