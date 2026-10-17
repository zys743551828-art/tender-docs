"""只复验已收口的直接取件、手机入口和历史版本三项整改。"""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

OUT=Path(__file__).parent/'pdf-slice-review'
BASE=json.loads((OUT/'result.json').read_text())
BID=BASE['project_id']
FILES=[Path('/workspace/tender-workbench')/x for x in [
 'apps/web/src/App.tsx','apps/web/src/Project.tsx','apps/web/src/Evidence.tsx',
 'apps/web/src/PdfPreview.tsx','apps/web/src/styles.css','apps/web/src/api.ts',
 'apps/api/main.py','apps/api/views.py']]
def hashes():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in FILES}
result={'scope':'只复验三项P2；其他稳定基础路径不重跑','project_id':BID,'checks':[],'source_hashes_before':hashes()}
def check(name,ok,**detail):
 result['checks'].append({'name':name,'passed':ok,**detail})
 (OUT/'delta-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
async def main():
 access=json.loads(Path('/workspace/tender-workbench/.local/dev-access.json').read_text())
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  context=await browser.new_context(viewport={'width':390,'height':844},accept_downloads=True)
  page=await context.new_page();page.set_default_timeout(20000)
  try:
   await page.goto('http://127.0.0.1:5173',wait_until='networkidle')
   await page.get_by_label('账号',exact=True).fill(access.get('login_name') or access.get('username') or access.get('account'))
   await page.get_by_label('密码',exact=True).fill(access['password'])
   await page.get_by_role('button',name='登录工作区').click()
   await page.get_by_role('heading',name='投标项目',exact=True).wait_for()
  except Exception:
   result['status']='login_unavailable';result['reason']='登录未到已认证项目列表，细节隐藏以保护凭据'
   await browser.close();return
  try:
   await page.goto(f'http://127.0.0.1:5173/bids/{BID}/materials',wait_until='networkidle')
   response=await page.request.get(f'http://127.0.0.1:5173/api/v1/bids/{BID}/materials');materials=await response.json()
   current=next(d for d in materials['documents'] if d['filename']=='product-body-v2.pdf')
   original_id=next(d['id'] for d in BASE['documents'] if d['filename']=='product-body.pdf')
   response=await page.request.get(f'http://127.0.0.1:5173/api/v1/documents/{original_id}')
   check('historical_document_read_projection',response.status==200,status=response.status)
   if response.status!=200:raise RuntimeError('Historical DocumentView endpoint is not ready')
   old=await response.json()
   check('same_logical_file_distinct_version_and_head',old['file_id']==current['file_id'] and old['id']!=current['id'] and current['previous_version_id']==old['id'] and old['file_head_version_id']==current['id'] and current['file_revision']>old['file_revision'],v1_id=old['id'],v2_id=current['id'])
   actions=page.get_by_role('button',name='查看原件与依据',exact=True)
   await actions.first.wait_for()
   boxes=[await actions.nth(i).bounding_box() for i in range(await actions.count())]
   sizes=await page.evaluate('({viewport:innerWidth,page:document.documentElement.scrollWidth})')
   check('mobile_original_actions_visible_without_horizontal_scroll',bool(boxes) and all(b and b['x']>=0 and b['x']+b['width']<=390 for b in boxes) and sizes['page']<=390,boxes=boxes,sizes=sizes)
   await page.screenshot(path=str(OUT/'10-fixed-mobile-materials.png'),full_page=True)
   await actions.first.click()
   await page.get_by_role('dialog').get_by_role('heading',name=current['filename'],exact=True).wait_for()
   async with page.expect_download() as info:
    await page.get_by_role('button',name='下载资料原件',exact=True).click()
   dl=await info.value;saved=OUT/'v2-direct-download.pdf';await dl.save_as(str(saved))
   check('v2_direct_download_original_filename_and_bytes',dl.suggested_filename==current['filename'] and hashlib.sha256(saved.read_bytes()).hexdigest()==current['sha256']==hashlib.sha256((OUT/'product-body-v2.pdf').read_bytes()).hexdigest(),filename=dl.suggested_filename,sha256=hashlib.sha256(saved.read_bytes()).hexdigest())
   await page.get_by_role('button',name='关闭原件',exact=True).click()
   await page.get_by_role('button',name='查看历史版本',exact=True).click()
   history=page.get_by_role('region',name='文件历史版本',exact=True)
   await history.get_by_text(old['filename'],exact=True).wait_for()
   check('v1_discoverable_from_current_file',True)
   await page.screenshot(path=str(OUT/'11-history-discoverable-mobile.png'),full_page=True)
   await history.get_by_role('button',name='查看此历史原件与依据',exact=True).first.click()
   dialog=page.get_by_role('dialog')
   await dialog.get_by_role('heading',name=old['filename'],exact=True).wait_for()
   await dialog.get_by_text('历史版本',exact=False).wait_for()
   await page.get_by_role('img',name=old['filename']+'，物理页1原页',exact=True).wait_for(state='visible')
   check('v1_original_page_identity_is_historical',True,document_id=old['id'],filename=old['filename'])
   await page.screenshot(path=str(OUT/'12-v1-original-mobile.png'),full_page=True)
   async with page.expect_download() as info:
    await page.get_by_role('button',name='下载资料原件',exact=True).click()
   dl=await info.value;saved=OUT/'v1-direct-download.pdf';await dl.save_as(str(saved))
   check('v1_download_keeps_v1_bytes_and_original_name',dl.suggested_filename==old['filename'] and hashlib.sha256(saved.read_bytes()).hexdigest()==old['sha256']==hashlib.sha256((OUT/'product-body.pdf').read_bytes()).hexdigest(),filename=dl.suggested_filename,sha256=hashlib.sha256(saved.read_bytes()).hexdigest())
   await page.keyboard.press('Escape')
   await page.set_viewport_size({'width':1440,'height':1000})
   await page.screenshot(path=str(OUT/'13-history-desktop.png'),full_page=True)
   result['status']='completed'
  except Exception as exc:
   result['status']='interrupted';result['reason']=re.sub(r'https?://\S+','[URL]',str(exc).split('Call log:')[0])[:400]
   result['error_type']=type(exc).__name__
   await page.screenshot(path=str(OUT/'delta-interrupted.png'),full_page=True)
  finally:
   result['source_hashes_after']=hashes()
   check('reviewed_sources_stable_during_delta',result['source_hashes_after']==result['source_hashes_before'])
   (OUT/'delta-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
   await browser.close()
 print(json.dumps({'status':result.get('status'),'checks':len(result['checks']),'failed':[x['name'] for x in result['checks'] if not x['passed']]},ensure_ascii=False))
asyncio.run(main())
