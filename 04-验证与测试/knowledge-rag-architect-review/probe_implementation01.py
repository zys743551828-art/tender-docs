"""Knowledge RAG frozen independent PG/ASGI, synthetic model mechanisms only."""
import hashlib,importlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'knowledge-development-checks';FROZEN=BASE/'rag01-inputs';DEPS=BASE/'rag01-dependencies'
sys.path[:0]=[str(FROZEN),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'rag-candidate-01.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='4671b3d9cd24afa3429f388c76bd02d4ee88c53c0ac6869bb06976c7aff24a15'
    manifest=json.loads(cand.read_text())
    for rel,d in manifest['sources'].items():assert hashlib.sha256((FROZEN/rel).read_bytes()).hexdigest()==d
    for rel,d in manifest['dependencies'].items():assert hashlib.sha256((DEPS/rel).read_bytes()).hexdigest()==d
    source=(FROZEN/'tests/test_knowledge_rag_integration.py').read_text()
    source+=r'''


def test_architect_final_search_RR_refuses_before_trace_and_command(environment,mechanism_http,monkeypatch):
    from contextlib import contextmanager
    from packages.knowledge import rag_api
    from packages.domain.runtime import DomainError
    env=environment
    publish(env);indexed(env)
    original=rag_api.connection
    opened=[0]
    @contextmanager
    def final_rr():
        opened[0]+=1
        with original() as c:
            if opened[0]==4:c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            yield c
    monkeypatch.setattr(rag_api,'connection',final_rr)
    with pytest.raises(DomainError) as error:
        rag_api.retrieve(env['target'],env['actor'],uuid4().hex,{'query':'原件参考','purpose':'authoring','knowledge_ids':[str(env['item'])],'limit':8})
    assert error.value.code=='KNOWLEDGE_SOURCE_OR_PROFILE_CHANGED'
    assert opened[0]==4
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_retrieval_traces WHERE bid_id=%s',(env['target'],)).fetchone()['n']==0
        assert c.execute("SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='knowledge-retrieve'",(env['target'],)).fetchone()['n']==0


def test_architect_first_reference_RR_and_replay_RR_refuse(environment,mechanism_http):
    from packages.knowledge import rag_api
    from packages.knowledge.rag_search import cite_unit
    from packages.domain.runtime import DomainError
    from contextlib import contextmanager
    from unittest.mock import patch
    env=environment
    publish(env);indexed(env)
    body={'query':'原件参考','purpose':'authoring','knowledge_ids':[str(env['item'])],'limit':8}
    key=uuid4().hex
    response,_=rag_api.retrieve(env['target'],env['actor'],key,body)
    hit=response['body']['hits'][0]
    with pytest.raises(ValueError,match='fresh read-committed') as error,connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cite_unit(c,UUID(response['body']['trace_id']),env['actor'],cite_body(hit)|{'target_object_id':str(env['target'])},None)
    original=rag_api.connection
    @contextmanager
    def rr():
        with original() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            yield c
    with patch.object(rag_api,'connection',rr),pytest.raises(ValueError,match='fresh read-committed') as error:
        rag_api.retrieve(env['target'],env['actor'],key,body)
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_unit_references WHERE bid_id=%s',(env['target'],)).fetchone()['n']==0
'''
    (HERE/'implementation01-frozen-plus-delta-tests.py').write_text(source)
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    objects={}
    with database(initializer_fidelity=True) as connect:
        connect.owner_sql((APP/'deploy/sql/vector-runtime-grants.sql').read_text())
        connect.owner_sql((FROZEN/'packages/knowledge/sql/rag.sql').read_text())
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'explicit-synthetic','CSRF_SECRET':'explicit-synthetic','RUN_KNOWLEDGE_RAG_INTEGRATION':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            for package in ('packages.domain','packages.authoring','packages.rag','packages.identity','apps.api','apps.worker'):
                mod=importlib.import_module(package);directory=DEPS/package.replace('.','/')
                if directory.is_dir():mod.__path__=[str(directory),*mod.__path__]
            import apps.api
            apps.api.__path__=[str(FROZEN/'apps/api'),*apps.api.__path__]
            from packages.domain import runtime,storage
            runtime.ROOT=APP
            from packages.knowledge import rag_api,rag_search,rag_repository,rag_tasks,bridge
            from apps.api import knowledge_routes
            loaded=[rag_api,rag_search,rag_repository,rag_tasks,bridge,knowledge_routes]
            for module in loaded:
                rel=str(Path(module.__file__).relative_to(FROZEN));assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==manifest['sources'][rel]
            sys.path.insert(0,str(FROZEN/'tests'))
            import pytest
            with patch.object(storage,'put',lambda key,data,media:objects.__setitem__(key,data)),patch.object(storage,'get',lambda key:objects[key]),patch.object(storage,'sign',lambda *a,**kw:'https://explicit-memory.test/object'):
                code=pytest.main([str(FROZEN/'tests/test_knowledge_rag.py'),str(HERE/'implementation01-frozen-plus-delta-tests.py'),'-q','--assert=plain','--junitxml='+str(HERE/'implementation01-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':22,'dependencies_verified':32,'actual_PG_nonowner':True,'actual_login_ASGI':True,'model_port':'explicit synthetic mechanism; actual BGE not run','object_port':'explicit in-memory','paid_calls':0,'consumers_started':False,'private_configuration_read':False,'loaded_modules':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'implementation01-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
