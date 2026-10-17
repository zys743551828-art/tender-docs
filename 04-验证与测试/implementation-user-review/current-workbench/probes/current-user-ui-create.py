import json,os
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/current-workbench');responses=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000},storage_state='/tmp/current-user-auth.json');page=c.new_page()
 page.on('response',lambda r:responses.append({'method':r.request.method,'path':r.url.split('5175')[-1].split('?')[0],'status':r.status}) if '/api/' in r.url else None)
 page.goto('http://127.0.0.1:5175');page.get_by_role('button',name='＋ 新建投标').wait_for();page.get_by_role('button',name='＋ 新建投标').click();page.get_by_label('项目名称',exact=True).fill('用户早期走查-独立人工章节-01');page.get_by_role('button',name='创建并上传资料').click();page.wait_for_timeout(2000)
 page.screenshot(path=str(out/'02-create-attempt.png'),full_page=True);print(json.dumps({'url':page.url,'responses':responses,'body':page.locator('body').inner_text()},ensure_ascii=False));(out/'initial-route-observations.json').write_text(json.dumps({'url':page.url,'responses':responses,'body':page.locator('body').inner_text()},ensure_ascii=False,indent=2)+'\n');Path('/tmp/current-user-project.json').write_text(json.dumps({'url':page.url}));c.storage_state(path='/tmp/current-user-auth.json');os.chmod('/tmp/current-user-auth.json',0o600);b.close()
