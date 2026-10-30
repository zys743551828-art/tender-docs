"""One local five-container Word mailbox timeout/retry acceptance. Not deployment.

Default invocation describes resources only. --execute starts the isolated run.
Application sources are never edited. The separate host executor is not containerized.
"""
from __future__ import annotations
import argparse,ast,base64,concurrent.futures,datetime,hashlib,json,os,pathlib,secrets,shutil,subprocess,sys,tempfile,time,traceback
ROOT=pathlib.Path(__file__).resolve().parent
APP=ROOT/'tender-app-dev/workbench'
IMAGE='tender-workbench-runtime:py3.12.16-candidate-20261030'
HOST_SHA='6ec55f7afbd88061d13e9f69178229e0c04d454ed01dc9fba894c839393a49d3'
CODEC_SHA='84ca1c67e3129745a9ff97343ce6999586bc83ea5ba12ef7d30359d09750c0dd'
PREBUILT_SHA='be44fcf07b5efee8482f4b3893a3bf99666ae296853c89bda145c90e198c1924'
PLAN={'mode':'local isolated acceptance only','application_image':IMAGE,'containers':['PG17','private S3','browser QA tooling only','API uid65532 readonly','Worker uid65532 readonly'],'networks':1,'host_executor':'separate bounded host process; not containerized','application_sources':'packages, apps/api, apps/worker, deploy, pyproject and requirements all readonly','api_generated_state':'/workspace/.local tmpfs16m uid/gid65532 mode0700','application_tmpfs':'/tmp512m','worker_mailbox':'/tmp/word-execution','protocol':'word-conversion-execution/2; rawconvert only','first_failure':'original external client 120s, executor absent','retry':'start pinned executor only after real timeout; UI double click must create 1 POST/new operation','guards':'no docker.sock; no DB/model env in QA or host executor; no model calls; per-job Gotenberg networknone','cleanup':'stop executor and application services before PG/S3; official process and conversion recovery helpers'}

ROLE=r'''
import hashlib,json,os,pathlib,sys
sys.path.insert(0,'/workspace')
from packages.domain.runtime import connection,assert_role
from packages.word_intake.conversion import configured_profile
worker=os.environ['WORKBENCH_RUNTIME_ROLE']=='worker';assert_role(worker)
with connection(worker) as c:
 role=c.execute("SELECT current_user AS current_user,r.rolsuper AS superuser,r.rolcreatedb AS createdb,r.rolcreaterole AS createrole,r.rolbypassrls AS bypassrls,(r.oid=d.datdba) AS database_owner FROM pg_roles r JOIN pg_database d ON d.datname=current_database() WHERE r.rolname=current_user").fetchone()
 assert not any(role[k] for k in ['superuser','createdb','createrole','bypassrls','database_owner'])
 pathlib.Path('/tmp/role.json').write_text(json.dumps(role,indent=2))
expected=json.loads(os.environ['WORD_SOURCE_PROOF']);observed={p:hashlib.sha256(pathlib.Path('/workspace',p).read_bytes()).hexdigest() for p in expected};assert expected==observed
assert configured_profile()['profile_digest']==os.environ['WORD_PROFILE_DIGEST']
pathlib.Path('/tmp/source-profile.json').write_text(json.dumps({'source_bytes_equal':True,'source_sha256':observed,'profile_digest':os.environ['WORD_PROFILE_DIGEST'],'uid':os.geteuid()},indent=2))
'''
API=ROLE+r'''
import subprocess,uvicorn
coordinator=subprocess.Popen([sys.executable,'-B','-m','apps.api.word_coordinator'])
try:uvicorn.run('apps.api.main:app',host='0.0.0.0',port=8800,log_level='error')
finally:
 coordinator.terminate()
 try:coordinator.wait(timeout=10)
 except subprocess.TimeoutExpired:coordinator.kill();coordinator.wait(timeout=5)
'''
WORKER=ROLE+r'''
import signal,time
from apps.worker import engine
from packages.word_intake.execution_transport import CONVERSION_TIMEOUT
qualification_trace=[];request_shapes=[];profile_errors=[]
def observe(frame,event,arg):
 try:
  code=frame.f_code;filename=code.co_filename.replace('\\','/')
  if event=='call' and filename.endswith('/packages/word_intake/prebuilt.py') and code.co_name in ('validate_profile','admit','_font_gaps_from_facts','inspect_pdf'):
   qualification_trace.append({'function':code.co_name,'qualified_name':code.co_qualname,'source':'packages/word_intake/prebuilt.py','pid':os.getpid(),'uid':os.geteuid(),'platform':sys.platform})
  if event=='return' and filename.endswith('/packages/word_intake/execution_transport.py') and code.co_name=='_request' and isinstance(arg,bytes):
   request=json.loads(arg);request_shapes.append({'protocol':request['protocol'],'operation':request['operation'],'top_level_keys':sorted(request),'argument_keys':sorted(request['arguments']),'source_bytes_present':bool(request['arguments']['source']),'forbidden_business_fields_present':bool(set(request['arguments']) & {'snapshot','materials','profile','coverage'}),'pid':os.getpid(),'uid':os.geteuid()})
 except Exception as error:profile_errors.append(type(error).__name__)
sys.setprofile(observe)
def stop(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
seen=set();records=[]
try:
 while True:
  with connection(True) as c:
   rows=c.execute("SELECT x.payload FROM outbox x JOIN operations o ON o.id=x.operation_id WHERE x.topic='tender.task.document.v1' AND o.state IN('queued','running') AND x.available_at<=now() ORDER BY x.available_at,x.id LIMIT 20").fetchall()
  for row in rows:
   message=row['payload'];key=(str(message['operation_id']),message['generation'])
   if key in seen:continue
   if message['handler_version'] not in ('word-render/1','document-parse/1') and message['step_key'] not in ('verify-upload','parse-materials'):raise RuntimeError('UNEXPECTED_TASK_TYPE_NO_MODEL_EXECUTED')
   start=time.monotonic();engine.handle(message);seen.add(key)
   with connection(True) as c:
    state=c.execute('SELECT state FROM operations WHERE id=%s',(message['operation_id'],)).fetchone()['state'];receipt=c.execute('SELECT output FROM step_receipts WHERE operation_id=%s AND step_key=%s',(message['operation_id'],message['step_key'])).fetchone()
   records.append({'operation_id':key[0],'generation':key[1],'handler_version':message['handler_version'],'state':state,'seconds':time.monotonic()-start,'output':receipt['output'] if receipt else None,'external_client_timeout_seconds':CONVERSION_TIMEOUT})
   pathlib.Path('/tmp/worker-events.json').write_text(json.dumps(records,default=str,indent=2))
   pathlib.Path('/tmp/worker-qualification.json').write_text(json.dumps({'trace':qualification_trace,'request_shapes':request_shapes,'observer_errors':profile_errors,'observer':'sys.setprofile; original functions unmodified; no argument values persisted'},indent=2))
  time.sleep(.2)
except KeyboardInterrupt:pass
finally:sys.setprofile(None)
'''
QA=r'''
import hashlib,json,pathlib,subprocess,os,urllib.request,time
p=pathlib.Path('/tmp/browser-run.json');cfg=json.loads(p.read_text());p.unlink()
for _ in range(120):
 try:
  with urllib.request.urlopen(cfg['backend']+'/openapi.json',timeout=1) as r:assert r.status==200
  break
 except OSError:time.sleep(.2)
else:raise RuntimeError('APPLICATION_API_NOT_READY')
digest=hashlib.sha256(pathlib.Path('/work/source/apps/web/scripts/word-container-browser.mjs').read_bytes()).hexdigest();assert digest==cfg.pop('browser_script_sha256')
pathlib.Path('/evidence/runtime-browser-source.json').write_text(json.dumps({'runtime_sha256':digest,'task_sha256':digest,'equal':True},indent=2))
env={k:v for k,v in os.environ.items() if k in ['PATH','HOME','LANG']};env['PLAYWRIGHT_BROWSERS_PATH']='/work/browsers'
result=subprocess.run(['node','scripts/word-container-browser.mjs'],cwd='/work/source/apps/web',env=env,input=json.dumps(cfg),text=True,capture_output=True,timeout=720)
pathlib.Path('/evidence/browser.log').write_text(result.stdout+'\n'+result.stderr);raise SystemExit(result.returncode)
'''

# Docker's archive/cp API does not reliably expose a live tmpfs on Desktop.
# Read only these generated, bounded proof files as the service uid instead.
METADATA_READ=r'''
import json,pathlib,sys
path=sys.argv[1]
assert path in ('/tmp/role.json','/tmp/source-profile.json','/tmp/worker-events.json','/tmp/worker-qualification.json')
p=pathlib.Path(path)
assert p.is_file() and not p.is_symlink() and p.stat().st_size<=1048576
value=json.loads(p.read_text(encoding='utf-8'))
print(json.dumps(value))
'''

def execute():
 os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['WORKBENCH_ENV_FILE']='explicit-container-word-no-business-env';sys.path.insert(0,str(APP));sys.path.insert(0,str(ROOT))
 import boto3,psycopg
 from botocore.config import Config
 from psycopg import sql
 from psycopg.conninfo import make_conninfo
 from psycopg.rows import dict_row
 from psycopg.types.json import Jsonb
 from run_word_prebuilt_pg import LocalRun,PG,S3,port,call
 from deploy.schema_plan import BASE_SQL
 from deploy.word_prebuilt_acceptance import stop_test_process,recover_conversion_receipts
 from packages.word_intake import prebuilt,repository
 from apps.api.auth import password_hash
 from tests.test_word_intake import make_docx
 from uuid import uuid4
 assert hashlib.sha256((APP/'deploy/word_execution_host.py').read_bytes()).hexdigest()==HOST_SHA,'HOST_INTERFACE_CHANGED'
 assert hashlib.sha256((APP/'packages/word_intake/execution_transport.py').read_bytes()).hexdigest()==CODEC_SHA,'CODEC_CHANGED'
 assert hashlib.sha256((APP/'packages/word_intake/prebuilt.py').read_bytes()).hexdigest()==PREBUILT_SHA,'PREBUILT_CHANGED'
 out=ROOT/'evidence-local-20261030'/('word-conversion-v2-browser-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));out.mkdir()
 (out/'frozen-inputs.json').write_text(json.dumps({'source_sha256':{'deploy/word_execution_host.py':HOST_SHA,'packages/word_intake/execution_transport.py':CODEC_SHA,'packages/word_intake/prebuilt.py':PREBUILT_SHA},'working_bytes_uncommitted_at_start':True,'head_2db_not_claimed_as_runtime_source':True},indent=2),encoding='utf-8')
 run=LocalRun(out/'resources.json');services=[];executor=None;future=None;pool=None;qa=None;private_values=[];code=1;stage='tooling';recovered=[]
 def metadata(identity,source,destination,required=True):
  result=run._call('exec','--user','65532:65532',identity,'python','-B','-c',METADATA_READ,source,timeout=15)
  if result.returncode:
   # Fixed metadata paths contain no configuration values; do not persist arbitrary stderr.
   if required:raise RuntimeError('GENERATED_METADATA_READ_FAILED:'+pathlib.PurePosixPath(source).name+':'+str(result.returncode))
   return {'path':source,'read':False,'exit_code':result.returncode}
  value=json.loads(result.stdout);(out/destination).write_text(json.dumps(value,indent=2),encoding='utf-8');return value
 status={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'started_local':datetime.datetime.now().astimezone().isoformat(),'topology':PLAN,'host_executor_containerized':False,'models_called':False,'full_compose_installed':False,'kafka_tested':False}
 with tempfile.TemporaryDirectory(prefix='word-container-browser-') as tmp:
  private=pathlib.Path(tmp);host_report=private/'host-report';host_stop=private/'stop-host'
  try:
   network=run._create('network',['--driver','bridge'])
   qa=run.container('tender-fusion-checks:py312-ocr',['--network',network,'--memory','4g','--cpus','2','--mount',f'type=bind,source={APP},target=/source,readonly','--mount',f'type=bind,source={out},target=/evidence','--entrypoint','python'],['-c','import time;time.sleep(2400)']);run.start(qa)
   print(json.dumps({'stage':stage,'evidence':str(out)}),flush=True)
   tree=ast.parse((ROOT/'run_native_browser.py').read_text(encoding='utf-8'));prep=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP' for t in n.targets));prep=prep.split("archive=pathlib.Path('/work/seaweedfs.tar.gz')")[0]
   result=run._call('exec',qa,'python','-c',prep,timeout=1500);(out/'prepare.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8');assert result.returncode==0,'QA_TOOLING_FAILED'
   for name in ['vite.config.ts','tsconfig.json','index.html']:call('cp',str(APP/'apps/web'/name),qa+':/work/source/apps/web/'+name)
   call('cp',str(ROOT/'word-container-browser.mjs'),qa+':/work/source/apps/web/scripts/word-container-browser.mjs')
   stage='dependencies';pg_pass,access,secret,api_pass,worker_pass=[secrets.token_urlsafe(30) for _ in range(5)];private_values.extend([pg_pass,access,secret,api_pass,worker_pass]);(private/'password').write_text(pg_pass);(private/'s3.json').write_text(json.dumps({'identities':[{'name':'synthetic-word-container','credentials':[{'accessKey':access,'secretKey':secret}],'actions':['Admin','Read','Write','List','Tagging']}]}))
   pg=run.container(PG,['--network',network,'--network-alias','database','--publish','127.0.0.1::5432','--memory','1g','--tmpfs','/var/run/postgresql','--env','POSTGRES_PASSWORD_FILE=/tmp/password'],['postgres','-c','log_statement=none','-c','log_min_error_statement=panic'])
   s3=run.container(S3,['--network',network,'--network-alias','object-store','--publish','127.0.0.1::8333','--memory','768m'],['server','-dir=/data','-ip=object-store','-ip.bind=0.0.0.0','-master.volumeSizeLimitMB=64','-volume.max=4','-s3','-s3.port=8333','-s3.config=/tmp/s3.json'])
   call('cp',str(private/'password'),pg+':/tmp/password');call('cp',str(private/'s3.json'),s3+':/tmp/s3.json');run.start(pg);run.start(s3);pg_port,s3_port=port(pg,5432),port(s3,8333)
   admin=make_conninfo(host='127.0.0.1',port=pg_port,dbname='postgres',user='postgres',password=pg_pass,connect_timeout=2)
   for _ in range(60):
    try:
     with psycopg.connect(admin,autocommit=True) as c:c.execute('SELECT 1')
     break
    except psycopg.OperationalError:time.sleep(1)
   else:raise RuntimeError('PG_READY_TIMEOUT')
   owner=make_conninfo(admin,dbname='word_container_once')
   with psycopg.connect(admin,autocommit=True) as c:c.execute('CREATE DATABASE word_container_once TEMPLATE template0')
   with psycopg.connect(owner,autocommit=True) as c:
    for filename in [*BASE_SQL,'packages/word_intake/schema.sql']:c.execute((APP/filename).read_text(encoding='utf-8'))
    for role,capability,password in [('word_container_api','tender_api',api_pass),('word_container_worker','tender_worker',worker_pass)]:
     c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)));c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(capability),sql.Identifier(role)));c.execute(sql.SQL('ALTER ROLE {} SET search_path TO design,public').format(sql.Identifier(role)))
    c.execute('SET search_path TO design,public');actor,bid,project,round_id,bidder=[uuid4() for _ in range(5)];login='word-container-'+secrets.token_hex(6);password=secrets.token_urlsafe(24);private_values.append(password)
    settings={'auto_review':False,'consistency':True,'scope_residue':True,'show_prediction':False,'pricing':False,'result_reminder':False,'accept_rules_as_written':False}
    c.execute("INSERT INTO members(id,display_name) VALUES(%s,'Synthetic container Word member')",(actor,));c.execute('INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)',(actor,login,password_hash(password)));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'Synthetic container Word project',%s,%s)",(bid,Jsonb(settings),actor));c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'Synthetic Word engineering')",(project,));c.execute('INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)',(round_id,project));c.execute("INSERT INTO bidders(id,name) VALUES(%s,'Synthetic Word bidder')",(bidder,));c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,round_id,bidder));c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),bid));c.execute('INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)',(bid,))
   endpoint=f'http://127.0.0.1:{s3_port}';bucket='synthetic-word-container-'+run.run_id
   store=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=2,read_timeout=3,retries={'max_attempts':0}))
   for _ in range(60):
    try:store.create_bucket(Bucket=bucket);break
    except Exception:time.sleep(1)
   else:raise RuntimeError('S3_READY_TIMEOUT')
   profile=prebuilt.build_profile(receipt=out/'profile-resources.json');profile_path=private/'profile.json';profile_path.write_text(json.dumps(profile));shutil.copy2(profile_path,out/'profile.json')
   sources={name:hashlib.sha256((APP/name).read_bytes()).hexdigest() for name in ['packages/word_intake/prebuilt.py','packages/word_intake/inspection.py','packages/word_intake/execution_transport.py']}
   base={'WORKBENCH_ENV_FILE':'explicit-word-container-no-business-env','PYTHONDONTWRITEBYTECODE':'1','HOME':'/tmp','WORD_INTAKE_ENABLED':'1','OBJECT_STORE_BACKEND':'s3','OBJECT_STORE_ENDPOINT':'http://object-store:8333','OBJECT_STORE_ACCESS_KEY':access,'OBJECT_STORE_SECRET_KEY':secret,'OBJECT_STORE_REGION':'us-east-1','OBJECT_STORE_BUCKET':bucket,'WORD_RENDER_PROFILE_PATH':'/run/word-profile.json','WORD_PROFILE_DIGEST':profile['profile_digest'],'WORD_SOURCE_PROOF':json.dumps(sources)}
   api_env=base|{'WORKBENCH_RUNTIME_ROLE':'api','API_DATABASE_DSN':make_conninfo(host='database',port=5432,dbname='word_container_once',user='word_container_api',password=api_pass),'OBJECT_TRANSFER_MODE':'same_origin','APP_ORIGIN':'http://127.0.0.1:5179','SESSION_SECRET':secrets.token_urlsafe(32),'CSRF_SECRET':secrets.token_urlsafe(32)};private_values.extend([api_env['SESSION_SECRET'],api_env['CSRF_SECRET']])
   worker_env=base|{'WORKBENCH_RUNTIME_ROLE':'worker','WORKER_DATABASE_DSN':make_conninfo(host='database',port=5432,dbname='word_container_once',user='word_container_worker',password=worker_pass),'WORD_RENDER_EXECUTION_DIRECTORY':'/tmp/word-execution'}
   def service(role,environment,program):
    path=private/(role+'.env');path.write_text('\n'.join(key+'='+value for key,value in environment.items())+'\n')
    flags=['--network',network,'--network-alias','workbench-'+role,'--user','65532:65532','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','1g','--tmpfs','/tmp:rw,nosuid,nodev,size=512m,mode=1777','--env-file',str(path),'--workdir','/workspace','--mount',f'type=bind,source={profile_path},target=/run/word-profile.json,readonly','--entrypoint','python']
    for name in ['packages','apps/api','apps/worker','deploy','requirements.lock.txt','pyproject.toml']:flags+=['--mount',f'type=bind,source={APP/name},target=/workspace/{name},readonly']
    if role=='api':flags+=['--tmpfs','/workspace/.local:rw,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700']
    identity=run.container(IMAGE,flags,['-B','-c',program]);services.append(identity);run.start(identity);return identity
   stage='application';api_id=service('api',api_env,API);worker_id=service('worker',worker_env,WORKER)
   cfg={'login':login,'password':password,'bid':str(bid),'docx':base64.b64encode(make_docx('施工工期120天，质量合格。合成容器浏览器Word验证。')).decode(),'backend':'http://workbench-api:8800','browser_script_sha256':hashlib.sha256((ROOT/'word-container-browser.mjs').read_bytes()).hexdigest()};(private/'browser.json').write_text(json.dumps(cfg));call('cp',str(private/'browser.json'),qa+':/tmp/browser-run.json')
   stage='real-timeout-then-retry';print(json.dumps({'stage':stage,'evidence':str(out),'worker_id':worker_id,'run_id':run.run_id}),flush=True)
   pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);future=pool.submit(run._call,'exec',qa,'python','-B','-c',QA,timeout=800)
   deadline=time.monotonic()+810
   while not future.done():
    if time.monotonic()>deadline:raise RuntimeError('BROWSER_DEADLINE')
    if (out/'start-host-executor.json').exists() and executor is None:
     events=metadata(worker_id,'/tmp/worker-events.json','worker-events-before-host.json');failed=[event for event in events if event['handler_version']=='word-render/1' and event['state']=='failed'];assert len(failed)==1 and failed[0]['seconds']>=119 and failed[0]['output']['error_code']=='WORD_EXECUTION_TIMEOUT','FIRST_FAILURE_NOT_ORIGINAL_CLIENT_TIMEOUT'
     clean_env={k:v for k,v in os.environ.items() if k.upper() in ['PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA']};host_log=open(out/'host-executor.log','w',encoding='utf-8')
     command=[sys.executable,'-B',str(APP/'deploy/word_execution_host.py'),'--container-id',worker_id,'--run-id',run.run_id,'--report-dir',str(host_report),'--stop-file',str(host_stop)]
     executor=subprocess.Popen(command,cwd=ROOT,env=clean_env,stdin=subprocess.DEVNULL,stdout=host_log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
     for _ in range(200):
      ready=host_report/'ready.json'
      if ready.is_file():
       value=json.loads(ready.read_text(encoding='utf-8'));assert value=={'state':'ready','container_id':worker_id,'run_id':run.run_id};break
      if executor.poll() is not None:raise RuntimeError('HOST_EXECUTOR_NOT_READY')
      time.sleep(.1)
     else:raise RuntimeError('HOST_EXECUTOR_READY_TIMEOUT')
     (out/'host-executor-started.json').write_text(json.dumps({'container_id':worker_id,'run_id':run.run_id,'ready':True,'environment_has_db_or_model_keys':False,'host_sha256':HOST_SHA},indent=2))
    time.sleep(.2)
   result=future.result();(out/'driver.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8');assert result.returncode==0,'BROWSER_FAILED'
   stage='verify';host_records=[json.loads(path.read_text(encoding='utf-8')) for path in (host_report/'requests').glob('*.json')];assert len(host_records)==1 and host_records[0]['operation']=='convert_docx' and host_records[0]['state']=='published' and not any(key in host_records[0] for key in ['lane','snapshot','materials','profile','coverage']);api_host=make_conninfo(owner,user='word_container_api',password=api_pass)
   with psycopg.connect(api_host,row_factory=dict_row) as c:
    current=c.execute('SELECT current_body_id FROM design.bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_body_id'];records=c.execute('SELECT v.id,b.media_type,b.digest,b.byte_size,b.object_key FROM design.document_versions v JOIN design.blobs b ON b.id=v.blob_id WHERE v.bid_id=%s',(bid,)).fetchall();parsed=c.execute('SELECT document_version_id,coverage,page_count,gaps FROM design.parse_runs WHERE document_version_id=%s ORDER BY created_at DESC LIMIT 1',(current,)).fetchone();relation=c.execute('SELECT kind,verification,source_document_version_id FROM design.document_relations WHERE document_version_id=%s',(current,)).fetchone();renders=c.execute('SELECT r.operation_id,r.outcome,o.state FROM design.word_render_runs r JOIN design.operations o ON o.id=r.operation_id WHERE r.bid_id=%s ORDER BY r.created_at,r.operation_id',(bid,)).fetchall()
   verified={'document_count':len(records),'current_pdf':str(current),'parse':dict(parsed) if parsed else None,'relation':dict(relation) if relation else None,'word_render_runs':[{'operation_id':str(row['operation_id']),'state':row['state'],'qualified':bool(row['outcome'] and repository.controlled_qualified(row['outcome']))} for row in renders]};(out/'verification.json').write_text(json.dumps(verified,default=str,indent=2))
   assert len(renders)==2 and sum(bool(row['outcome'] and repository.controlled_qualified(row['outcome'])) for row in renders)==1;assert parsed['coverage']=='partial' and parsed['page_count']==1 and parsed['gaps'];assert relation['kind']=='controlled_render' and relation['verification']=='verified';assert len(records)==2
   for row in records:
    data=store.get_object(Bucket=bucket,Key=row['object_key'])['Body'].read();assert hashlib.sha256(data).hexdigest()==row['digest'];assert (out/('download-converted.pdf' if row['media_type']=='application/pdf' else 'download-original.docx')).read_bytes()==data
   trace=metadata(worker_id,'/tmp/worker-qualification.json','worker-qualification.json');assert not trace['observer_errors'];required={'validate_profile','admit','_font_gaps_from_facts','inspect_pdf'};assert required<={event['function'] for event in trace['trace']};assert all(event['uid']==65532 and event['platform']=='linux' for event in trace['trace']);assert len(trace['request_shapes'])==2 and all(shape['protocol']=='word-conversion-execution/2' and shape['operation']=='convert_docx' and shape['argument_keys']==['requested_fonts','source'] and not shape['forbidden_business_fields_present'] for shape in trace['request_shapes'])
   status.update(worker_local_qualification_proved=True,narrow_conversion_v2_proved=True,working_bytes_uncommitted_at_start=True,passed=True,first_real_client_timeout=True,retry_single_post_and_new_run=True,downloads_match_s3=True,parse_coverage='partial',api_worker_candidate_image=True,browser_url='http://127.0.0.1:5179/bids/'+str(bid)+'/materials');code=0
  except BaseException as error:
   status.update(passed=False,stage=stage,error_type=type(error).__name__,frames=[{'file':pathlib.Path(frame.filename).name,'line':frame.lineno,'function':frame.name} for frame in traceback.extract_tb(error.__traceback__)])
   if isinstance(error,psycopg.Error):status.update(sqlstate=error.sqlstate,table=error.diag.table_name,constraint=error.diag.constraint_name)
  finally:
   host_stop.touch()
   if executor is not None:
    try:executor.wait(timeout=90)
    except subprocess.TimeoutExpired:stop_test_process(executor)
    status['host_executor_stopped']=executor.poll() is not None;status['host_executor_exit_code']=executor.returncode
   for identity in services:
    role='api' if identity==services[0] else 'worker'
    for source,destination in [('/tmp/role.json',role+'-role.json'),('/tmp/source-profile.json',role+'-source-profile.json')]+([('/tmp/worker-events.json','worker-events.json'),('/tmp/worker-qualification.json','worker-qualification.json')] if role=='worker' else []):
     try:metadata(identity,source,destination,required=False)
     except (ValueError,OSError):pass
    result=LocalRun._call('logs',identity);(out/(role+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    resource=next(item for item in run.resources if item['id']==identity)
    if run._owned(resource) is not None:LocalRun._call('stop','--time','15',identity)
   if future is not None and not future.done() and qa is not None:LocalRun._call('stop','--time','10',qa)
   if pool is not None:pool.shutdown(wait=True,cancel_futures=True)
   if host_report.is_dir():
    for directory in sorted((host_report/'receipts').glob('*')):
     if directory.is_dir():recovered.extend(recover_conversion_receipts(directory))
    shutil.copytree(host_report,out/'host-executor-report')
   if not run.closed:run.cleanup()
   for path in [*out.glob('*.log'),*out.glob('browser-*.json')]:
    text=path.read_text(encoding='utf-8',errors='replace')
    for value in private_values:text=text.replace(value,'[REDACTED]')
    path.write_text(text,encoding='utf-8')
   status.update(conversion_cleanup=recovered,cleanup_closed=run.closed and all(item['closed'] for item in recovered) and (executor is None or executor.poll() is not None),finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),finished_local=datetime.datetime.now().astimezone().isoformat());(out/'summary.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8');print(json.dumps({'evidence':str(out),**status}),flush=True)
 return code

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
 if not args.execute:print(json.dumps(PLAN,indent=2))
 else:raise SystemExit(execute())
