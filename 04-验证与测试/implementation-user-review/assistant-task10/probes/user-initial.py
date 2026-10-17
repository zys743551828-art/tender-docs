import json
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('/workspace/tender-workbench'); out=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/assistant-task10');m=json.loads((root/'.local/assistant_ui01-test-members.json').read_text())[1]
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']); c=b.new_context(viewport={'width':1440,'height':1000}); p=c.new_page();p.goto('http://127.0.0.1:5184/projects');p.get_by_label('账号',exact=True).fill(m['login']);p.get_by_label('密码',exact=True).fill(m['password']);p.get_by_role('button',name='登录工作区',exact=True).click();p.get_by_role('button',name='＋ 新建投标',exact=True).wait_for()
 for name,url in [('assistant','/assistant'),('tasks','/bids/d62284e0-f41c-41e8-8c95-134760df3e19/tasks')]:
  p.goto('http://127.0.0.1:5184'+url);p.wait_for_timeout(900);(out/(name+'-text.txt')).write_text(p.locator('body').inner_text());p.screenshot(path=str(out/(name+'-desktop.png')),full_page=True)
 b.close()
print('initial desktop screenshots and visible text saved')
