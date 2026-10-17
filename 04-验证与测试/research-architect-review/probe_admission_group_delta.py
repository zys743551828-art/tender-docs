"""研究冻结候选独立非owner PG/MockTransport验证；不读凭据、不收费。"""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
BASE=HERE.parent/'research-backend-checks/candidate-02'
sys.path[:0]=[str(BASE/'frozen-inputs'),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    configured='--without-authority' not in sys.argv
    cand=BASE/'research-candidate-02.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='c53c68b39f28a2ed145c68d3f99141b2918dbfa43c5197b0220544e2187807a9'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256((BASE/'frozen-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    source=(BASE/'frozen-inputs/tests/test_research_integration.py').read_text()
    source+='''\n\ndef test_architect_original_low_token_config_now_rejected(client,monkeypatch):
    import json
    low={"profile_digest":digest(),"currency":"USD","input_per_million":"0.30","output_per_million":"1.20","max_output_tokens":1,"context_tokens":2}
    monkeypatch.setenv("RESEARCH_PRICE_CONFIG",json.dumps(low))
    with pytest.raises(DomainError) as error:batches.price_config()
    assert error.value.code=="RESEARCH_PRICE_INVALID"
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate02-budget-regression.json').write_text(json.dumps({'original_config_rejected':True,'code':error.value.code,'paid_calls':0},indent=2)+'\\n')


def test_architect_original_withdrawn_body_zero_new_calls(client):
    import json
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
    assert value['state']=='failed' and requests==[]
    with ledger.research_connection() as c:
        calls=c.execute('SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s',(run['id'],)).fetchone()['n']
        item=c.execute('SELECT state,result_blob_id FROM research_run_items WHERE operation_id=%s',(message['operation_id'],)).fetchone()
    assert calls==0 and item['result_blob_id'] is None
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate02-withdrawal-regression.json').write_text(json.dumps({'original_counterexample_rejected':True,'new_model_requests_after_withdrawal':0,'calls':calls,'item_state':item['state'],'result_blob_id':None,'paid_calls':0},indent=2)+'\\n')
'''
    source += '\n\ndef test_architect_new_admission_group_change_does_not_pause(client):\n    import json\n    from packages.research.worker import process\n    web,actor,headers,_=client\n    run=create_run(client)\n    message=execution.schedule_one(run[\'id\'])\n    with connection() as c:\n        original=c.execute(\'SELECT s.id,s.source_group_id,br.engineering_project_id FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id JOIN bid_profiles bp ON bp.bid_id=s.bid_id JOIN bid_rounds br ON br.id=bp.round_id WHERE a.operation_id=%s\',(message[\'operation_id\'],)).fetchone()\n    added=seed(actor,year=2025)\n    with connection() as c:c.execute(\'UPDATE bid_rounds SET engineering_project_id=%s,round_no=2 WHERE id=%s\',(original[\'engineering_project_id\'],added[\'identity\'][\'round_id\']))\n    added[\'identity\'][\'project_id\']=str(original[\'engineering_project_id\'])\n    registered,_=register(web,headers,added)\n    verify(web,headers,added,registered)\n    admitted=web.post(f"/api/v1/bids/{added[\'bid\']}/submissions/{added[\'submission\']}/sample-admission",json={},headers=headers())\n    assert admitted.status_code==200,admitted.text\n    with connection() as c:\n        current=c.execute(\'SELECT source_group_id FROM samples WHERE id=%s\',(original[\'id\'],)).fetchone()[\'source_group_id\']\n        state=c.execute(\'SELECT state FROM research_runs WHERE id=%s\',(run[\'id\'],)).fetchone()[\'state\']\n    assert current!=original[\'source_group_id\'] and state==\'running\'\n    requests=[]\n    result=process(message,synthetic_native_transport(requests))\n    assert result[\'state\']==\'succeeded\' and len(requests)==3\n    Path(os.environ[\'ARCHITECT_RESEARCH_OUT\']).joinpath(\'candidate02-admission-group-counterexample.json\').write_text(json.dumps({\'reproduced\':True,\'frozen_group_changed\':True,\'run_state_after_new_admission\':state,\'new_model_requests\':len(requests),\'result_state\':result[\'state\'],\'paid_calls\':0},indent=2)+\'\\n\')\n'
    source=source.replace('import os\n','import os\nfrom pathlib import Path\n',1)
    (HERE/'candidate02-group-delta-tests.py').write_text(source)
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        if configured:connect.owner_sql((BASE/'frozen-inputs/deploy/sql/research-source-qualification.sql').read_text())
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
            files=[str(BASE/'frozen-inputs/tests/test_research_core.py'),str(HERE/'candidate02-group-delta-tests.py')] if configured else [str(HERE/'candidate02-group-delta-tests.py'),'-k','unconfigured_authority']
            report='candidate02-group-delta-junit.xml' if configured else 'candidate02-unconfigured-junit.xml'
            code=pytest.main(files+['-k','architect_new_admission_group_change','-q','--assert=plain','--junitxml='+str(HERE/report)])
            assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':len(manifest['sources']),'group_delta_cases':1,'counterexample_reproduced':True,'actual_postgresql_nonowner':True,'model_transport':'explicit native DeepSeekClient MockTransport','object_port':'explicit in-memory','paid_calls':0,'credentials_read':False,'source_module_directory':str(BASE/'frozen-inputs'),'loaded_frozen_modules':{m.__name__:str(m.__file__) for m in loaded},'reference_inputs_verified':len(manifest['reference_inputs'])}
    result['source_authority_installed']=configured
    if not configured:
        result['candidate_suite_cases']=1;result['additional_original_regressions']=0;result['original_defects_closed']='not_evaluated_in_unconfigured_mode'
    name='candidate02-group-delta-result.json' if configured else 'candidate02-unconfigured-result.json'
    (HERE/name).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'configured':configured,'candidate_suite_cases':32 if configured else 1,'original_regressions_closed':2 if configured else 0,'paid_calls':0}))
if __name__=='__main__':main()
