"""冻结RAG02独立真实PG/ASGI机制；显式合成向量，不读取私有配置。"""
import hashlib, importlib.util, json, os
from pathlib import Path
import sys
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
CAND=Path('/workspace/tender-docs/04-验证与测试/rag-development-checks/candidate-02/candidate.json')
sys.path.insert(0,str(APP))
from isolated_pg import database

class Dense:
    identity={'model_name':'explicit-architect-fixture','model_revision':'fixture','weights_digest':'a'*64,'preprocessing_digest':'b'*64}
    lexical_dictionary_digest='c'*64
    calls=0
    hook=None
    def offsets(self,text):return [(i,i+1) for i in range(len(text))]
    def embed(self,texts):
        Dense.calls+=1
        if Dense.hook:
            hook=Dense.hook;Dense.hook=None;hook()
        return [[1.0]+[0.0]*1023 for _ in texts]
class Lexical:
    def __init__(self,*args):pass
    def lexemes(self,text):return ' '.join(text)
class Rerank:
    last_identity={'fixture':'explicit-mechanism'}
    def __init__(self,*args):pass
    def rank(self,query,texts):return [0.0]*len(texts)

def main():
    assert hashlib.sha256(CAND.read_bytes()).hexdigest()=='03cbcf424b021d91c08d398e4a482b38b957c7fa0def1c6f2be7a86bd095e101'
    manifest=json.loads(CAND.read_text());verified=[]
    for row in manifest['sources']:
        assert hashlib.sha256(Path(row['frozen_path']).read_bytes()).hexdigest()==row['sha256']
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
        verified.append({'path':row['path'],'sha256':row['sha256']})
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        connect.owner_sql((APP/'deploy/sql/vector-runtime-grants.sql').read_text())
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','RUN_RAG_REAL':'1','SESSION_SECRET':'synthetic-architect-only-secret-012345678901234567890','CSRF_SECRET':'synthetic-csrf-secret-01234567890123456789'}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            from apps.api import rag_routes
            spec=importlib.util.spec_from_file_location('architect_rag02_cases',APP/'tests/test_rag_replay_guard.py')
            cases=importlib.util.module_from_spec(spec);spec.loader.exec_module(cases)
            with patch.object(rag_routes,'DenseHttp',Dense),patch.object(rag_routes,'LexicalHttp',Lexical),patch.object(rag_routes,'RerankHttp',Rerank),patch.object(cases,'DenseHttp',Dense),patch.object(cases,'LexicalHttp',Lexical):
                cases.test_changed_but_eligible_ancestor_rejects_same_key_replay()
            from packages.rag.repository import source_scope_digest
            from datetime import datetime,timezone
            scope={'as_of':datetime.now(timezone.utc).isoformat(),'source_ids':['fixture'],'purpose':'assist','excluded_groups':[],'bid_id':'fixture'}
            prior=[{'revision':2,'status':'published','valid_until':None}]
            assert source_scope_digest(prior,scope)==source_scope_digest(prior,dict(scope))
            assert source_scope_digest(prior,scope)!=source_scope_digest([{'revision':3,'status':'published','valid_until':None}],scope)
            assert source_scope_digest(prior,scope)!=source_scope_digest(prior,{**scope,'purpose':'authoring'})
    for row in manifest['sources']:
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
    result={'candidate_sha256':hashlib.sha256(CAND.read_bytes()).hexdigest(),'sources':verified,'passed':True,'checks':['actual ASGI unchanged replay 200; eligible ancestor revision/validity change rejects409; trace remains one','manifest helper stable for identical original trusted scope; authority and purpose changes alter digest'],'model_port':'explicit synthetic 1024D normalized fixture; no real BGE in this delta','paid_calls':0,'real_object_requests':0,'credentials_read':False,'source_hashes_unchanged':True}
    (HERE/'candidate02-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'sources':len(verified),'paid_calls':0}))
if __name__=='__main__':main()
