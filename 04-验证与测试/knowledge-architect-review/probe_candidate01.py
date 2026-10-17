"""企业知识冻结候选，独立PG/HTTP/合成PDF；对象端口内存，不请求S3。"""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'knowledge-development-checks'
sys.path[:0]=[str(BASE/'candidate01-inputs'),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'candidate-01.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='0f23c448a071ed86804911e69c7204d1da446ee9ae34f7f1caac7c104a1d7d87'
    manifest=json.loads(cand.read_text())
    for rel,d in manifest['sources'].items():assert hashlib.sha256((BASE/'candidate01-inputs'/rel).read_bytes()).hexdigest()==d
    source=(BASE/'candidate01-inputs/tests/test_knowledge_integration.py').read_text()
    source+='''\n\ndef test_architect_pending_fact_satisfies_knowledge_condition(environment):
    import json
    from psycopg.types.json import Jsonb
    env=environment
    body=copy.deepcopy(env['body'])
    body['applicability']['conditions']=[{'subject':'工程','scope':'当前项目','name':'开挖深度','value':3,'unit':'m'}]
    active=publish(env,body)
    with connection() as c:
        fact_set,fact=uuid4(),uuid4()
        c.execute('INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)',(fact_set,env['target'],sha(str(fact_set))))
        c.execute('INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(fact,env['target'],fact_set,'工程','当前项目','开挖深度',Jsonb(3),'m',[]))
        c.execute('INSERT INTO fact_heads(bid_id,fact_set_id,revision) VALUES(%s,%s,1) ON CONFLICT(bid_id) DO UPDATE SET fact_set_id=excluded.fact_set_id,revision=fact_heads.revision+1',(env['target'],fact_set))
        assert c.execute("SELECT count(*) AS n FROM decision_heads WHERE bid_id=%s AND object_kind='fact'",(env['target'],)).fetchone()['n']==0
        selected=bridge.eligible_knowledge_sources(c,env['target'],'authoring',now(),[UUID(active['version_id'])])
        assert len(selected['eligible'])==1
    Path(os.environ['ARCHITECT_KNOWLEDGE_OUT']).joinpath('unconfirmed-fact-counterexample.json').write_text(json.dumps({'reproduced':True,'matching_current_fact_value':3,'fact_evidence_ids':[],'fact_decision_heads':0,'knowledge_eligible':True,'paid_calls':0},indent=2)+'\\n')
'''
    (HERE/'frozen-plus-independent-tests.py').write_text(source)
    orig=Path.read_text
    def private_off(p,*a,**kw):return '' if p.resolve()==APP/'.env' else orig(p,*a,**kw)
    objects={}
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'explicit-synthetic-only','CSRF_SECRET':'explicit-synthetic-csrf','OBJECT_STORE_BACKEND':'s3','RUN_KNOWLEDGE_INTEGRATION':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','ARCHITECT_KNOWLEDGE_OUT':str(HERE)}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            from packages.domain import storage
            from packages.knowledge import repository,bridge,sources
            from apps.api import knowledge_routes
            loaded=[repository,bridge,sources,knowledge_routes]
            for module in loaded:
                rel=module.__name__.replace('.','/')+'.py';assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==manifest['sources'][rel]
            import pytest
            with patch.object(storage,'put',lambda key,data,media:objects.__setitem__(key,data)),patch.object(storage,'get',lambda key:objects[key]),patch.object(storage,'sign',lambda *a,**kw:'https://explicit-memory.test/object'):
                code=pytest.main([str(BASE/'candidate01-inputs/tests/test_knowledge.py'),str(HERE/'frozen-plus-independent-tests.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate01-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':10,'original_cases':21,'additional_defect_reproductions':1,'paid_calls':0,'actual_postgresql_nonowner':True,'actual_login_asgi':True,'original_pdf':'explicit existing synthetic PDF; PDFium actual text extraction','object_port':'explicit in-memory; no S3 requests','credentials_read':False,'loaded_sources':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'original_cases':21,'counterexamples':1,'paid_calls':0}))
if __name__=='__main__':main()
