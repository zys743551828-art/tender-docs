import json,hashlib
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from web06_user_helpers import *
f=json.load(open('/tmp/web06-user-fixture.json'));events=[];result={'exports':[]}
def find_op(x):
 if isinstance(x,dict):
  for k in ['operation_id']:
   if isinstance(x.get(k),str):return x[k]
  for v in x.values():
   a=find_op(v)
   if a:return a
 if isinstance(x,list):
  for v in x:
   a=find_op(v)
   if a:return a
 return None
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web06-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(20000)
 try:
  page.goto(f['standard_review_url'],wait_until='domcontentloaded');sel=page.get_by_label('报告用途与格式',exact=False);sel.wait_for()
  for label,filename in [('评审报告（Word）','user06-review-report.docx'),('评审报告（PDF）','user06-review-report.pdf'),('批注审阅稿（PDF）','user06-annotated-review.pdf')]:
   sel.select_option(label=label)
   with page.expect_response(lambda r:r.request.method=='POST' and urlparse(r.url).path.endswith('/report-exports'))as info:page.get_by_role('button',name='生成此用途副本',exact=True).click()
   res=info.value;ack=res.json();opid=find_op(ack);row={'purpose':label,'accepted_status':res.status,'operation_id':opid,'ack_keys':list(ack)};print('ACK',row,flush=True)
   if opid:
    for _ in range(45):
     rr=c.request.get(BASE+'/api/v1/operations/'+opid);op=rr.json();row['operation_keys']=list(op);row['terminal_status']=op.get('status',op.get('state'));row['operation_kind']=op.get('kind')
     if row['terminal_status']in ['succeeded','failed','cancelled','completed']:break
     page.wait_for_timeout(500)
   page.wait_for_timeout(600);capture(page,'report-final-'+filename.replace('.','-'));button=page.get_by_role('button',name='下载'+label,exact=True).first
   with page.expect_download(timeout=30000)as info:button.click()
   d=info.value;target=ROOT/'fixtures'/filename;d.save_as(str(target));row.update({'filename':d.suggested_filename,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size});result['exports'].append(row);(ROOT/'report-exports-final-result.json').write_text(json.dumps({'at':stamp(),**result,'events':events},ensure_ascii=False,indent=2));print('DOWNLOADED',row,flush=True)
  page.get_by_role('button',name='查看本轮受检原件',exact=True).focus();page.keyboard.press('Enter');page.get_by_role('dialog').wait_for();page.locator('canvas').wait_for();capture(page,'29-controlled-original-viewer-mobile')
  with page.expect_download()as info:page.get_by_role('button',name='下载资料原件',exact=True).click()
  d=info.value;target=ROOT/'fixtures/controlled-clean-original.pdf';d.save_as(str(target));result['clean_original']={'name':d.suggested_filename,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size};page.keyboard.press('Escape');result['focus_after_escape']=page.locator(':focus').inner_text()
 except Exception as e:result['exception']=type(e).__name__;capture(page,'report-final-probe-current');print('ERROR',type(e).__name__,page.locator('body').inner_text(),flush=True)
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'report-exports-final-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));page.unroute_all(behavior='ignoreErrors');b.close()
