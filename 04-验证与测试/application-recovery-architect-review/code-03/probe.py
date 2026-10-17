from pathlib import Path
import json,hashlib,shutil,sys,os,socket,subprocess
out=Path(__file__).parent
p=Path('/workspace/tender-docs/04-验证与测试/application-recovery-development-checks/code-03/candidate.json');m=json.loads(p.read_text())
assert hashlib.sha256(p.read_bytes()).hexdigest()=='5e2ab741c3f39227dc1436b83aa7a714252ec94f18abb3536a7f01f478992391'
root=out/'runtime'
for v in m['owned_sources']+m['observed_dependencies']:
 src=Path(v['archive']);assert hashlib.sha256(src.read_bytes()).hexdigest()==v['sha256'];q=root/v['path'];q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,q)
sys.path.insert(0,str(root));os.chdir(root)
import psycopg

def forbidden(*a,**kw):raise AssertionError('Independent OPS review forbids DB/resources/network')
psycopg.connect=forbidden;socket.create_connection=forbidden
from deploy.application_recovery import security
security.docker=forbidden;security.pg_tool=forbidden
# Keep only local -I Python source-import check; no arbitrary processes allowed.
original_run=subprocess.run

def process(args,*a,**kw):
 if not (isinstance(args,list) and len(args)>=3 and args[0]==sys.executable and args[1:3]==['-I','-c']):forbidden()
 return original_run(args,*a,**kw)
subprocess.run=process
import pytest
code=pytest.main(['-c','/dev/null','--rootdir',str(root),'-o','pythonpath='+str(root),str(root/'tests/test_application_recovery.py'),str(root/'tests/test_application_recovery_binding.py'),str(root/'tests/test_application_recovery_pause.py'),str(root/'tests/test_application_recovery_ddl.py'),str(out/'test_pause_creation.py'),'--assert=plain','-q','--junitxml='+str(out/'independent-junit.xml')])
for v in m['owned_sources']+m['observed_dependencies']:assert hashlib.sha256(Path(v['archive']).read_bytes()).hexdigest()==v['sha256']
(out/'result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_dependencies_verified_before_after':23,'pytest_exit':code,'DB':0,'Docker':0,'DDL':0,'native_objects':0,'models':0,'consumers':0,'subprocess':'Only existing -I Python frozen-source imports; no lifespan or service connections'},indent=2)+'\n')
sys.exit(code)
