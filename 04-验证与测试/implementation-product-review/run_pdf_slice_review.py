"""产品角色实际浏览器走查；只写独立合成项目，不输出凭据或授权URL。"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
import fitz
from playwright.async_api import async_playwright

OUT = Path(__file__).parent / 'pdf-slice-review'
OUT.mkdir(exist_ok=True)
RESUME = '--resume' in sys.argv
previous = json.loads((OUT/'result.json').read_text()) if RESUME else None
TITLE = previous['project_title'] if previous else '产品实走-资料切片-' + uuid.uuid4().hex[:8]
checks = []
state = {'scope':'T0+PDF资料入库切片，非完整A1或业务P0验收','project_title':TITLE,'checks':checks,'software_execution':'实际浏览器＋真实API；未调用模型评分','secrets_exported':False}

def record(name, passed, **details):
    checks.append({'name':name,'passed':passed,**details})
    (OUT/'result.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')

def pdf_fixture(name, pages):
    p=OUT/name
    doc=fitz.open()
    for text in pages:
        page=doc.new_page(width=595,height=842)
        if text: page.insert_text((56,80),text,fontsize=14)
    doc.save(p); doc.close()
    return p

async def main():
    if RESUME:
        tender=OUT/'product-tender.pdf';body=OUT/'product-body.pdf';word=OUT/'unsupported.docx'
        state['project_id']=previous['project_id']
        state['continuation_of']='result-initial-interruption.json'
    else:
        tender=pdf_fixture('product-tender.pdf',['Synthetic Tender / Product Review\nContract duration: 180 days.\nRequirement: Traffic diversion plan is required.'])
        body=pdf_fixture('product-body.pdf',['Synthetic Body V1\nDuration: 240 days.\nTraffic diversion draft.', '', 'Synthetic Body Appendix\nEnd of synthetic document.'])
        word=OUT/'unsupported.docx';word.write_bytes(b'SYNTHETIC UNSUPPORTED FORMAT')
    state['fixtures']=[{'filename':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [tender,body,word]]
    access=json.loads(Path('/workspace/tender-workbench/.local/dev-access.json').read_text())
    login=access.get('login_name') or access.get('username') or access.get('account')
    password=access.get('password')
    if not login or not password:
        record('access_configuration',False,reason='Provided local credential shape does not expose expected login fields');return
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        page=await context.new_page();page.set_default_timeout(20000)
        try:
            await page.goto('http://127.0.0.1:5173',wait_until='networkidle')
            await page.get_by_label('账号',exact=True).fill(login)
            await page.get_by_label('密码',exact=True).fill(password)
            await page.get_by_role('button',name='登录工作区').click()
            await page.get_by_role('heading',name='投标项目',exact=True).wait_for()
            record('real_login',True)
        except Exception:
            record('real_login',False,reason='Login flow did not reach authenticated project list; details omitted to protect credentials')
            await browser.close();return
        try:
            if not RESUME:
                await page.get_by_role('button',name='＋ 新建投标').click()
                await page.get_by_label('项目名称',exact=True).fill(TITLE)
                await page.get_by_role('button',name='创建并上传资料').click()
                await page.get_by_role('heading',name=TITLE,exact=True).wait_for()
                state['project_id']=re.search(r'/bids/([^/]+)',page.url).group(1)
                record('create_minimal_project',True,required_fields=['项目名称'])
                await page.screenshot(path=str(OUT/'01-empty-project.png'),full_page=True)
                await page.locator('#project-files').set_input_files([str(tender),str(body),str(word)])
                await page.get_by_label('product-body.pdf的资料用途',exact=True).select_option('body')
                await page.get_by_role('button',name='上传并核验原件').click()
                await page.get_by_text('当前仅支持PDF，请从Word导出PDF后上传。',exact=True).wait_for()
                await page.get_by_text('我确认仅将已核验的',exact=False).wait_for(timeout=45000)
                record('supported_files_survive_unsupported_peer',True,verified_expected=2,unsupported='DOCX诚实拒绝，未宣称转换')
                await page.screenshot(path=str(OUT/'02-partial-batch.png'),full_page=True)
                await page.get_by_role('checkbox').check()
                await page.get_by_role('button',name='确认 2 份资料入库',exact=True).click()
                await page.get_by_role('heading',name='本批资料已入库',exact=True).wait_for()
            else:
                await page.goto(f'http://127.0.0.1:5173/bids/{state["project_id"]}/materials',wait_until='networkidle')
            for _ in range(20):
                response=await page.request.get(f'http://127.0.0.1:5173/api/v1/bids/{state["project_id"]}/materials')
                mat=await response.json()
                if len(mat.get('documents',[]))>=2 and all(x.get('parse_coverage')!='pending' for x in mat['documents']): break
                await page.wait_for_timeout(1000)
            state['documents']=[{k:d.get(k) for k in ['id','file_id','filename','role','file_revision','parse_coverage']} for d in mat.get('documents',[])]
            record('batch_committed_and_real_parse_observed',len(state['documents'])==2 and all(d['parse_coverage']!='pending' for d in state['documents']),documents=state['documents'])
            await page.reload(wait_until='networkidle')
            await page.get_by_role('heading',name=TITLE,exact=True).wait_for()
            await page.get_by_text('当前尚未形成风险结论或可提交判断。',exact=False).wait_for()
            record('refresh_recovery_and_no_false_review',True)
            await page.screenshot(path=str(OUT/'03-materials-restored.png'),full_page=True)
            row=page.get_by_role('row').filter(has=page.get_by_text('product-body.pdf',exact=True))
            await row.get_by_role('button',name='查看原件与依据').click()
            await page.get_by_role('dialog').wait_for()
            await page.get_by_role('heading',name='解析文本',exact=True).wait_for()
            await page.wait_for_timeout(2000)
            canvas=page.get_by_role('img',name='product-body.pdf，物理页1原页')
            ready=await canvas.evaluate('(el)=>!el.hidden && el.width>0 && el.height>0')
            record('pdf_original_canvas_rendered',ready)
            await page.screenshot(path=str(OUT/'04-evidence-page1.png'),full_page=True)
            select=page.get_by_role('dialog').get_by_role('combobox')
            opts=await select.locator('option').all_text_contents()
            record('all_physical_pages_selectable',opts==['1','2','3'],options=opts)
            await select.select_option('2');await page.wait_for_timeout(500)
            await page.screenshot(path=str(OUT/'05-evidence-blank-page2.png'),full_page=True)
            record('blank_physical_page_kept',await page.get_by_role('img',name='product-body.pdf，物理页2原页').is_visible())
            href=await page.get_by_role('link',name='下载资料原件').get_attribute('href')
            response=await context.request.get(href)
            raw=await response.body()
            saved=OUT/'downloaded-original.pdf';saved.write_bytes(raw)
            record('original_link_returns_original_bytes',response.status==200 and hashlib.sha256(raw).hexdigest()==hashlib.sha256(body.read_bytes()).hexdigest(),content_type=response.headers.get('content-type'),content_disposition=response.headers.get('content-disposition'))
            async with context.expect_page() as popup_info:
                await page.get_by_role('link',name='下载资料原件').click()
            popup=await popup_info.value
            record('original_link_opens_new_tab',True,download_event_on_origin_page=False,meaning='原件字节可取；点击目标为新标签，非本页附件下载事件')
            await popup.close()
            await page.get_by_role('button',name='关闭原件').click()
            await page.set_viewport_size({'width':390,'height':844})
            await page.screenshot(path=str(OUT/'06-materials-mobile.png'),full_page=True)
            sizes=await page.evaluate('({w:innerWidth,scroll:document.documentElement.scrollWidth})')
            record('mobile_no_page_overflow',sizes['scroll']<=sizes['w'],sizes=sizes)
            await row.get_by_role('button',name='查看原件与依据').click()
            await page.wait_for_timeout(1200)
            await page.screenshot(path=str(OUT/'07-evidence-mobile.png'),full_page=True)
            record('mobile_evidence_accessible',await page.get_by_role('button',name='关闭原件').is_visible())
            await page.keyboard.press('Escape')
            record('escape_returns_from_evidence',await page.get_by_role('dialog').count()==0)
            state['status']='completed_for_slice'
        except Exception as exc:
            state['status']='partial_review_interrupted'
            record('review_interruption',False,error_type=type(exc).__name__,reason=str(exc).split('Call log:')[0][:400])
            await page.screenshot(path=str(OUT/'interrupted.png'),full_page=True)
        finally:
            (OUT/'result.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
            await browser.close()
    print(json.dumps({'project_id':state.get('project_id'),'status':state.get('status'),'checks':len(checks),'failed':[x['name'] for x in checks if not x['passed']]},ensure_ascii=False))

asyncio.run(main())
