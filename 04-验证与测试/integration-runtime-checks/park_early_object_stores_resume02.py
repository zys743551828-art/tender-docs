"""Park exact early synthetic S3 containers, preserving volumes/config/rootfs for restore."""
import gzip,hashlib,json,os,shutil,subprocess,time
from pathlib import Path
BASE=Path('/workspace/tender-workbench/.local/parked-early-object-stores01');BASE.mkdir(mode=0o700,exist_ok=True)
ENV=dict(os.environ)
for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):ENV.pop(k,None)
CMD=['docker','--host=unix:///var/run/docker.sock']
def call(args):return subprocess.run(CMD+args,env=ENV,check=True,capture_output=True,text=True).stdout

def private(p,d):
 if p.exists():return
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(d,f,indent=2);f.write('\n')
records=[]
for name,identifier in [('tender-workbench-qa-object-store-1','79c3ec4a0d4f5cfe8b39a5671389f58a8e350428766f2c5ea9ac6dc3c0a55182'),('tender-workbench-dev-object-store-1','dee6a2311dfaa4bef1c0274900fcd492a511e3842f0ce51d54398aae4b5321ab')]:
 folder=BASE/name;folder.mkdir(mode=0o700,exist_ok=True)
 d=json.loads(call(['inspect',identifier]))[0]
 if (folder/'original-inspect.json').exists():
  original=json.loads((folder/'original-inspect.json').read_text());assert original['Id']==d['Id'] and original['Config']==d['Config'] and {m['Destination']:m for m in original['Mounts']}=={m['Destination']:m for m in d['Mounts']};d=original
 assert d['Id']==identifier and d['Name']=='/'+name and d['Config']['WorkingDir']=='/data'
 assert d['Config']['Image'].startswith('chrislusf/seaweedfs') and '-dir=/data' in d['Config']['Cmd']
 volumes=[m for m in d['Mounts'] if m['Type']=='volume'];binds=[m for m in d['Mounts'] if m['Type']=='bind']
 assert len(volumes)==1 and volumes[0]['Destination']=='/data' and volumes[0]['Name']==name.replace('-object-store-1','_object_data')
 assert len(binds)==1 and binds[0]['Destination']=='/etc/seaweedfs/s3-identities.json' and not binds[0]['RW']
 v=json.loads(call(['volume','inspect',volumes[0]['Name']]))[0]
 private(folder/'original-inspect.json',d);private(folder/'original-volume.json',v)
 identity_bytes=Path(binds[0]['Source']).read_bytes();p=folder/'bound-identities.json';p.write_bytes(identity_bytes);p.chmod(0o600)
 before=shutil.disk_usage('/workspace').free
 call(['stop','--time','20',identifier]);stopped=json.loads(call(['inspect',identifier]))[0]
 if (folder/'original-inspect.json').exists():
  original=json.loads((folder/'original-inspect.json').read_text());assert original['Id']==d['Id'] and original['Config']==d['Config'] and {m['Destination']:m for m in original['Mounts']}=={m['Destination']:m for m in d['Mounts']};d=original
 private(folder/'stopped-inspect.json',stopped)
 assert not stopped['State']['Running'] and stopped['State']['ExitCode']==0
 diff=call(['diff',identifier]);private(folder/'resume-readback.json',{'id':identifier,'mounts_order_only':'same entries by destination; first attempt had compared unstable Docker list ordering','rootfs_diff':diff})
 archive=folder/'container-rootfs.tar.gz'
 if not archive.exists():
  proc=subprocess.Popen(CMD+['export',identifier],env=ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   with open(archive,'xb') as raw:
    os.chmod(archive,0o600)
    with gzip.GzipFile(filename='',mode='wb',fileobj=raw,compresslevel=6,mtime=0) as target:shutil.copyfileobj(proc.stdout,target,1024*1024)
   error=proc.stderr.read();assert proc.wait()==0,'container export failed'
  finally:
   if proc.poll() is None:proc.terminate();proc.wait()
 digest=hashlib.sha256();total=0
 with gzip.open(archive,'rb') as stream:
  while chunk:=stream.read(1024*1024):digest.update(chunk);total+=len(chunk)
 assert total>1000000
 again=json.loads(call(['inspect',identifier]))[0];assert again['Id']==identifier and {m['Destination']:m for m in again['Mounts']}=={m['Destination']:m for m in d['Mounts']} and not again['State']['Running']
 call(['rm',identifier])
 kept=json.loads(call(['volume','inspect',v['Name']]))[0];assert kept['Name']==v['Name'] and kept['Mountpoint']==v['Mountpoint'] and kept['Labels']==v['Labels']
 assert Path(binds[0]['Source']).read_bytes()==identity_bytes
 result={'name':name,'removed_container_id':identifier,'image_id':d['Image'],'retained_volume':v['Name'],'private_original_config':str(folder/'original-inspect.json'),'rootfs_archive':str(archive),'compressed_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'uncompressed_tar_sha256':digest.hexdigest(),'uncompressed_bytes':total,'compressed_bytes':archive.stat().st_size,'bound_config_sha256':hashlib.sha256(identity_bytes).hexdigest(),'exit_code':0,'volume_unchanged_identity':True,'free_before':before,'free_after':shutil.disk_usage('/workspace').free,'volume_deleted':False,'objects_deleted':False}
 private(folder/'park-result.json',result);records.append(result)
private(BASE/'park-results.json',records)
out=Path('/workspace/tender-docs/04-验证与测试/integration-runtime-checks/early-object-stores-parked01.json')
private(out,{'scope':'temporarily parked earliest synthetic local S3 runtimes; native OSS/PG/Kafka unchanged','private_base':str(BASE),'records':records,'free_after':shutil.disk_usage('/workspace').free})
print(json.dumps({'parked':[r['name'] for r in records],'retained_volumes':[r['retained_volume'] for r in records],'rootfs_and_original_config_archived':True,'free_bytes':shutil.disk_usage('/workspace').free}))
