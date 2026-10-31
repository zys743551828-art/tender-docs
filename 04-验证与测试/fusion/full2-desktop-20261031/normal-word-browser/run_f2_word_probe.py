"""Describe/execute a Word probe against one provider-owned normal instance.
Owns only a QA container; never creates/stops Compose services, DB, networks or host.
Credentials stay in the read-only validation volume and Node memory.
"""
from __future__ import annotations
import argparse,ast,base64,datetime,hashlib,io,json,os,pathlib,subprocess,sys,zipfile
ROOT=pathlib.Path(__file__).resolve().parent
SOURCE=ROOT/'.tools/f2/workbench'
REQUEST=SOURCE/'.local/full-desktop/accept_f2g/request.json'
FIXTURES=ROOT/'evidence-local-20261030/authoring-word-conversion-v2-20261031-012639/fixtures'
BROWSER_IMAGE='sha256:9ff5ec8223f3610e515880bfa6db1fc563463afd885920a1e38ee1a8319e3988'
PLAN={'own_resources':['one UID65532 QA container, shares exact normal web network namespace'],'provider_resources':'web/API/DB/S3/workers/normal deployment hosts untouched','origin':'http://127.0.0.1:8080','credentials':'validation_login volume RO; Node reads profile.json only; no credentials/cookies/body/token export','external':'normal UI upload/convert/adopt/parse refresh/preview/download','authoring':'prefer real manual draft + immutable builtin templates + normal export API; independently scoped controlled PAGE/RICH Worker compute','compute':'existing worker-export exec UID65532; installed fusion_word_runtime.environment; no test converter override; normal deployment host only','cleanup':'only own QA; remote fixture process has bounded timeout and cancellation signal; provider owns services/conversion lifecycle'}
EXTRACT=r"""
import io,pathlib,stat,sys,zipfile
root=pathlib.Path('/evidence');out=io.BytesIO();total=0
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
 for p in root.iterdir():
  if p.suffix not in {'.json','.png','.pdf','.docx'} or p.is_symlink() or not p.is_file():continue
  if p.name=='profile.json':raise RuntimeError('PRIVATE_PROFILE_NOT_ALLOWED')
  total+=p.stat().st_size
  if total>64*1024*1024:raise RuntimeError('EVIDENCE_LIMIT')
  archive.writestr(p.name,p.read_bytes())
sys.stdout.buffer.write(out.getvalue())
"""
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def execute(args):
 import importlib.metadata
 assert importlib.metadata.version('pypdfium2')=='5.14.0','DRIVER_PDFIUM_VERSION_MISMATCH'
 os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['WORKBENCH_ENV_FILE']='explicit-normal-probe-no-ambient-env';sys.path.insert(0,str(SOURCE))
 from deploy.disposable_docker import DockerTestRun
 from packages.word_intake import prebuilt
 class LocalRun(DockerTestRun):
  @staticmethod
  def _call(*argv,timeout=60,env=None):
   r=prebuilt._call(list(argv),timeout=timeout);return subprocess.CompletedProcess(r.args,r.returncode,r.stdout.decode('utf-8',errors='replace'),r.stderr.decode('utf-8',errors='replace'))
 def call(argv,*,data=None,timeout=60):return prebuilt._call(argv,data=data,timeout=timeout)
 public=json.loads(REQUEST.read_text(encoding='utf-8'));profiles=public['word']['profiles'];assert public['origin']==PLAN['origin']
 for lane in ['page','rich']:
  fixture=json.loads((FIXTURES/(lane+'.json')).read_text());assert isinstance(fixture['snapshot'],dict)
  # Historical synthetic payloads are reused; qualification profile comes only from this installed request.
  assert profiles[lane]['identity']['provider_profile']['identity']['protocol']=='word-prebuilt-gotenberg/2'
 out=ROOT/'evidence-local-20261030'/('f2-normal-word-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));out.mkdir();run=LocalRun(out/'qa-resources.json');qa=None;evidence_extracted=False;status={'state':'starting','started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'started_local':datetime.datetime.now().astimezone().isoformat(),'provider_services_owned_by_this_driver':False,'models_called':False,'head_equivalence_not_asserted':True};code=1
 try:
  inspect_format='{"Id":{{json .Id}},"ImageID":{{json .Image}},"User":{{json .Config.User}},"Running":{{json .State.Running}},"Labels":{{json .Config.Labels}}}'
  observed={}
  for role,identity in [('web',args.web),('worker-export',args.worker_export)]:
   r=call(['inspect','--type','container','--format',inspect_format,identity]);assert r.returncode==0;value=json.loads(r.stdout);assert value['Id']==identity and value['Running'] and value['Labels']['com.docker.compose.service']==role and value['Labels']['tender.desktop.instance']==public['instance_id'];observed[role]=value
  export_labels=observed['worker-export']['Labels'];project=export_labels['com.docker.compose.project'];assert observed['web']['Labels']['com.docker.compose.project']==project;assert export_labels.get('tender.test.disposable')!='true'
  for file,service in [(args.document_host_ready,'worker-document'),(args.export_host_ready,'worker-export')]:
   value=json.loads(file.read_text(encoding='utf-8'));binding=value['deployment_binding'];assert value['state']=='ready' and value['peer_mode']=='deployment' and binding['instance_id']==public['instance_id'] and binding['service']==service and binding['project']==project
   if service=='worker-export':assert value['container_id']==args.worker_export and binding['plan_digest']==export_labels['tender.word.plan']
  r=call(['volume','inspect','--format','{{json .Labels}}',args.login_volume]);assert r.returncode==0;volume_labels=json.loads(r.stdout);assert volume_labels['com.docker.compose.project']==project
  write(out/'provider-public-binding.json',{'instance_id':public['instance_id'],'services':observed,'host_mode_verified':'deployment','login_volume_ro':True,'profile_api_not_mounted':True,'origin':public['origin'],'public_profile_digests':{k:v['profile_digest'] for k,v in profiles.items()},'request_sha256':sha(REQUEST)})
  qa=run.container(BROWSER_IMAGE,['--network','container:'+args.web,'--user','65532:65532','--memory','4g','--cpus','2','--pids-limit','512','--tmpfs','/tmp:rw,nosuid,nodev,size=512m,mode=1777','--tmpfs','/evidence:rw,nosuid,nodev,size=128m,uid=65532,gid=65532,mode=0700','--mount',f'type=volume,source={args.login_volume},target=/validation-login,readonly','--entrypoint','python'],['-B','-c','import time;time.sleep(2400)']);run.start(qa)
  status['stage']='qa-tools';print(json.dumps({'stage':status['stage'],'evidence':str(out),'qa_container':qa}),flush=True)
  tree=ast.parse((ROOT/'run_native_browser.py').read_text(encoding='utf-8'));prep=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP' for t in n.targets));prep=prep.split("archive=pathlib.Path('/work/seaweedfs.tar.gz')")[0];lines=prep.splitlines();start=next(i for i,line in enumerate(lines) if line.startswith('def run('));prep='\n'.join(lines[:4]+["pathlib.Path('/work').mkdir(exist_ok=True)"]+[line for line in lines[start:] if "'npm','ci'" not in line]);r=call(['exec','--user','0',qa,'python','-B','-c',prep],timeout=1500);assert r.returncode==0,'QA_PREPARATION_FAILED'
  r=call(['cp',str(ROOT/'f2_word_browser.mjs'),qa+':/work/f2_word_browser.mjs']);assert r.returncode==0
  sample=ROOT/'evidence-local-20261030/word-production-dist-browser-20261031-020927/download-original.docx';cfg={'origin':public['origin'],'installation_version':public['version'],'expected_plan_digest':export_labels['tender.word.plan'],'run_id':run.run_id,'docx_base64':base64.b64encode(sample.read_bytes()).decode('ascii'),'resume_bid':args.resume_bid}
  status['stage']='normal-browser';r=call(['exec','--user','65532:65532','-e','HOME=/tmp','-e','PLAYWRIGHT_BROWSERS_PATH=/work/browsers','-i',qa,'/usr/local/bin/node','/work/f2_word_browser.mjs'],data=json.dumps(cfg).encode(),timeout=1200);status['browser_exit_code']=r.returncode
  # Deliberately never save stdout/stderr, credential-bearing error stacks or browser bodies.
  result=call(['exec','--user','65532:65532',qa,'python','-B','-c',EXTRACT],timeout=30);assert result.returncode==0
  with zipfile.ZipFile(io.BytesIO(result.stdout)) as bundle:
   for item in bundle.infolist():
    target=out/item.filename;assert len(pathlib.PurePosixPath(item.filename).parts)==1 and target.resolve().is_relative_to(out.resolve());target.write_bytes(bundle.read(item))
  evidence_extracted=True
  assert r.returncode==0,'NORMAL_BROWSER_PROBE_FAILED';browser=json.loads((out/'browser-result.json').read_text());assert browser['state']=='completed';status['external_word']=browser['external_word'];status['manual_authoring']=browser['manual_authoring']
  status['stage']='installed-page-rich-thin-compute';client=(ROOT/'f2_word_compute.py').read_text(encoding='utf-8')
  for lane in ['page','rich']:
   fixture=json.loads((FIXTURES/(lane+'.json')).read_text());fixture['profile']=profiles[lane];payload={'lane':lane,'fixture':fixture,'public_profile':profiles[lane]}
   result=call(['exec','--user','65532:65532','-i',args.worker_export,'timeout','--signal=TERM','--kill-after=10s','330s','python','-B','-c',client],data=json.dumps(payload,ensure_ascii=False).encode(),timeout=360)
   if result.returncode:status[lane]={'state':'failed','exit_code':result.returncode};raise RuntimeError('INSTALLED_FORMAT_CLIENT_FAILED')
   packet=json.loads(result.stdout);assert packet['lane']==lane and packet['controlled_format_only'] and not packet['business_authority_claimed'];value=packet['result'];assert value['state']=='succeeded' and value['coverage']=='complete' and value['gaps']==[] and value['resource_receipt']['closed'];assert value['chain']['renderer_digest']==profiles[lane]['identity']['provider_profile']['identity']['renderer_digest'],'RETURNED_RENDERER_CHAIN_MISMATCH';trace={k:packet[k] for k in ['lane','execution_locations','conversion_inputs','runtime','protocol','installed_export_environment_verified','controlled_format_only','business_authority_claimed']};write(out/(lane+'-worker-trace.json'),trace)
   for kind in ['pdf','docx']:
    binary=value.get(kind) if kind=='pdf' else packet['source_docx']
    if isinstance(binary,dict):data=base64.b64decode(binary['base64'],validate=True);assert len(data)==binary['byte_size'] and hashlib.sha256(data).hexdigest()==binary['sha256'];(out/(lane+'.'+kind)).write_bytes(data)
   def redact_binary(x):
    if isinstance(x,dict):return {k:redact_binary(v) for k,v in x.items() if k!='base64'}
    if isinstance(x,list):return [redact_binary(v) for v in x]
    return x
   write(out/(lane+'-format-result.json'),redact_binary(value));status[lane]={'state':value['state'],'coverage':value['coverage'],'controlled_format_only':True,'business_qualification_claimed':False,'resource_cleanup_closed':True,'renderer_chain_matches_installed_profile':True}
  status['state']='completed' if status['external_word'].get('state')=='passed' and status['manual_authoring'].get('state')=='succeeded' else 'incomplete';status['stage']='complete';code=0 if status['state']=='completed' else 1
 except BaseException as error:status.update(state='failed',error_type=type(error).__name__,error_code=str(error) if isinstance(error,RuntimeError) else 'PROBE_ASSERTION_OR_EXECUTION_FAILED')
 finally:
  if qa is not None and not evidence_extracted:
   try:
    captured=call(['exec','--user','65532:65532',qa,'python','-B','-c',EXTRACT],timeout=30)
    if captured.returncode==0:
     with zipfile.ZipFile(io.BytesIO(captured.stdout)) as bundle:
      for item in bundle.infolist():
       target=out/item.filename;assert len(pathlib.PurePosixPath(item.filename).parts)==1 and target.resolve().is_relative_to(out.resolve());target.write_bytes(bundle.read(item))
     status['diagnostic_evidence_extracted']=True
   except BaseException as capture_error:status['diagnostic_capture_error_type']=type(capture_error).__name__
  # Only resources registered to this exact disposable run are stopped/removed.
  if not run.closed:run.cleanup()
  status['qa_cleanup_closed']=run.closed;status['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();status['finished_local']=datetime.datetime.now().astimezone().isoformat();write(out/'summary.json',status);write(out/'task-source.json',{'files':{f:sha(ROOT/f) for f in ['run_f2_word_probe.py','f2_word_browser.mjs','f2_word_compute.py']},'source_root':str(SOURCE),'historical_fixture_profiles_not_used_for_qualification':True,'profiles_from_current_installation_request':True,'normal_service_lifecycles_untouched':True});print(json.dumps({'evidence':str(out),**status}),flush=True)
 return code
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--resume-bid');p.add_argument('--web');p.add_argument('--worker-export');p.add_argument('--login-volume');p.add_argument('--document-host-ready',type=pathlib.Path);p.add_argument('--export-host-ready',type=pathlib.Path);a=p.parse_args()
 if not a.execute:print(json.dumps(PLAN,indent=2))
 else:
  if any(getattr(a,k) is None for k in ['web','worker_export','login_volume','document_host_ready','export_host_ready']):p.error('execute requires exact ready IDs, volume and public deployment host ready paths')
  raise SystemExit(execute(a))
