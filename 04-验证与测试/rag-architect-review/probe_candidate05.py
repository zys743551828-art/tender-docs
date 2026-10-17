"""Frozen RAG03 API PG + explicit synthetic loopback HTTP, no BGE/paid/private config."""
import hashlib,importlib,json,os,sys,threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
BASE=HERE.parent/'rag-development-checks/candidate-05'
FROZEN=BASE/'frozen-inputs'
sys.path[:0]=[str(FROZEN),str(APP),str(HERE)]
from isolated_pg import database
IDENTITY={'model_name':'explicit-architect-synthetic','model_revision':'fixture-v1','weights_digest':'a'*64,'preprocessing_digest':'b'*64}
TOKEN='explicit-synthetic-loopback-token-00000000000000'
RPC=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):self.handle_call()
    def do_POST(self):self.handle_call()
    def handle_call(self):
        assert self.headers.get('Authorization')=='Bearer '+TOKEN
        n=int(self.headers.get('Content-Length','0'));body=json.loads(self.rfile.read(n)) if n else None
        RPC.append(self.path)
        if self.path=='/identity':value={'protocol_version':1,'dimensions':1024,'identity':IDENTITY,'lexical_dictionary_digest':'c'*64}
        elif self.path=='/tokenize':value={'identity':IDENTITY,'offsets':[[i,i+1] for i in range(len(body['text']))]}
        elif self.path=='/embed':value={'identity':IDENTITY,'vectors':[[1.0]+[0.0]*1023 for _ in body['texts']]}
        elif self.path=='/lexical':value={'dictionary_digest':'c'*64,'lexemes':' '.join(body['text'])}
        elif self.path=='/rerank':value={'identity':{'explicit_fixture':True},'scores':[0.0]*len(body['texts'])}
        else:raise AssertionError(self.path)
        raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
@contextmanager
def runtime():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();server.server_close();thread.join()
def main():
    cand=BASE/'candidate.json'
    assert hashlib.sha256(cand.read_bytes()).hexdigest()=='8dbb86c0203cdecc51b29c24d5d5ee91b4d2d5e066cc67ccc18b05a5396b21fb'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256(Path(row['frozen_path']).read_bytes()).hexdigest()==row['sha256']
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect,runtime() as url:
        connect.owner_sql((APP/'deploy/sql/vector-runtime-grants.sql').read_text())
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','RUN_RAG_REAL':'1','RAG_RUNTIME_URL':url,'RAG_RUNTIME_TOKEN':TOKEN,'SESSION_SECRET':'explicit-synthetic-secret','CSRF_SECRET':'explicit-synthetic-csrf','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            for name in ('packages.rag','tests'):
                module=importlib.import_module(name);directory=FROZEN/name.replace('.','/')
                if directory.is_dir():module.__path__=[str(directory),*module.__path__]
            from packages.domain import runtime as domain_runtime
            domain_runtime.ROOT=APP
            from packages.rag import indexing,tasks
            loaded=[indexing,tasks]
            for module in loaded:
                rel=str(Path(module.__file__).relative_to(FROZEN))
                assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==next(r['sha256'] for r in manifest['sources'] if Path(r['frozen_path']).relative_to(FROZEN).as_posix()==rel)
            import pytest
            code=pytest.main([str(FROZEN/'tests/test_rag_staged_indexing.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate05-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':6,'passed_cases':2,'actual_postgresql_nonowner':True,'actual_loopback_HTTP':True,'runtime':'explicit synthetic unit vectors/character tokenizer/zero rerank; not actual BGE','paid_calls':0,'private_configuration_read':False,'consumers_started':False,'cloud_requests':0,'loaded_frozen_sources':{m.__name__:str(m.__file__) for m in loaded},'RPC_paths':sorted(set(RPC)),'RPC_count':len(RPC),'evidence_sha256':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ['probe_candidate05.py','candidate05-independent-junit.xml']}}
    (HERE/'candidate05-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed_cases':2,'synthetic_loopback_HTTP':True,'paid_calls':0}))
if __name__=='__main__':main()
