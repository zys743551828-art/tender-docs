import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from web06_user_helpers import *
f=json.load(open('/tmp/web06-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web06-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(16000)
 try:
  page.goto(BASE,wait_until='domcontentloaded');page.get_by_label('切换工作区',exact=True).select_option('knowledge');page.get_by_role('button',name='建立知识候选',exact=True).click();page.get_by_label('候选标题',exact=True).fill('用户06就地缺项提示-'+f['uuid']);page.get_by_label('知识正文',exact=True).fill('Synthetic actual submitted document 3157b22e-5');page.get_by_label('拟适用范围说明',exact=True).fill('独立用户测试候选，尚待完整范围维护，不发布。');page.get_by_label('候选来源项目',exact=True).select_option(f['own_bid_id']);page.get_by_role('checkbox',name='user06-actual-body.pdf · 版本 1',exact=True).check();page.get_by_role('button',name='保存待完善候选',exact=True).click();page.get_by_role('button',name='完善并追加候选版本',exact=True).click();page.get_by_text('保存前还需填写或核对',exact=False).wait_for();page.get_by_text('保存前还需填写或核对',exact=False).scroll_into_view_if_needed();capture(page,'33-d-missing-hint-mobile');result['hint_body']=page.locator('body').inner_text();result['has_scope_note']=('特殊条件与范围说明' in result['hint_body']);result['candidate_url']=page.url;print(result['hint_body'][-4500:])
 except Exception as e:result['exception']=type(e).__name__;capture(page,'d-hint-probe-current');print(type(e).__name__,page.locator('body').inner_text());print('checkboxes',page.get_by_role('checkbox').evaluate_all('(es)=>es.map(x=>x.labels?.[0]?.innerText)'))
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'d-missing-hint-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
