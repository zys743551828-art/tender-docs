from pathlib import Path
import hashlib,json,shutil,sys,os,subprocess,socket
out=Path(__file__).parent
p=Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/page-port-static-01/candidate.json')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='ddd11c50e4191b865b7e423bf895bd620ceb42a97c4927167112f60ecc755c86'
m=json.loads(p.read_text());dest=out/'runtime'
for v in m['sources']:
 src=Path(v['archive']);assert hashlib.sha256(src.read_bytes()).hexdigest()==v['sha256'];q=dest/v['path'];q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,q)
for v in m['external_inputs']:
 assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256']
os.environ['AUTHORING_GENERATED_BASE_PROFILE_FIXTURE']=m['external_inputs'][2]['path']
sys.path.insert(0,str(dest));os.chdir(dest)
def forbidden(*a,**kw):raise AssertionError('Independent offline review: no processes/network/DB permitted')
subprocess.Popen=forbidden;subprocess.run=forbidden;socket.create_connection=forbidden
import psycopg
psycopg.connect=forbidden
import pytest
code=pytest.main(['-c','/dev/null','--rootdir',str(dest),'-o','pythonpath='+str(dest),str(dest/'tests/test_authoring_generated.py'),str(out/'test_geometry.py'),'--assert=plain','-q','--junitxml='+str(out/'independent-junit.xml')])
assert all(hashlib.sha256(Path(v['archive']).read_bytes()).hexdigest()==v['sha256'] for v in m['sources'])
(out/'result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'input_sources_verified_before_after':19,'pytest_exit':code,'new_Docker':0,'subprocess':0,'network':0,'DB':0,'paid':0,'note':'Real PDFium reads own minimal ASCII PDF fixtures; no actual LibreOffice conversion.'},indent=2)+'\n')
sys.exit(code)
