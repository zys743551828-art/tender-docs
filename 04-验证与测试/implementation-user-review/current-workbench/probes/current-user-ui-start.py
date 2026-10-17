import json,os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
out=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/current-workbench')
members=json.loads(Path('/workspace/tender-workbench/.local/external-test-members.json').read_text());member=next(x for x in members if x['login']=='external-qa-2dda5041')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 c=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
 page=c.new_page();page.goto('http://127.0.0.1:5175',wait_until='domcontentloaded');page.get_by_label('账号',exact=True).fill(member['login']);page.get_by_label('密码',exact=True).fill(member['password']);page.get_by_role('button',name='登录工作区').click();page.get_by_role('button',name='＋ 新建投标').wait_for(timeout=20000)
 page.screenshot(path=str(out/'01-projects-desktop.png'),full_page=True)
 c.storage_state(path='/tmp/current-user-auth.json');os.chmod('/tmp/current-user-auth.json',0o600)
 (out/'01-projects-visible.txt').write_text(page.locator('body').inner_text())
 print(page.locator('body').inner_text()[:6500]);browser.close()
