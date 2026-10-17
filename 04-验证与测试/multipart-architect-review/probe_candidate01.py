"""冻结分片模块独立PG/ASGI机制；对象端口明确内存替身，禁止云请求。"""
import hashlib,io,json,os,sys,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
sys.path.insert(0,str(APP));sys.path.insert(0,str(HERE.parent/'rag-architect-review'))
from isolated_pg import database

class MemoryObjects:
    def __init__(self):self.uploads={};self.objects={};self.lock=threading.Lock();self.native=self;self.calls=[]
    def _key(self,**kw):return kw['Key']
    def init_multipart_upload(self,key,headers):
        with self.lock:
            ident=uuid4().hex;self.uploads[ident]={'key':key,'parts':{}};self.calls.append('init')
        return SimpleNamespace(upload_id=ident)
    def list_parts(self,key,ident,marker,max_parts):
        parts=self.uploads[ident]['parts']
        return SimpleNamespace(parts=[SimpleNamespace(part_number=n,etag=hashlib.md5(data).hexdigest(),size=len(data)) for n,data in sorted(parts.items())],is_truncated=False)
    def upload_part(self,key,ident,number,data):
        with self.lock:self.uploads[ident]['parts'][number]=data
        return SimpleNamespace(etag=hashlib.md5(data).hexdigest())
    def complete_multipart_upload(self,key,ident,parts,headers):
        from oss2.exceptions import NoSuchUpload
        with self.lock:
            if ident not in self.uploads:raise NoSuchUpload(404,{},b'',{})
            value=self.uploads.pop(ident);self.objects[key]=b''.join(value['parts'][p.part_number] for p in parts)
    def abort_multipart_upload(self,key,ident):
        with self.lock:self.uploads.pop(ident,None)
    def head_object(self,**kw):
        from botocore.exceptions import ClientError
        if kw['Key'] not in self.objects:raise ClientError({'Error':{'Code':'NoSuchKey'}},'HeadObject')
        return {'ContentLength':len(self.objects[kw['Key']])}
    def get_object(self,**kw):
        data=self.objects[kw['Key']];return {'ContentLength':len(data),'Body':io.BytesIO(data)}
    def put_object(self,**kw):self.objects[kw['Key']]=kw['Body']

def main():
    cand=APP/'docs/multipart-development-checks/candidate.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='8fa3b069cf40b56f82b2bf09d13734eb400a9214b921baa50fa3c6d8781bec0a'
    manifest=json.loads(cand.read_text());observed={}
    for rel,digest in manifest['sources'].items():
        assert hashlib.sha256((APP/rel).read_bytes()).hexdigest()==digest
        assert hashlib.sha256((cand.parent/'frozen-inputs'/rel).read_bytes()).hexdigest()==digest
    for rel in manifest['observed_runtime_dependencies']:observed[rel]=hashlib.sha256((APP/rel).read_bytes()).hexdigest()
    memory=MemoryObjects();original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'synthetic-only-architect-secret','CSRF_SECRET':'synthetic-only-csrf-secret','OBJECT_STORE_BACKEND':'oss','OBJECT_STORE_BUCKET':'explicit-in-memory-only','RUN_MULTIPART_INTEGRATION':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            from packages.domain import storage
            from packages.uploads import service
            import pytest
            with patch.object(storage,'client',lambda:memory),patch.object(service,'initialization_candidate',lambda *a:None):
                code=pytest.main([str(APP/'tests/test_multipart.py'),str(APP/'tests/test_multipart_integration.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate01-independent-junit.xml')]);assert code==0
    for rel,digest in manifest['sources'].items():assert hashlib.sha256((APP/rel).read_bytes()).hexdigest()==digest
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'passed':True,'checks':9,'actual_postgresql_nonowner':True,'object_port':'explicit in-memory OSS API fixture; real OSS not exercised by architect','actual_asgi':True,'source_hashes_unchanged':True,'observed_dependencies':observed,'paid_calls':0,'real_object_requests':0,'credentials_read':False}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'checks':9}))
if __name__=='__main__':main()
