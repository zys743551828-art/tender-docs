"""Disposable hybrid Linux browser/API + Windows real Word Worker acceptance."""
from __future__ import annotations
import ast,base64,datetime,hashlib,json,os,pathlib,secrets,signal,subprocess,sys,tempfile,time,traceback
ROOT=pathlib.Path(__file__).resolve().parent;APP=ROOT/'tender-app-dev/workbench'
sys.path.insert(0,str(APP));sys.path.insert(0,str(ROOT));os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['WORKBENCH_ENV_FILE']='explicit-no-business-env'
import boto3,psycopg
from botocore.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from run_word_prebuilt_pg import LocalRun,PG,S3,port,call
from deploy.schema_plan import BASE_SQL
from deploy.word_prebuilt_acceptance import stop_test_process,recover_conversion_receipts
from packages.word_intake import prebuilt

WORKER=r'''
import json,os,pathlib,sys,time
config=json.loads(sys.stdin.readline());os.environ.clear();os.environ.update(config['environment']);sys.path.insert(0,config['source'])
from apps.worker import engine
from packages.domain.runtime import connection,assert_role
from packages.word_intake.conversion import configured_profile
assert_role(True);assert configured_profile()['profile_digest']==config['profile_digest']
out=pathlib.Path(config['out']);seen=set();records=[]
while not (out/'stop-worker').exists():
 with connection(True) as c:
  rows=c.execute("SELECT x.payload FROM outbox x JOIN operations o ON o.id=x.operation_id WHERE x.topic='tender.task.document.v1' AND o.state IN('queued','running') ORDER BY x.id LIMIT 20").fetchall()
 for row in rows:
  message=row['payload'];identity=str(message['operation_id'])
  if identity in seen:continue
  if message['handler_version'] not in ('word-render/1','document-parse/1') and message['step_key'] not in ('verify-upload','parse-materials'):raise RuntimeError('UNEXPECTED_TASK_TYPE_NO_MODEL_EXECUTED')
  lease=engine.acquire(message)
  if lease is None:continue
  result=engine.run_compute(message,lease);assert engine.finish(message,lease,result);seen.add(identity)
  records.append({'operation_id':identity,'handler_version':message['handler_version'],'completed':True})
  (out/'worker-operations.json').write_text(json.dumps(records,indent=2))
 time.sleep(.2)
'''

LINUX=r'''
import base64,hashlib,json,os,pathlib,subprocess,sys,time,urllib.request
cfg=json.loads(pathlib.Path('/tmp/word-run.json').read_text());pathlib.Path('/tmp/word-run.json').unlink()
environment=os.environ|cfg['environment'];os.environ.update(cfg['environment']);sys.path.insert(0,'/work/source')
from packages.word_intake.conversion import configured_profile
assert configured_profile()['profile_digest']==cfg['profile_digest']
identity={n:hashlib.sha256(pathlib.Path('/work/source',n).read_bytes()).hexdigest() for n in ['packages/word_intake/prebuilt.py','packages/word_intake/inspection.py']};assert identity==cfg['source_bytes']
pathlib.Path('/evidence/source-profile-match.json').write_text(json.dumps({'source_bytes_equal':True,'profile_digest':cfg['profile_digest'],'docker_socket_mounted':False},indent=2))
api=subprocess.Popen([sys.executable,'-B','-m','uvicorn','apps.api.main:app','--host','127.0.0.1','--port','8800','--log-level','error'],cwd='/work/source',env=environment,stdout=open('/evidence/api.log','w'),stderr=subprocess.STDOUT)
coordinator=None
try:
 for _ in range(100):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8800/openapi.json',timeout=1) as r:assert r.status==200
   break
  except OSError:time.sleep(.2)
 else:raise RuntimeError('API_NOT_READY')
 coordinator=subprocess.Popen([sys.executable,'-B','-m','apps.api.word_coordinator'],cwd='/work/source',env=environment,stdout=open('/evidence/coordinator.log','w'),stderr=subprocess.STDOUT)
 browser_env={k:v for k,v in environment.items() if k in ['PATH','HOME','LANG']};browser_env['PLAYWRIGHT_BROWSERS_PATH']='/work/browsers'
 run=subprocess.run(['node','scripts/word-browser-once.mjs'],cwd='/work/source/apps/web',env=browser_env,input=json.dumps(cfg['browser']),text=True,capture_output=True,timeout=480)
 pathlib.Path('/evidence/browser.log').write_text(run.stdout+'\n'+run.stderr)
 raise SystemExit(run.returncode)
finally:
 for p in [coordinator,api]:
  if p is not None:
   p.terminate()
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
'''

def main():
 out=ROOT/'evidence-local-20261030'/('word-browser-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));out.mkdir()
 run=LocalRun(out/'resources.json');worker=None;code=1;stage='starting';private_values=[]
 status={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'started_local':datetime.datetime.now().astimezone().isoformat(),'network':'run-owned bridge; only PG/S3 published on random 127.0.0.1 ports; not Docker --internal','full_compose_word_installed':False,'kafka_tested':False,'models_called':False}
 try:
  with tempfile.TemporaryDirectory(prefix='word-browser-') as tmp:
   private=pathlib.Path(tmp);network=run._create('network',['--driver','bridge'])
   stage='tooling';print(json.dumps({'stage':stage,'evidence':str(out)}),flush=True)
   runtime=run.container('tender-fusion-checks:py312-ocr',['--network',network,'--memory','4g','--cpus','2','--mount',f'type=bind,source={APP},target=/source,readonly','--mount',f'type=bind,source={out},target=/evidence','--entrypoint','python'],['-c','import time;time.sleep(2400)']);run.start(runtime)
   tree=ast.parse((ROOT/'run_native_browser.py').read_text(encoding='utf-8'));prep=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP' for t in n.targets));prep=prep.split("archive=pathlib.Path('/work/seaweedfs.tar.gz')")[0]
   prepared=run._call('exec',runtime,'python','-c',prep,timeout=1500);(out/'prepare.log').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8');assert prepared.returncode==0,'TOOLING_FAILED'
   call('cp',str(ROOT/'word-browser-once.mjs'),runtime+':/work/source/apps/web/scripts/word-browser-once.mjs')
   for filename in ['vite.config.ts','tsconfig.json','index.html']:
    call('cp',str(APP/'apps/web'/filename),runtime+':/work/source/apps/web/'+filename)
   stage='dependencies';pg_password,access,secret,api_password,worker_password=[secrets.token_urlsafe(30) for _ in range(5)];private_values.extend([pg_password,access,secret,api_password,worker_password])
   (private/'password').write_text(pg_password);(private/'s3.json').write_text(json.dumps({'identities':[{'name':'synthetic-word-browser','credentials':[{'accessKey':access,'secretKey':secret}],'actions':['Admin','Read','Write','List','Tagging']}]}))
   pg=run.container(PG,['--network',network,'--network-alias','database','--publish','127.0.0.1::5432','--memory','1g','--tmpfs','/var/run/postgresql','--env','POSTGRES_PASSWORD_FILE=/tmp/password'],['postgres','-c','log_statement=none','-c','log_min_error_statement=panic'])
   s3=run.container(S3,['--network',network,'--network-alias','object-store','--publish','127.0.0.1::8333','--memory','768m'],['server','-dir=/data','-ip=object-store','-ip.bind=0.0.0.0','-master.volumeSizeLimitMB=64','-volume.max=4','-s3','-s3.port=8333','-s3.config=/tmp/s3.json'])
   call('cp',str(private/'password'),pg+':/tmp/password');call('cp',str(private/'s3.json'),s3+':/tmp/s3.json');run.start(pg);run.start(s3)
   pg_port,s3_port=port(pg,5432),port(s3,8333);admin=make_conninfo(host='127.0.0.1',port=pg_port,dbname='postgres',user='postgres',password=pg_password,connect_timeout=2)
   for _ in range(60):
    try:
     with psycopg.connect(admin,autocommit=True) as c:c.execute('SELECT 1')
     break
    except psycopg.OperationalError:time.sleep(1)
   else:raise RuntimeError('PG_NOT_READY')
   stage='schema';owner_dsn=make_conninfo(admin,dbname='word_browser_once')
   with psycopg.connect(admin,autocommit=True) as c:c.execute('CREATE DATABASE word_browser_once TEMPLATE template0')
   with psycopg.connect(owner_dsn,autocommit=True) as c:
    for f in [*BASE_SQL,'packages/word_intake/schema.sql']:c.execute((APP/f).read_text(encoding='utf-8'))
    for role,capability,password in [('word_browser_api','tender_api',api_password),('word_browser_worker','tender_worker',worker_password)]:
     c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)));c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(capability),sql.Identifier(role)));c.execute(sql.SQL('ALTER ROLE {} SET search_path TO design,public').format(sql.Identifier(role)))
   endpoint=f'http://127.0.0.1:{s3_port}';bucket='synthetic-word-'+run.run_id
   store=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=2,read_timeout=3,retries={'max_attempts':0}))
   for _ in range(60):
    try:store.create_bucket(Bucket=bucket);break
    except Exception:time.sleep(1)
   else:raise RuntimeError('S3_NOT_READY')
   stage='profile';profile=prebuilt.build_profile(receipt=out/'profile-resources.json');(out/'profile.json').write_text(json.dumps(profile,indent=2));call('cp',str(out/'profile.json'),runtime+':/tmp/word-profile.json')
   from uuid import uuid4
   from apps.api.auth import password_hash
   from tests.test_word_intake import make_docx
   actor,bid,project,round_id,bidder=[uuid4() for _ in range(5)];login='word-browser-'+secrets.token_hex(6);password=secrets.token_urlsafe(24);private_values.append(password)
   with psycopg.connect(owner_dsn,autocommit=True) as c:
    settings={'auto_review':False,'consistency':True,'scope_residue':True,'show_prediction':False,'pricing':False,'result_reminder':False,'accept_rules_as_written':False}
    c.execute('SET search_path TO design,public');c.execute("INSERT INTO members(id,display_name) VALUES(%s,'Synthetic browser Word member')",(actor,));c.execute('INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)',(actor,login,password_hash(password)));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'Synthetic browser Word project',%s,%s)",(bid,Jsonb(settings),actor));c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'Synthetic browser engineering')",(project,));c.execute('INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)',(round_id,project));c.execute("INSERT INTO bidders(id,name) VALUES(%s,'Synthetic browser bidder')",(bidder,));c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,round_id,bidder));c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),bid));c.execute('INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)',(bid,))
   base_env={'WORKBENCH_ENV_FILE':'explicit-word-browser-no-business-env','PYTHONDONTWRITEBYTECODE':'1','WORD_INTAKE_ENABLED':'1','OBJECT_STORE_BACKEND':'s3','OBJECT_STORE_ACCESS_KEY':access,'OBJECT_STORE_SECRET_KEY':secret,'OBJECT_STORE_REGION':'us-east-1','OBJECT_STORE_BUCKET':bucket,'SESSION_SECRET':secrets.token_urlsafe(32),'CSRF_SECRET':secrets.token_urlsafe(32)};private_values.extend([base_env['SESSION_SECRET'],base_env['CSRF_SECRET']])
   api_env=base_env|{'API_DATABASE_DSN':make_conninfo(host='database',port=5432,dbname='word_browser_once',user='word_browser_api',password=api_password),'OBJECT_STORE_ENDPOINT':'http://object-store:8333','OBJECT_TRANSFER_MODE':'same_origin','WORD_RENDER_PROFILE_PATH':'/tmp/word-profile.json','APP_ORIGIN':'http://127.0.0.1:5179'}
   (out/'conversion-resources').mkdir()
   host_env={k:v for k,v in os.environ.items() if k.upper() in ['PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA']};host_env.update({k:v for k,v in base_env.items() if k not in ['SESSION_SECRET','CSRF_SECRET']}|{'WORKER_DATABASE_DSN':make_conninfo(owner_dsn,user='word_browser_worker',password=worker_password),'OBJECT_STORE_ENDPOINT':endpoint,'WORD_RENDER_PROFILE_PATH':str(out/'profile.json'),'WORD_RENDER_TEST_RECEIPT_DIRECTORY':str(out/'conversion-resources'),'RUN_WORD_PREBUILT_PG':'1','PYTHONPATH':str(APP)})
   source_bytes={n:hashlib.sha256((APP/n).read_bytes()).hexdigest() for n in ['packages/word_intake/prebuilt.py','packages/word_intake/inspection.py']};docx=make_docx('施工工期120天，质量合格。合成浏览器Word验证。')
   cfg={'environment':api_env,'profile_digest':profile['profile_digest'],'source_bytes':source_bytes,'browser':{'login':login,'password':password,'bid':str(bid),'docx':base64.b64encode(docx).decode()}};(private/'runtime.json').write_text(json.dumps(cfg));call('cp',str(private/'runtime.json'),runtime+':/tmp/word-run.json')
   stage='browser';print(json.dumps({'stage':stage,'evidence':str(out),'browser_url':'http://127.0.0.1:5179/bids/'+str(bid)+'/materials'}),flush=True)
   worker_log=open(out/'worker.log','w',encoding='utf-8');worker=subprocess.Popen([sys.executable,'-B','-X','utf8','-c',WORKER],cwd=APP,env=host_env,stdin=subprocess.PIPE,stdout=worker_log,stderr=subprocess.STDOUT,text=True,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP);worker.stdin.write(json.dumps({'environment':host_env,'source':str(APP),'out':str(out),'profile_digest':profile['profile_digest']})+'\n');worker.stdin.flush();worker.stdin.close();worker.stdin=None
   completed=run._call('exec',runtime,'python','-B','-c',LINUX,timeout=540);(out/'driver.log').write_text(completed.stdout+'\n'+completed.stderr,encoding='utf-8');assert completed.returncode==0,'BROWSER_JOURNEY_FAILED'
   stage='verify';api_host=make_conninfo(owner_dsn,user='word_browser_api',password=api_password)
   with psycopg.connect(api_host,row_factory=dict_row) as c:
    records=c.execute('SELECT v.id,b.media_type,b.digest,b.byte_size,b.object_key FROM design.document_versions v JOIN design.blobs b ON b.id=v.blob_id WHERE v.bid_id=%s',(bid,)).fetchall();current=c.execute('SELECT current_body_id FROM design.bid_profiles WHERE bid_id=%s',(bid,)).fetchone()['current_body_id']
    relation=c.execute('SELECT kind,verification,source_document_version_id FROM design.document_relations WHERE document_version_id=%s',(current,)).fetchone()
    renders=c.execute('SELECT outcome FROM design.word_render_runs WHERE bid_id=%s ORDER BY finished_at DESC NULLS LAST LIMIT 1',(bid,)).fetchone()
   parsed=None
   for _ in range(100):
    with psycopg.connect(api_host,row_factory=dict_row) as c:
     parsed=c.execute('SELECT document_version_id,coverage,page_count,gaps FROM design.parse_runs WHERE document_version_id=%s ORDER BY created_at DESC LIMIT 1',(current,)).fetchone()
    if parsed:break
    time.sleep(.2)
   from packages.word_intake import repository
   qualified=bool(renders and repository.controlled_qualified(renders['outcome']))
   verification={'document_count':len(records),'documents':[{k:str(v) if k=='id' else v for k,v in row.items() if k!='object_key'} for row in records],'current_pdf_id':str(current),'relation':dict(relation) if relation else None,'current_pdf_parse':dict(parsed) if parsed else None,'controlled_render_qualified':qualified}
   (out/'verification.json').write_text(json.dumps(verification,default=str,indent=2)+'\n',encoding='utf-8')
   assert parsed and parsed['coverage']=='partial' and parsed['page_count']==1 and parsed['gaps'];assert len(records)==2
   assert qualified and relation and relation['kind']=='controlled_render' and relation['verification']=='verified'
   for row in records:
    data=store.get_object(Bucket=bucket,Key=row['object_key'])['Body'].read();assert hashlib.sha256(data).hexdigest()==row['digest'];local=out/('download-converted.pdf' if row['media_type']=='application/pdf' else 'download-original.docx');assert local.read_bytes()==data
    if row['media_type']=='application/pdf':assert row['id']==current
   status.update(passed=True,parse_coverage='partial',page_count=1,controlled_render_qualified=True,controlled_relation_verified=True,downloads_match_real_storage=True,api_nonowner=True,worker_nonowner=True,source_profile_match=True,browser_url='http://127.0.0.1:5179/bids/'+str(bid)+'/materials');code=0
 except BaseException as error:
  status.update(passed=False,stage=stage,error_type=type(error).__name__,frames=[{'file':pathlib.Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)])
  if isinstance(error,psycopg.Error):status.update(sqlstate=error.sqlstate,table=error.diag.table_name,constraint=error.diag.constraint_name)
 finally:
  (out/'stop-worker').touch()
  status['worker_stop_fallback_triggered']=False
  if worker is not None:
   try:worker.wait(timeout=90)
   except subprocess.TimeoutExpired:
    status['worker_stop_fallback_triggered']=True
    stop_test_process(worker)
   status['worker_exit_code']=worker.returncode
   status['worker_stopped']=worker.poll() is not None
  if not run.closed:run.cleanup()
  conversions=recover_conversion_receipts(out/'conversion-resources')
  status['conversion_cleanup']=conversions
  for log in [*out.glob('*.log'),*out.glob('browser-*.json')]:
   text=log.read_text(encoding='utf-8',errors='replace')
   for value in private_values:text=text.replace(value,'[REDACTED]')
   log.write_text(text,encoding='utf-8')
  status.update(cleanup_closed=run.closed and all(item['closed'] for item in conversions) and (worker is None or worker.poll() is not None),finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),finished_local=datetime.datetime.now().astimezone().isoformat());(out/'summary.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8');print(json.dumps({'evidence':str(out),**status}),flush=True)
 return code

if __name__=='__main__':raise SystemExit(main())
