"""One explicit temporary Desktop full/1 baseline; no application or Git edits."""
from __future__ import annotations
import argparse,json,os,re,signal,socket,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parent
WB=ROOT/'.tools/d188/workbench'
PY=Path(r'D:\workspace\tender\tender-app\workbench\.venv\Scripts\python.exe')
DIRECTORY=WB/'.local/full-desktop/accept35_verify'
PY_IMAGE='tender-workbench-runtime@sha256:126140f957f2988cea63a404c84debc7787fe7a800cb53b084fbd8624968ee5e'
WEB_IMAGE='nginx:1.30.5-alpine@sha256:8f84ed99befc3891b8f329c5c202785278a2cfb7c25107d57fb2a134a3117433'
sys.path.insert(0,str(WB))
from deploy import fusion_desktop as cli
from deploy import fusion_desktop_compose as desktop
from deploy import fusion_full_spec as spec
ENV=cli._environment() | {'PYTHONDONTWRITEBYTECODE':'1','WORKBENCH_ENV_FILE':'explicit-accept188-no-inherited-profile'}
ALLOWED_KEYS={'ok','code','failure_type','sqlstate','stage','project','origin','plan_digest','services_started','model_configured','task_runner_enabled','private_values_exported','synthetic_transport_verified','provider_calls','roles','database','action','volumes_deleted','bootstrap_only','runtime_uid','nonowner','same_origin_http','session_and_ticket_isolation','kafka_worker_parse','physical_pages','coverage','current_body_changed','download_sha256'}
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
def run(args,timeout=30):
 process=subprocess.Popen(args,cwd=WB,env=ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
 try:
  stdout,stderr=process.communicate(timeout=timeout)
  return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
 except BaseException:
  try:
   process.send_signal(signal.CTRL_BREAK_EVENT)
   process.communicate(timeout=15)
  except BaseException:
   subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],env=ENV,capture_output=True,timeout=15,check=False)
   process.communicate(timeout=15)
  raise
def docker(*args,timeout=30):return run(['docker',*args],timeout)
def list_owned(kind,project):
 args={'container':['container','ls','-a','-q','--no-trunc'],'network':['network','ls','-q','--no-trunc'],'volume':['volume','ls','-q']}[kind]
 r=docker(*args,'--filter','label=com.docker.compose.project='+project)
 if r.returncode:raise RuntimeError('PUBLIC_RESOURCE_ENUMERATION_FAILED')
 return r.stdout.split()
def inspect(kind,identity):
 formats={'container':'{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"health":{{json .State.Health.Status}}}', 'network':'{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Labels}},"attached":{{json .Containers}}}', 'volume':'{"name":{{json .Name}},"labels":{{json .Labels}}}'}
 # Some one-shot services have no Health entry.
 if kind=='container':formats[kind]='{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}'
 r=docker(kind,'inspect','--format',formats[kind],identity)
 if r.returncode:
  if 'no such' in r.stderr.lower() or 'not found' in r.stderr.lower():return None
  raise RuntimeError('PUBLIC_RESOURCE_INSPECTION_FAILED')
 return json.loads(r.stdout)
def safe_events(raw):
 found=[]
 for line in raw.splitlines():
  if '{' not in line:continue
  candidate=line[line.find('{'):]
  try:value=json.loads(candidate)
  except (ValueError,TypeError):continue
  if not isinstance(value,dict):continue
  # Only public scalar metadata from the established CLIs; discard all raw logs.
  public={k:v for k,v in value.items() if k in ALLOWED_KEYS and (v is None or type(v) in {str,int,bool} or (k=='roles' and type(v) is list and all(type(i)is str and re.fullmatch('[a-z_]+',i) for i in v)))}
  if public:found.append(public)
 return found
def cli_command(action,origin=None):
 args=[str(PY),'-X','utf8','-B','-m','deploy.fusion_desktop',action,'--directory',str(DIRECTORY)]
 if action=='prepare':args+=['--name','full_accept35v','--origin',origin,'--python-image',PY_IMAGE,'--web-image',WEB_IMAGE]
 return args
def planning(out):
 if DIRECTORY.exists():raise RuntimeError('ACCEPT188_DIRECTORY_ALREADY_EXISTS')
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 origin=f'http://127.0.0.1:{port}'
 request={'version':desktop.VERSION,'instance_id':uuid4().hex,'name':'full_accept35v','origin':origin,'python_image':PY_IMAGE,'web_image':WEB_IMAGE}
 plan={'version':spec.VERSION,'deployment_id':uuid4().hex,'name':request['name'],'origin':origin,'images':spec.IMAGES|{'python':PY_IMAGE,'web':WEB_IMAGE},'topology':{'mode':'compose','postgres_host':'postgres','postgres_port':5432,'kafka_bootstrap':'kafka:9092','s3_endpoint':'http://object-store:8333'},'kafka_cluster_id':'A'*22,'bucket':'fusion-full-'+uuid4().hex,'ordered_sql':[{'path':v.path.replace('\\','/'),'sha256':v.sha256} for v in spec.ordered_sql()],'sources':spec.sources(),'frontend':spec.frontend(),'policy_sha256':spec.digest((WB/spec.POLICY).read_bytes()),'model_configured':False,'task_runner_enabled':False}
 plan['bucket']='fusion-full-'+plan['deployment_id']
 document=desktop.document(request,DIRECTORY,plan)
 enabled={n:v for n,v in document['services'].items() if not v.get('profiles')}
 nets=sorted({n for v in enabled.values() for n in v.get('networks',[])})
 vols=sorted({m['source'] for v in enabled.values() for m in v.get('volumes',[]) if m.get('type')=='volume'})
 images=[]
 for role,image in plan['images'].items():
  r=docker('image','inspect','--format','{{.Os}}|{{.Architecture}}',image)
  images.append({'role':role,'image':image,'available':r.returncode==0,'platform':r.stdout.strip() if r.returncode==0 else None})
 old=json.loads((WB/'.local/full-desktop/accept188/public-plan.json').read_text())
 normalize=lambda values:{k.replace('\\','/'):v for k,v in values.items()}
 before,after=normalize(old['sources']),normalize(plan['sources'])
 changed={key for key in set(before)|set(after) if before.get(key)!=after.get(key)}
 save(out/'source-change-vs188.json',{'changed_paths':sorted(changed),'expected_changed_path':'deploy/fusion_desktop_verify.py'})
 assert changed=={'deploy/fusion_desktop_verify.py'}, 'UNEXPECTED_SOURCE_CHANGE'
 gitdir=Path((WB.parent/'.git').read_text().strip().removeprefix('gitdir: '))
 head=(gitdir/'HEAD').read_text().strip()
 assert head=='35b9e3c6f84a43ac79a2f21b4a3616ae378f61d8', 'HEAD_CHANGED'
 save(out/'fixture-preflight.json',json.loads((ROOT/'evidence-local-20261030/desktop35-fixture-preflight.json').read_text()))
 assert normalize(old['frontend'])==normalize(plan['frontend']), 'FROZEN_DIST_CHANGED'
 prior=json.loads((ROOT/'evidence-local-20261030/desktop-accept188-185421/cleanup-recovery.json').read_text())
 assert prior['closed'] is True and len(prior['before']['container'])==4
 assert all(v['health'] is None for v in prior['before']['container'])
 save(out/'optional-health-regression.json',{'evidence':'desktop-accept188-185421/cleanup-recovery.json','actual_no_health_containers_inspected':4,'source_change_only_fixed_pdf_fixture':True,'dist_unchanged':True,'new_resources_for_template_regression':0})
 result={'scope':'Temporary local isolated full/1 baseline; no Word v2/model/automation integration','worktree':str(WB),'directory':str(DIRECTORY),'origin':origin,'default_services':sorted(enabled),'default_service_count':len(enabled),'verification_extra_service':'validation','expected_total_containers_with_verify':len(enabled)+1,'declared_volumes':sorted(document['volumes']),'expected_used_volumes':vols,'expected_used_volume_count':len(vols),'declared_networks':sorted(document['networks']),'expected_used_networks':nets,'images':images,'commands':[cli_command(a,origin) for a in ['prepare','up','verify','stop']],'cleanup':'Stop exact saved group, inspect label/id ownership, individually rm containers without -v, remove only initially absent matching project networks/volumes; preserve all ambiguity'}
 save(out/'dry-plan.json',result)
 if not all(v['available'] and v['platform']=='linux|amd64' for v in images):raise RuntimeError('REQUIRED_FIXED_LOCAL_IMAGE_MISSING_OR_PLATFORM_CHANGED')
 print(json.dumps({k:result[k] for k in ['origin','default_service_count','expected_total_containers_with_verify','expected_used_volume_count','expected_used_networks','images']},indent=2),flush=True)
 return result
def execute(out,plan):
 events=[];journal={'scope':plan['scope'],'stages':{},'observations':{},'cleanup':{'removed':[],'retained':[]},'started_resources':False};project=instance=None
 def checkpoint():save(out/'result.json',journal)
 def snapshot(stage):
  values={}
  for kind in ['container','network','volume']:
   values[kind]=[inspect(kind,i) for i in list_owned(kind,project)]
  journal['observations'][stage]=values;checkpoint();return values
 def phase(action):
  started=time.monotonic();r=run(cli_command(action,plan['origin']),timeout={'prepare':45,'up':530,'verify':270,'stop':150}[action])
  record={'returncode':r.returncode,'seconds':round(time.monotonic()-started,2),'public_events':safe_events(r.stdout+'\n'+r.stderr),'raw_output_retained':False}
  journal['stages'][action]=record;checkpoint();print(json.dumps({'phase':action,**record}),flush=True)
  if r.returncode:raise RuntimeError('DESKTOP_'+action.upper()+'_FAILED')
 try:
  with socket.socket() as sock:sock.bind(('127.0.0.1',int(plan['origin'].rsplit(':',1)[1])))
  phase('prepare')
  request=json.loads((DIRECTORY/'request.json').read_bytes());desktop.validate_request(request);project=desktop.project(request);instance=request['instance_id']
  journal.update(project=project,instance_id=instance,origin=request['origin']);checkpoint()
  if request['origin']!=plan['origin']:raise RuntimeError('PUBLIC_REQUEST_CHANGED')
  # Query exact future names as well as labels before anything starts.
  for kind,names in [('volume',plan['declared_volumes']),('network',plan['declared_networks'])]:
   if list_owned(kind,project):raise RuntimeError('PREEXISTING_PROJECT_RESOURCE')
   for name in names:
    if inspect(kind,project+'_'+name) is not None:raise RuntimeError('PREEXISTING_EXPECTED_RESOURCE_NAME')
  if list_owned('container',project):raise RuntimeError('PREEXISTING_PROJECT_CONTAINER')
  journal['initial_project_resources_empty']=True;journal['started_resources']=True;checkpoint()
  phase('up');snapshot('after_up')
  phase('verify');snapshot('after_verify')
  journal['outcome']='passed'
 except BaseException as error:
  journal['outcome']='cancelled' if isinstance(error,KeyboardInterrupt) else 'failed'
  journal['error_code']=str(error) if isinstance(error,RuntimeError) and re.fullmatch('[A-Z_]+',str(error)) else type(error).__name__
  checkpoint();print(json.dumps({'outcome':journal['outcome'],'error_code':journal['error_code']}),flush=True)
 finally:
  if project and journal.get('initial_project_resources_empty') and journal['started_resources']:
   try:snapshot('before_cleanup')
   except Exception:journal['cleanup']['snapshot_error']='PUBLIC_RESOURCE_ENUMERATION_FAILED'
   try:phase('stop')
   except BaseException:journal['cleanup']['normal_stop_failed']=True
   for kind in ['container','network','volume']:
    try:identities=list_owned(kind,project)
    except Exception:
     journal['cleanup']['retained'].append({'kind':kind,'reason':'ENUMERATION_FAILED'});continue
    for identity in identities:
     try:
      value=inspect(kind,identity)
      if value is None:continue
      labels=value.get('labels') or {};owned=labels.get('com.docker.compose.project')==project
      if kind=='container':
       owned=owned and labels.get('tender.desktop.instance')==instance and labels.get('com.docker.compose.service') in set(plan['default_services'])|{'validation'}
      else:
       logical=labels.get('com.docker.compose.'+kind)
       owned=owned and logical in plan['declared_'+('volumes' if kind=='volume' else 'networks')] and value['name']==project+'_'+logical
      if not owned:raise RuntimeError('OWNERSHIP_UNCONFIRMED')
      if kind=='container':
       if value['status']=='running':docker('container','stop','--time','45',identity,timeout=60)
       current=inspect(kind,identity)
       args=['container','rm']+(['--force'] if current and current['status']=='running' else [])+[identity]
      elif kind=='network':
       if value.get('attached'):raise RuntimeError('NETWORK_STILL_ATTACHED')
       args=['network','rm',identity]
      else:args=['volume','rm',identity]
      r=docker(*args,timeout=60)
      if r.returncode or inspect(kind,identity) is not None:raise RuntimeError('EXACT_REMOVAL_FAILED')
      journal['cleanup']['removed'].append({'kind':kind,'id':identity,'name':value['name']})
     except BaseException as error:
      journal['cleanup']['retained'].append({'kind':kind,'id':identity,'reason':str(error) if isinstance(error,RuntimeError) else type(error).__name__})
     checkpoint()
   try:
    remaining={k:list_owned(k,project) for k in ['container','network','volume']}
    journal['cleanup']['remaining']=remaining;journal['cleanup']['closed']=not any(remaining.values()) and not journal['cleanup']['retained']
   except Exception:journal['cleanup']['closed']=False
  else:journal['cleanup']['closed']=not journal['started_resources']
  checkpoint();print(json.dumps({'evidence':str(out),'outcome':journal.get('outcome'),'cleanup_closed':journal['cleanup']['closed'],'removed_counts':{k:sum(x['kind']==k for x in journal['cleanup']['removed']) for k in ['container','network','volume']}}),flush=True)
 return 0 if journal.get('outcome')=='passed' and journal['cleanup']['closed'] else 1
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--execute',type=Path);args=parser.parse_args()
 if args.execute:
  out=args.execute.resolve();raise SystemExit(execute(out,json.loads((out/'dry-plan.json').read_text())))
 out=ROOT/'evidence-local-20261030'/('desktop-accept35-verify-'+datetime.now(timezone.utc).strftime('%H%M%S'));out.mkdir()
 try:planning(out)
 except Exception as error:
  save(out/'preflight-error.json',{'code':str(error) if isinstance(error,RuntimeError) else type(error).__name__});raise
 print('DRY_PLAN',out,flush=True)
