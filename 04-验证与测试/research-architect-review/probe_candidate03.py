"""研究冻结候选独立非owner PG/MockTransport验证；不读凭据、不收费。"""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
BASE=HERE.parent/'research-backend-checks/candidate-03'
sys.path[:0]=[str(BASE/'frozen-inputs'),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    configured='--without-authority' not in sys.argv
    cand=BASE/'research-candidate-03.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='2877c2a331e4d3a5ca3884626322b6731b3311cd27a4fea779949dc935dfb9ce'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256((BASE/'frozen-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    source=(BASE/'frozen-inputs/tests/test_research_integration.py').read_text()
    source+='''\n\ndef test_architect_original_low_token_config_now_rejected(client,monkeypatch):
    import json
    low={"profile_digest":digest(),"currency":"USD","input_per_million":"0.30","output_per_million":"1.20","max_output_tokens":1,"context_tokens":2}
    monkeypatch.setenv("RESEARCH_PRICE_CONFIG",json.dumps(low))
    with pytest.raises(DomainError) as error:batches.price_config()
    assert error.value.code=="RESEARCH_PRICE_INVALID"
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate03-budget-regression.json').write_text(json.dumps({'original_config_rejected':True,'code':error.value.code,'paid_calls':0},indent=2)+'\\n')


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
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate03-withdrawal-regression.json').write_text(json.dumps({'original_counterexample_rejected':True,'new_model_requests_after_withdrawal':0,'calls':calls,'item_state':item['state'],'result_blob_id':None,'paid_calls':0},indent=2)+'\\n')
'''
    source += r"""


def test_architect_original_raw_admission_then_forced_resume_still_blocks(client):
    import json
    from packages.research.worker import process
    web,actor,headers,_=client
    run=create_run(client)
    message=execution.schedule_one(run['id'])
    original=run_sample_identities(message)
    added=seed(actor,year=2025,project_id=original['project_id'],round_no=2)
    outcome,_=register(web,headers,added)
    verify(web,headers,added,outcome)
    admitted=web.post(f"/api/v1/bids/{added['bid']}/submissions/{added['submission']}/sample-admission",json={},headers=headers())
    assert admitted.status_code==200 and admitted.json()['eligible_label_pair']
    with connection() as c:
        current=c.execute('SELECT source_group_id FROM samples WHERE id=%s',(original['id'],)).fetchone()['source_group_id']
        state=c.execute('SELECT state FROM research_runs WHERE id=%s',(run['id'],)).fetchone()['state']
    assert current!=original['source_group_id'] and state=='paused'
    requests=[]
    assert process(message,synthetic_native_transport(requests)) is None and not requests
    with ledger.research_connection() as c:
        c.execute("UPDATE research_runs SET state='running' WHERE id=%s",(run['id'],))
    result=process(message,synthetic_native_transport(requests))
    assert result['state']=='failed' and not requests
    with ledger.research_connection() as c:
        calls=c.execute('SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s',(run['id'],)).fetchone()['n']
    assert calls==0
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate03-admission-regression.json').write_text(json.dumps({'raw_admission_changed_frozen_group':True,'run_state_after_admission':state,'forced_resume_blocked':True,'new_model_requests':0,'ledger_rows':calls,'paid_calls':0},indent=2)+'\n')
"""
    source=source.replace('import os\n','import os\nfrom pathlib import Path\n',1)
    (HERE/'candidate03-frozen-plus-original-regressions.py').write_text(source)
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
            files=[str(BASE/'frozen-inputs/tests/test_research_core.py'),str(HERE/'candidate03-frozen-plus-original-regressions.py')] if configured else [str(HERE/'candidate03-frozen-plus-original-regressions.py'),'-k','unconfigured_authority']
            report='candidate03-independent-junit.xml' if configured else 'candidate03-unconfigured-junit.xml'
            code=pytest.main(files+['-q','--assert=plain','--junitxml='+str(HERE/report)])
            assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':len(manifest['sources']),'candidate_suite_cases':38,'additional_original_regressions':3,'original_defects_closed':True,'actual_postgresql_nonowner':True,'model_transport':'explicit native DeepSeekClient MockTransport','object_port':'explicit in-memory','paid_calls':0,'credentials_read':False,'source_module_directory':str(BASE/'frozen-inputs'),'loaded_frozen_modules':{m.__name__:str(m.__file__) for m in loaded},'reference_inputs_verified':len(manifest['reference_inputs'])}
    result['source_authority_installed']=configured
    if not configured:
        result['candidate_suite_cases']=1;result['additional_original_regressions']=0;result['original_defects_closed']='not_evaluated_in_unconfigured_mode'
    name='candidate03-independent-result.json' if configured else 'candidate03-unconfigured-result.json'
    (HERE/name).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'configured':configured,'candidate_suite_cases':38 if configured else 1,'original_regressions_closed':3 if configured else 0,'paid_calls':0}))
if __name__=='__main__':main()
