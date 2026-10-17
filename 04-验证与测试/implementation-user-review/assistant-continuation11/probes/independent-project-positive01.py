import datetime,hashlib,json,sys,time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
root=Path('/workspace/tender-workbench');out=Path('/workspace/tender-docs/04-验证与测试/implementation-user-review/assistant-continuation11');origin='http://127.0.0.1:5184';bid='c0de9e01-8d96-42e2-b1d2-1016e5a2d589';facility=Path('/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/facility-candidate02/runtime');sys.path.insert(0,str(facility));from safety import MARKER,read_control
CONTROL=root/'.local/assistant_ui01-control.json';original=read_control(CONTROL);assert original['mode']=='hold';members=json.loads((root/'.local/assistant_ui01-test-members.json').read_text());eventpath=Path('/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/fixture05/worker-events.jsonl');evidence=out/'independent-project-positive01-result.json';assert not evidence.exists(),'one-shot original evidence must be retained';question='请用两句话说明：我如何先查证原文，再决定是否采纳修改建议？这是独立用户界面机制复验，不进行正式评分，不执行任何业务动作。';checks=[];writes=[];errors=[];stage='preflight';control_changed=False;mock_before=eventpath.read_text().splitlines();context_bids=[];unresolved=[];jobid=None;turnposts=[];blocked=[];preflight_jobs=[]
def control(value):
 temporary=CONTROL.with_suffix('.independent-user11-temporary');temporary.write_text(json.dumps(value));temporary.chmod(0o600);temporary.replace(CONTROL)
def record_result():
 entries=[json.loads(line) for line in eventpath.read_text().splitlines()[len(mock_before):]];safe=[{k:v for k,v in x.items() if k in ['event','mode','real_provider_attempts','job_id','scope','transport']} for x in entries];result={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stage':stage,'project_id':bid,'job_id':jobid,'checks':checks,'preflight_project_count':len(set(context_bids)),'preflight_project_jobs':preflight_jobs,'unresolved_project_jobs':unresolved,'turn_posts':turnposts,'writes':writes,'worker_events_delta':safe,'new_mock_requests':sum(x.get('event')=='mock_request' for x in safe),'control_final_mode':read_control(CONTROL)['mode'],'control_final_equal_original':json.loads(CONTROL.read_text())==original,'blocked_external_browser':blocked,'page_errors':errors,'real_provider_calls':0,'cloud_calls':0,'kafka_delivery':'not executed; direct original handler fixture','provider_quality':'explicit static synthetic answer only; no real model evaluation','control_writes':2 if control_changed else 0};evidence.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);contexts=[]
 def login(index):
  c=browser.new_context(viewport={'width':1440,'height':1000});contexts.append(c);p=c.new_page();p.set_default_timeout(15000)
  def route(r):
   parsed=urlsplit(r.request.url)
   if parsed.hostname not in ['127.0.0.1','localhost']:blocked.append({'host':parsed.hostname});r.abort();return
   if parsed.path.endswith('/turns') and r.request.method=='POST':
    turnposts.append({'path':parsed.path,'method':'POST'})
    if len(turnposts)>1:raise RuntimeError('more than one turn refused')
   r.continue_()
  c.route('**/*',route)
  def response(r):
   path=urlsplit(r.url).path
   if '/api/' in path and r.request.method not in ['GET','HEAD'] and '/auth/' not in path:writes.append({'path':path,'method':r.request.method,'status':r.status})
  p.on('response',response);p.on('pageerror',lambda e:errors.append(type(e).__name__));p.goto(origin+'/projects');p.get_by_label('账号',exact=True).fill(members[index]['login']);p.get_by_label('密码',exact=True).fill(members[index]['password']);p.get_by_role('button',name='登录工作区',exact=True).click();p.get_by_role('button',name='＋ 新建投标',exact=True).wait_for();return c,p
 try:
  # The Worker scans project jobs of both registered members. Inspect all their visible projects,
  # not only the chosen project, before allowing the project-only Mock control window.
  for member in [0,1]:
   c,p=login(member);cursor=None
   while True:
    response=p.request.get(origin+'/api/v1/bids?limit=100'+('&cursor='+cursor if cursor else ''));assert response.status==200;page=response.json()
    for item in page['items']:
     identity=item['id'];context_bids.append(identity);conversations=p.request.get(origin+f'/api/v1/assistant/conversations?scope=project&bid_id={identity}');assert conversations.status==200
     for conversation in conversations.json()['items']:
      jobs=p.request.get(origin+f'/api/v1/assistant/jobs?scope=project&bid_id={identity}&conversation_id={conversation["id"]}&limit=100');assert jobs.status==200;items=jobs.json()['items'];assert len(items)<100,'incomplete job page; stop rather than guess'
      for job in items:
       state=job['operation']['state'];entry={'id':job['id'],'bid_id':identity,'state':state,'requires_billing_reconciliation':job['requires_billing_reconciliation']};preflight_jobs.append(entry)
       if state in ['queued','running','cancel_requested'] or job['requires_billing_reconciliation']:unresolved.append(entry)
    cursor=page.get('next_cursor')
    if not cursor:break
   if member==0:c.close()
  assert not unresolved,'unresolved project job; do not enable fixture'
  settings=p.request.get(origin+f'/api/v1/bids/{bid}/settings');assert settings.status==200 and settings.json()['auto_review'] is False
  main=p.request.get(origin+f'/api/v1/bids/{bid}/conversations/main');assert main.status==200 and main.json()['state']=='active';conversation_id=main.json()['id'];before_messages=p.request.get(origin+f'/api/v1/bids/{bid}/conversations/{conversation_id}/messages?limit=50').json()['items'];assert not any(any(part.get('type')=='text' and part.get('text')==question for part in x['parts']) for x in before_messages),'question already exists: do not retry'
  checks.append({'case':'both-members-all-project-conversations-no-queued-running-cancel-unknown-auto-review-off','passed':True})
  stage='project-composer-ready';p.goto(origin+f'/assistant?bid={bid}');p.get_by_label('本次提问',exact=True).wait_for();p.wait_for_function("document.activeElement?.getAttribute('aria-label')==='本次提问'");p.get_by_label('本次提问',exact=True).fill(question);p.get_by_role('button',name='已核对会话，按当前修订继续',exact=True).click();send=p.get_by_role('button',name='发送给助手',exact=True);p.wait_for_function("!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='发送给助手')?.disabled");assert not send.is_disabled();assert p.get_by_label('本消息已选来源',exact=True).inner_text()==''
  # Exact authorized control delta; preserve marker and restore original hold in finally.
  control({**original,'mode':'answer','scopes':['project']});control_changed=True;stage='single-new-turn'
  with p.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/turns')) as accepted:send.click()
  response=accepted.value;assert response.status==202,'non-202 result: keep receipt, restore hold, no retry';acceptedjob=response.json();jobid=acceptedjob['id'];assert acceptedjob['scope']=='project' and acceptedjob['bid_id']==bid;deadline=time.monotonic()+30;published=None
  while time.monotonic()<deadline:
   result=p.request.get(origin+'/api/v1/assistant/jobs/'+jobid);assert result.status==200;published=result.json()
   if published.get('publication') and published['publication']['state']=='published':break
   if published['operation']['state'] in ['failed','cancelled'] or published['requires_billing_reconciliation']:raise RuntimeError('incomplete/unknown job; preserve receipt and stop')
   p.wait_for_timeout(250)
  assert published and published['operation']['state']=='succeeded' and published['publication']['state']=='published','publication did not finish; do not retry'
  control(original);stage='published-hold-restored';assert read_control(CONTROL)['mode']=='hold';assert published['conversation_id']==conversation_id
  answer='显式离线合成机制回答：请核对原文。本回答用于界面验收，不代表真实模型效果或正式评审。';p.get_by_text(question,exact=True).wait_for();p.get_by_text(answer,exact=True).last.wait_for();p.screenshot(path=str(out/'positive-project-published-desktop.png'),full_page=False);p.reload();p.get_by_label('本次提问',exact=True).wait_for();p.get_by_text(question,exact=True).wait_for();p.get_by_text(answer,exact=True).last.wait_for();after_messages=p.request.get(origin+f'/api/v1/bids/{bid}/conversations/{conversation_id}/messages?limit=50').json()['items'];assert len(after_messages)==len(before_messages)+2;assert sum(any(part.get('type')=='text' and part.get('text')==question for part in x['parts']) for x in after_messages)==1;assert p.get_by_label('本次提问',exact=True).input_value()==''
  p.set_viewport_size({'width':390,'height':844});p.get_by_role('button',name='回到最新／继续提问',exact=True).click();box=p.get_by_label('本次提问',exact=True).bounding_box();assert box['y']>=0 and box['y']+box['height']<=844;assert p.evaluate('document.documentElement.scrollWidth<=innerWidth');p.screenshot(path=str(out/'positive-project-refresh-mobile.png'),full_page=False);checks.append({'case':'one-UI-Chinese-project-question-one-published-Mock-answer-refresh-same-conversation','passed':True,'message_count_delta':2,'user_question_count':1,'published_state':published['publication']['state'],'mobile_input_rect':box});assert len(turnposts)==1 and not errors and not blocked;stage='completed'
 except Exception as e:
  print(json.dumps({'stage':stage,'error':type(e).__name__,'turn_posts':len(turnposts)},ensure_ascii=False));
  if 'p' in locals():p.screenshot(path=str(out/('positive-'+stage+'-failure.png')),full_page=True)
  raise
 finally:
  if control_changed:control(original)
  r=record_result();browser.close();print(json.dumps({'stage':stage,'checks':len(checks),'turn_posts':len(turnposts),'new_mock_requests':r['new_mock_requests'],'control':r['control_final_mode']},ensure_ascii=False))
if stage=='completed':assert r['new_mock_requests']==1 and all(x.get('job_id')==jobid for x in r['worker_events_delta'] if x.get('job_id'))
