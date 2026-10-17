"""研究冻结候选独立非owner PG/MockTransport验证；不读凭据、不收费。"""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
BASE=HERE.parent/'research-backend-checks'
sys.path[:0]=[str(BASE/'frozen-inputs'),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'research-candidate-01.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='20e92d95e30325f3a44d98bb160fa680dcfe777c05ce60c64faa33326e1279d5'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256((BASE/'frozen-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    source=(BASE/'frozen-inputs/tests/test_research_integration.py').read_text()
    source+='''\n\ndef test_architect_low_token_price_config_underreserves(client,monkeypatch):
    import json
    from packages.research.worker import process
    low={"profile_digest":digest(),"currency":"USD","input_per_million":"0.30","output_per_million":"1.20","max_output_tokens":1,"context_tokens":2}
    monkeypatch.setenv("RESEARCH_PRICE_CONFIG",json.dumps(low))
    run=create_run(client)
    message=execution.schedule_one(run['id'])
    requests=[]
    value=process(message,synthetic_native_transport(requests))
    assert value['state']=='succeeded' and len(requests)==3
    assert all(r['max_tokens']==4096 for r in requests)
    with ledger.research_connection() as c:
        calls=c.execute('SELECT reserved_amount FROM model_calls WHERE research_run_id=%s',(run['id'],)).fetchall()
    assert all(str(r['reserved_amount'])=='0.00000180' or r['reserved_amount']==__import__('decimal').Decimal('0.0000018') for r in calls)
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('budget-counterexample.json').write_text(json.dumps({'reproduced':True,'accepted_price_context_tokens':2,'accepted_price_output_tokens':1,'actual_request_max_tokens':4096,'calls':3,'reserved_per_call':str(calls[0]['reserved_amount']),'paid_calls':0},indent=2)+'\\n')


def test_architect_withdrawn_original_still_sends_new_calls(client):
    import json
    from pathlib import Path
    from packages.research.worker import process
    from packages.rag.indexing import ensure_parse_sources,withdraw_source
    run=create_run(client)
    message=execution.schedule_one(run['id'])
    with connection() as c:
        original=c.execute('SELECT pr.id,pr.bid_id FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id JOIN submission_records sr ON sr.id=s.submission_id JOIN parse_runs pr ON pr.document_version_id=sr.body_version_id WHERE a.operation_id=%s',(message['operation_id'],)).fetchone()
    with connection(True) as c:
        root,child=ensure_parse_sources(c,original['bid_id'],original['id'],publish_readable=True)
        revision=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(root,)).fetchone()['revision']
        withdraw_source(c,original['bid_id'],child,revision)
    requests=[]
    value=process(message,synthetic_native_transport(requests))
    assert value['state']=='succeeded' and len(requests)==3
    with connection() as c:state=c.execute('SELECT status FROM retrieval.source_heads WHERE source_id=%s',(root,)).fetchone()['status']
    assert state=='withdrawn'
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('withdrawal-counterexample.json').write_text(json.dumps({'reproduced':True,'original_source_status':state,'new_model_requests_after_withdrawal':len(requests),'item_state':value['state'],'paid_calls':0,'fixture':'SQL original; explicit native MockTransport'},indent=2)+'\\n')
'''
    source=source.replace('import os\n','import os\nfrom pathlib import Path\n',1)
    (HERE/'frozen-plus-independent-tests.py').write_text(source)
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        connect.owner_sql('CREATE ROLE architect_research LOGIN INHERIT NOSUPERUSER; GRANT tender_research_worker TO architect_research; ALTER ROLE architect_research SET search_path TO design,public;')
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn;research=worker.replace('architect_rag_worker','architect_research')
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'RESEARCH_WORKER_DATABASE_DSN':research,'APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'explicit-synthetic-secret','CSRF_SECRET':'explicit-synthetic-csrf','RUN_RESEARCH_INTEGRATION':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','ARCHITECT_RESEARCH_OUT':str(HERE)}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            import pytest
            from packages.research import batches,chapters,execution,ledger,model_port,worker
            from apps.api import research_routes
            loaded=[batches,chapters,execution,ledger,model_port,worker,research_routes]
            for module in loaded:
                rel=module.__name__.replace('.','/')+'.py'
                expected=next(row['sha256'] for row in manifest['sources'] if row['path']==rel)
                assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==expected
            for row in manifest['reference_inputs']:
                assert hashlib.sha256((APP/row['path']).read_bytes()).hexdigest()==row['sha256']
            code=pytest.main([str(BASE/'frozen-inputs/tests/test_research_core.py'),str(HERE/'frozen-plus-independent-tests.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate01-independent-junit.xml')])
            assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':len(manifest['sources']),'original_cases':25,'additional_reproduced_defects':2,'actual_postgresql_nonowner':True,'model_transport':'explicit native DeepSeekClient MockTransport','object_port':'explicit in-memory','paid_calls':0,'credentials_read':False,'source_module_directory':str(BASE/'frozen-inputs'),'loaded_frozen_modules':{m.__name__:str(m.__file__) for m in loaded},'reference_inputs_verified':len(manifest['reference_inputs'])}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'original_cases':25,'counterexamples':2,'paid_calls':0}))
if __name__=='__main__':main()
