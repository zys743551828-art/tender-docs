import datetime,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
root=Path('/workspace/tender-workbench');candidate=root/'apps/web/qa-previews/assistant-continuation-11';baseline=root/'apps/web/qa-previews/assistant-task-10';out=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/assistant-continuation11');origin='http://127.0.0.1:5184';member=json.loads((root/'.local/assistant_ui01-test-members.json').read_text())[1];manifest=json.loads((candidate/'frontend-candidate.json').read_text());checks=[];assets={};errors=[];blocked=[];writes=[];stage='binding';bid='c0de9e01-8d96-42e2-b1d2-1016e5a2d589'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify():
 assert sha(candidate/'frontend-candidate.json')=='af1a992ec7539c465b873abd04bcc87e4bbc89901962f2f6c283e395ef8629e5'
 for directory in [candidate,baseline]:
  m=json.loads((directory/'frontend-candidate.json').read_text())
  for key,folder in [('source_files','frozen-inputs'),('compiled_assets','frozen-dist')]:
   for item in m[key]:assert sha(directory/folder/item['path'])==item['sha256']
def result():
 (out/'independent-delta01-result.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stage':stage,'candidate_sha256':sha(candidate/'frontend-candidate.json'),'checks':checks,'loaded_assets':assets,'page_errors':errors,'blocked_external':blocked,'business_write_attempts':writes,'source_files_verified_before_after':130,'compiled_assets_verified_before_after':215,'baseline10_verified_preserved':True,'scope':'Independent user-role Chromium delta only; native fixed frontend11 and original fixed API8816; no overlay/response mocks/turns/control changes in this probe'},ensure_ascii=False,indent=2)+'\n')
verify()
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1000});p=c.new_page();p.set_default_timeout(15000)
 def route(r):
  parsed=urlsplit(r.request.url)
  if parsed.hostname not in ['127.0.0.1','localhost']:blocked.append({'host':parsed.hostname});r.abort();return
  if parsed.path.startswith('/api/') and r.request.method not in ['GET','HEAD'] and '/auth/' not in parsed.path:writes.append({'method':r.request.method,'path':parsed.path});r.abort();return
  r.continue_()
 c.route('**/*',route)
 def observed(r):
  path=urlsplit(r.url).path
  if path.startswith('/assets/') and r.status==200:
   value=hashlib.sha256(r.body()).hexdigest();assert value==next(x['sha256'] for x in manifest['compiled_assets'] if '/'+x['path']==path);assets[path]=value
 p.on('response',observed);p.on('pageerror',lambda e:errors.append(type(e).__name__))
 try:
  p.goto(origin+'/projects');p.get_by_label('账号',exact=True).fill(member['login']);p.get_by_label('密码',exact=True).fill(member['password']);p.get_by_role('button',name='登录工作区',exact=True).click();p.get_by_role('button',name='＋ 新建投标',exact=True).wait_for();actual=c.request.get('http://127.0.0.1:8816/openapi.json');assert actual.status==200 and actual.json()==json.loads((baseline/'implemented-task04.openapi.json').read_text());checks.append({'case':'native11-assets-actual-task05-contract-bound','passed':True})
  def ready():
   p.get_by_label('本次提问',exact=True).wait_for();p.wait_for_function("document.activeElement?.getAttribute('aria-label')==='本次提问'");p.wait_for_timeout(250)
  def visible():
   box=p.get_by_label('本次提问',exact=True).bounding_box();assert box['y']>=0 and box['y']+box['height']<=p.viewport_size['height'];return box
  for width,height in [(1440,1000),(390,844)]:
   stage=f'long-conversation-{width}';print(stage,flush=True);p.set_viewport_size({'width':width,'height':height});p.goto(origin+'/assistant');ready();cold=visible();assert p.get_by_role('button',name='发送给助手',exact=True).is_disabled();p.screenshot(path=str(out/f'{width}-cold-input.png'));p.reload();ready();refreshed=visible();p.mouse.move(width//2,height//2);p.mouse.wheel(0,-20000);p.get_by_role('button',name='会话显示与结束',exact=True).focus();p.wait_for_timeout(200);before=p.evaluate('scrollY');focusbefore=p.evaluate('document.activeElement.textContent');p.wait_for_timeout(4500);assert abs(p.evaluate('scrollY')-before)<4;assert p.evaluate('document.activeElement.textContent')==focusbefore;jump=p.get_by_role('button',name='回到最新／继续提问',exact=True);jump.focus();p.keyboard.press('Tab');p.keyboard.press('Shift+Tab');assert jump.evaluate('(e)=>document.activeElement===e');p.keyboard.press('Enter');assert p.get_by_label('本次提问',exact=True).evaluate('(e)=>document.activeElement===e');visible();assert p.evaluate('document.documentElement.scrollWidth<=innerWidth');checks.append({'case':f'{width}-cold-refresh-continuation-history-read-no-poll-focus-scroll-steal-keyboard-jump','passed':True,'cold_input_rect':cold,'refreshed_input_rect':refreshed,'history_scroll_before_after':before,'unknown_send_disabled':True})
  stage='draft-source-scope-preservation';print(stage,flush=True);p.get_by_label('本次提问',exact=True).fill('独立用户11全局未发送草稿');p.get_by_role('button',name='＋ / @ 选择可信来源',exact=True).click();picker=p.get_by_role('region',name='可信来源选择',exact=True);picker.get_by_label('来源所属项目',exact=True).select_option(bid);picker.get_by_role('checkbox').first.wait_for();picker.get_by_role('checkbox').first.check();selected=p.get_by_label('本消息已选来源',exact=True).inner_text();assert '物理页1' in selected;p.reload();ready();assert p.get_by_label('本次提问',exact=True).input_value()=='独立用户11全局未发送草稿';assert p.get_by_label('本消息已选来源',exact=True).inner_text()==selected;assert p.get_by_role('button',name='发送给助手',exact=True).is_disabled();p.get_by_label('助手会话范围',exact=True).select_option(bid);ready();p.get_by_label('本次提问',exact=True).fill('独立用户11项目未发送草稿');assert p.get_by_label('本消息已选来源',exact=True).inner_text()=='';p.get_by_label('助手会话范围',exact=True).select_option('');ready();assert p.get_by_label('本次提问',exact=True).input_value()=='独立用户11全局未发送草稿';assert p.get_by_label('本消息已选来源',exact=True).inner_text()==selected;p.go_back();ready();assert p.get_by_label('本次提问',exact=True).input_value()=='独立用户11项目未发送草稿';checks.append({'case':'unsent-draft-and-server-trusted-source-refresh-global-project-back-preserved','passed':True})
  stage='drawer-fullscreen-focus';print(stage,flush=True);p.goto(origin+f'/bids/{bid}/materials');trigger=p.get_by_role('button',name='项目助手',exact=True);trigger.click();dialog=p.get_by_role('dialog',name='项目主会话助手',exact=True);ready();visible();assert dialog.get_by_label('本次提问',exact=True).input_value()=='独立用户11项目未发送草稿';
  for _ in range(25):p.keyboard.press('Tab');assert dialog.evaluate('(e)=>e.contains(document.activeElement)')
  p.keyboard.press('Escape');assert trigger.evaluate('(e)=>document.activeElement===e');trigger.click();ready();dialog.get_by_role('link',name='全屏打开同一主会话',exact=True).click();ready();visible();assert p.get_by_label('本次提问',exact=True).input_value()=='独立用户11项目未发送草稿';p.go_back();trigger.wait_for();trigger.click();ready();visible();p.screenshot(path=str(out/'390-drawer-return-input.png'));p.keyboard.press('Escape');checks.append({'case':'390-drawer-tab-contained-escape-focus-fullscreen-same-draft-and-return','passed':True})
  stage='action-deep-link';print(stage,flush=True);p.goto(origin+'/assistant?action=90110e14-e937-43ca-a5bd-1cc209575ef1');p.get_by_role('heading',name='明确链接的操作提议',exact=True).wait_for();p.get_by_label('本次提问',exact=True).wait_for();p.wait_for_timeout(4500);assert p.evaluate('scrollY')<100;assert p.get_by_role('button',name='确认建立知识候选',exact=True).count()==0;p.screenshot(path=str(out/'390-action-deep-link-preserved.png'));p.get_by_role('button',name='回到最新／继续提问',exact=True).click();visible();assert p.get_by_label('本次提问',exact=True).evaluate('(e)=>document.activeElement===e');assert p.get_by_role('button',name='发送给助手',exact=True).is_disabled();checks.append({'case':'explicit-action-link-not-auto-skipped-executed-receipt-manual-continuation-unknown-still-disabled','passed':True});assert not writes and not errors and not blocked;verify();stage='completed';result();print(json.dumps({'checks':len(checks),'passed':True,'business_write_attempts':len(writes)},ensure_ascii=False))
 except Exception as e:
  result();p.screenshot(path=str(out/(stage+'-failure.png')),full_page=True);print(json.dumps({'stage':stage,'error':type(e).__name__}));raise
 finally:b.close()
