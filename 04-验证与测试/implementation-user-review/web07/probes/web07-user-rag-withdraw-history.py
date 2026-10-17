import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
from web07_user_helpers import *
f=json.load(open('/tmp/web07-user-fixture.json'));f['citation_url']=f['trace_url']+'&citation=922efae1-b9a2-4209-9dcd-fe5af4cd994d';Path('/tmp/web07-user-fixture.json').write_text(json.dumps(f,ensure_ascii=False));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/web07-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(24000)
 try:
  page.goto(f['citation_url'],wait_until='domcontentloaded');page.get_by_role('button',name='追溯当时批准来源原页（1）',exact=True).wait_for();capture(page,'31-fixed-citation-restored-before-stop');result['before_body']=page.locator('body').inner_text();page.goto(f['knowledge_url'],wait_until='domcontentloaded');page.get_by_label('维护依据与原因',exact=True).fill('独立合成用户测试：停止此自有知识版本的新用，保留本次固定查证、引用与原件历史。');page.get_by_role('button',name='停用当前发布版本',exact=True).click();page.get_by_text('已停用',exact=True).wait_for();capture(page,'32-own-knowledge-withdrawn');page.goto(f['citation_url'],wait_until='domcontentloaded');page.get_by_role('button',name='追溯当时批准来源原页（1）',exact=True).wait_for();capture(page,'33-fixed-trace-citation-after-stop');result['after_body']=page.locator('body').inner_text();buttons=page.get_by_role('button',name='登记为本项目参考',exact=True);result['new_citation_disabled']=[buttons.nth(i).is_disabled()for i in range(buttons.count())];result['old_quote_preserved']='Keep pedestrian crossings open during staged municipal construction.'in result['before_body']and'Keep pedestrian crossings open during staged municipal construction.'in result['after_body'];page.get_by_role('button',name='追溯当时批准来源原页（1）',exact=True).focus();page.keyboard.press('Enter');page.get_by_role('button',name='打开物理页 1 · 文件版本 1',exact=True).last.focus();page.keyboard.press('Enter');page.get_by_role('dialog').wait_for();page.locator('canvas').wait_for();page.wait_for_timeout(900);capture(page,'34-historical-rag-source-canvas-mobile');result['dialog_body']=page.get_by_role('dialog').inner_text();result['document_width']=page.evaluate('document.documentElement.scrollWidth');focus=[]
  for _ in range(10):
   page.keyboard.press('Tab');focus.append(page.locator(':focus').evaluate('(x)=>!!x.closest("[role=dialog]")'))
  result['tab_in_dialog']=focus
  with page.expect_download()as dl:page.get_by_role('button',name='下载资料原件',exact=True).click()
  d=dl.value;out=ROOT/'fixtures/rag-historical-original.pdf';d.save_as(str(out));result['original_sha']=hashlib.sha256(out.read_bytes()).hexdigest();page.keyboard.press('Escape');result['focus_after_escape']=page.locator(':focus').inner_text();print('NEW_DISABLED',result['new_citation_disabled'],'QUOTE_KEPT',result['old_quote_preserved'],'SHA',result['original_sha']);print('STOPPED_BODY\n'+result['after_body'])
 except Exception as e:result['exception']=type(e).__name__;capture(page,'rag-withdraw-probe-current');print(type(e).__name__,page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'rag-withdraw-history-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));b.close()
