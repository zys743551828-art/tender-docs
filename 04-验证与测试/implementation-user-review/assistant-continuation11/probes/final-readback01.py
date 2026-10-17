import datetime,hashlib,json,subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path('/workspace/tender-workbench');docs=Path('/workspace/tender-docs/04-验证与测试');out=docs/'implementation-user-review/assistant-continuation11';origin='http://127.0.0.1:5184';members=json.loads((root/'.local/assistant_ui01-test-members.json').read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();verified=[]
for path,root_key,file_key,folder in [(docs/'task-drafts-development-checks/candidate05/candidate.json','runtime_root','sources',None),(docs/'assistant-browser-environment01/facility-candidate02/candidate.json','runtime_root','sources',None)]:
 m=json.loads(path.read_text());runtime=Path(m[root_key])
 for x in m[file_key]:assert sha(runtime/x['path'])==x['sha256']
 verified.append({'manifest':str(path),'sha256':sha(path),'source_count':len(m[file_key])})
for name in ['assistant-continuation-11','assistant-task-10']:
 base=root/'apps/web/qa-previews'/name;m=json.loads((base/'frontend-candidate.json').read_text())
 for key,d in [('source_files','frozen-inputs'),('compiled_assets','frozen-dist')]:
  for x in m[key]:assert sha(base/d/x['path'])==x['sha256']
 verified.append({'candidate':name,'manifest_sha256':sha(base/'frontend-candidate.json'),'sources':len(m['source_files']),'assets':len(m['compiled_assets'])})
unknown=[];model=[];positive=json.loads((out/'independent-project-positive03-result.json').read_text())
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for index in [0,1]:
  c=b.new_context();p=c.new_page();p.goto(origin+'/projects');p.get_by_label('账号',exact=True).fill(members[index]['login']);p.get_by_label('密码',exact=True).fill(members[index]['password']);p.get_by_role('button',name='登录工作区',exact=True).click();p.get_by_role('button',name='＋ 新建投标',exact=True).wait_for();main=p.request.get(origin+'/api/v1/assistant/global-conversations/main');assert main.status==200;globalid=main.json()['id'];jobs=p.request.get(origin+f'/api/v1/assistant/jobs?scope=global&conversation_id={globalid}&limit=100');assert jobs.status==200
  items=jobs.json()['items'];unresolved=[x for x in items if x['requires_billing_reconciliation']];assert unresolved and all(x['model_call']['state']=='unknown' and not x['automatic_retry_authorized'] for x in unresolved)
  unknown.append({'member_index':index,'global_conversation_id':globalid,'unresolved_jobs':[{'id':x['id'],'operation_state':x['operation']['state'],'model_call':x['model_call'],'requires_billing_reconciliation':True,'automatic_retry_authorized':False} for x in unresolved]})
  p.goto(origin+'/assistant');p.get_by_label('本次提问',exact=True).wait_for();assert p.get_by_role('button',name='发送给助手',exact=True).is_disabled()
  if index==0:
   old=p.request.get(origin+'/api/v1/assistant/jobs/8171d331-92a6-4cf2-8c46-3fc5fe5e9c59');assert old.status==200 and old.json()['model_call']['state']=='succeeded';model.append({'kind':'retained-old-project-Mock-call','job_id':old.json()['id'],'model_call':old.json()['model_call']})
  if index==1:
   new=p.request.get(origin+'/api/v1/assistant/jobs/'+positive['job_id']);assert new.status==200 and new.json()['model_call']['state']=='succeeded' and new.json()['publication']['state']=='published';model.append({'kind':'new-single-project-Mock-call','job_id':new.json()['id'],'model_call':new.json()['model_call']});settings=p.request.get(origin+'/api/v1/bids/'+positive['project_id']+'/settings');assert settings.status==200 and settings.json()['settings']['auto_review'] is False
  c.close()
 b.close()
control=json.loads((root/'.local/assistant_ui01-control.json').read_text());assert control['mode']=='hold';processes=[]
for line in subprocess.check_output(['ps','-p','3220759,2888579,2888544,2888527','-o','pid=,uid=,args='],text=True).splitlines():
 pid,uid,argv=line.strip().split(maxsplit=2);assert int(uid)==1000;processes.append({'pid':int(pid),'uid':int(uid),'argv':argv})
assert len(processes)==4
result={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'verified':verified,'global_unknown_preserved':unknown,'old_and_new_project_model_ledger_retained':model,'control_final_mode':control['mode'],'processes':processes,'new_project_auto_review':False,'scope':'Readonly normal authenticated API/browser and frozen-file hashes; no SQL mutation, no global turn, no ledger deletion'};(out/'final-readback01-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'unknown_members':len(unknown),'model_call_views':len(model),'sources':[x.get('source_count') for x in verified[:2]],'control':control['mode']},ensure_ascii=False))
