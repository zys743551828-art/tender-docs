"""只在产品实走自己的合成项目验证新版本与历史身份。"""
import asyncio
import hashlib
import json
from pathlib import Path
import fitz
from playwright.async_api import async_playwright

OUT=Path(__file__).parent/'pdf-slice-review'
base=json.loads((OUT/'result.json').read_text())
project=base['project_id']
checks=[]
async def main():
    fixture=OUT/'product-body-v2.pdf'
    if not fixture.exists():
        doc=fitz.open()
        for text in ['Synthetic Body V2\nDuration corrected to 180 days.','', 'Synthetic appendix V2']:
            page=doc.new_page()
            if text:page.insert_text((56,80),text,fontsize=14)
        doc.save(fixture);doc.close()
    access=json.loads(Path('/workspace/tender-workbench/.local/dev-access.json').read_text())
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1000})
        page=await context.new_page();page.set_default_timeout(25000)
        try:
            await page.goto('http://127.0.0.1:5173',wait_until='networkidle')
            await page.get_by_label('账号',exact=True).fill(access.get('login_name') or access.get('username') or access.get('account'))
            await page.get_by_label('密码',exact=True).fill(access['password'])
            await page.get_by_role('button',name='登录工作区').click()
            await page.get_by_role('heading',name='投标项目',exact=True).wait_for()
        except Exception:
            (OUT/'version-result.json').write_text(json.dumps({'status':'login_unavailable','details_redacted':True},ensure_ascii=False));await browser.close();return
        try:
            await page.goto(f'http://127.0.0.1:5173/bids/{project}/materials',wait_until='networkidle')
            response=await page.request.get(f'http://127.0.0.1:5173/api/v1/bids/{project}/materials');before=await response.json()
            old=next(x for x in before['documents'] if x['filename']=='product-body.pdf')
            if not any(x['filename']=='product-body-v2.pdf' for x in before['documents']):
                await page.locator('#project-files').set_input_files(str(fixture))
                await page.get_by_label('product-body-v2.pdf的资料用途',exact=True).select_option('body')
                await page.get_by_label('product-body-v2.pdf的文件身份',exact=True).select_option(old['file_id'])
                await page.get_by_role('button',name='上传并核验原件').click()
                await page.get_by_role('button',name='确认 1 份资料入库',exact=True).wait_for(timeout=45000)
                await page.get_by_role('button',name='确认 1 份资料入库',exact=True).click()
                await page.get_by_role('heading',name='本批资料已入库',exact=True).wait_for()
            for _ in range(25):
                response=await page.request.get(f'http://127.0.0.1:5173/api/v1/bids/{project}/materials');after=await response.json()
                docs=after['documents']
                if any(x['filename']=='product-body-v2.pdf' and x['parse_coverage']!='pending' for x in docs):break
                await page.wait_for_timeout(1000)
            first=next(x for x in docs if x['filename']=='product-body.pdf')
            second=next(x for x in docs if x['filename']=='product-body-v2.pdf')
            checks.append({'name':'new_version_keeps_logical_file_and_old_identity','passed':first['id']!=second['id'] and first['file_id']==second['file_id'] and second['file_revision']>first['file_revision']})
            checks.append({'name':'head_matches_v2_and_v1_is_history','passed':first['file_head_version_id']==second['id'] and second['file_head_version_id']==second['id']})
            await page.reload(wait_until='networkidle')
            await page.get_by_text('product-body-v2.pdf',exact=True).wait_for()
            await page.screenshot(path=str(OUT/'08-version-history.png'),full_page=True)
            row=page.get_by_role('row').filter(has=page.get_by_text('product-body.pdf',exact=True))
            await row.get_by_role('button',name='查看原件与依据').click()
            await page.get_by_role('dialog').get_by_text('投标正文 · 版本 1 · 历史版本',exact=False).wait_for()
            checks.append({'name':'old_evidence_remains_exact_historical_version','passed':True})
            await page.screenshot(path=str(OUT/'09-old-version-evidence.png'),full_page=True)
            safe=[{k:x.get(k) for k in ['id','file_id','filename','file_revision','file_head_version_id','parse_coverage']} for x in docs]
            result={'project_id':project,'checks':checks,'documents':safe,'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'status':'completed','scope':'文件版本与真实解析；未评审风险或预测技术分'}
        except Exception as exc:
            result={'project_id':project,'checks':checks,'status':'interrupted','error_type':type(exc).__name__,'reason':str(exc).split('Call log:')[0][:300]}
        (OUT/'version-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        await browser.close()
    print(json.dumps({'status':result['status'],'checks':len(checks),'failed':[x['name'] for x in checks if not x['passed']]},ensure_ascii=False))
asyncio.run(main())
