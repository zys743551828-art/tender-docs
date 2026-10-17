import json
from playwright.sync_api import sync_playwright
from web06_user_helpers import *
f=json.load(open('/tmp/web06-user-fixture.json'));result=[]
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 for width in [1440,390]:
  c=b.new_context(viewport={'width':width,'height':844},storage_state='/tmp/web06-user-auth.json');guarded(c,[]);page=c.new_page();page.goto(f['standard_review_url'],wait_until='domcontentloaded');page.get_by_label('报告用途与格式',exact=False).wait_for();page.wait_for_timeout(300);page.screenshot(path=str(ROOT/('24-review-first-viewport-'+str(width)+'.png')));capture(page,'25-review-density-'+str(width));row={'width':width,'height':844,'document_width':page.evaluate('document.documentElement.scrollWidth'),'repeated_completed_cards':page.get_by_text('执行已结束',exact=True).count(),'completed_warning_count':page.get_by_text('执行结束不等于核心核对通过，请回到对应资料或评审查看范围和产物。',exact=True).count()}
  for name,loc in [('next_action',page.get_by_role('button',name='查看风险与本轮处理',exact=True)),('report_format',page.get_by_label('报告用途与格式',exact=False)),('operations_heading',page.get_by_role('heading',name='处理记录',exact=True))]:
   row[name]=loc.bounding_box()if loc.count()==1 else {'count':loc.count()}
  result.append(row);c.close()
 (ROOT/'review-density-result.json').write_text(json.dumps({'at':stamp(),'observations':result},ensure_ascii=False,indent=2));print(result);b.close()
