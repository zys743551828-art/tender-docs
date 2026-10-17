"""普通模型账本独立PG与MockTransport验证；屏蔽dotenv，不收费、不连对象。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Barrier
from unittest.mock import patch

import httpx

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
sys.path.insert(0,str(APP))
sys.path.insert(0,str(HERE.parent/'rag-architect-review'))
from isolated_pg import database


def main():
    record=json.loads((HERE/'candidate01-inputs.json').read_text())
    def verify_sources():
        assert all(hashlib.sha256((APP/item['path']).read_bytes()).hexdigest()==item['sha256'] for item in record['sources'])
    verify_sources()
    original=Path.read_text
    def no_private(path,*args,**kwargs):
        return '' if path.resolve()==APP/'.env' else original(path,*args,**kwargs)
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api_dsn=c.info.dsn
        with connect() as c:worker_dsn=c.info.dsn
        environment={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api_dsn,'WORKER_DATABASE_DSN':worker_dsn,'RUN_MODEL_LEDGER_INTEGRATION':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        with patch.dict(os.environ,environment,clear=True),patch.object(Path,'read_text',no_private):
            import pytest
            exit_code=pytest.main([str(APP/'tests/test_model_ledger_integration.py'),'--assert=plain','-q','--junitxml='+str(HERE/'candidate01-independent-tests.xml')])
            assert exit_code==0
            spec=importlib.util.spec_from_file_location('architect_ledger_cases',APP/'tests/test_model_ledger_integration.py')
            cases=importlib.util.module_from_spec(spec);spec.loader.exec_module(cases)
            checks=[]
            with cases.executions() as ((message,lease),):
                barrier=Barrier(2)
                def one(_):barrier.wait();return cases.reserve(message,lease)
                with ThreadPoolExecutor(max_workers=2) as pool:answers=list(pool.map(one,range(2)))
                assert sum(int(created) for _,created in answers)==1
                assert answers[0][0]['id']==answers[1][0]['id']
                checks.append('two concurrent same-key attempts create one reservation')
            for label,existing,max_tokens,expected_count in [('calls',9,32,10),('tokens',3,8192,4)]:
                with cases.executions(2) as ((first,first_lease),(second,second_lease)):
                    for i in range(existing):cases.reserve(first,first_lease,'existing-'+str(i),max_tokens)
                    barrier=Barrier(2)
                    def attempt(item):
                        message,lease,key=item;barrier.wait()
                        try:return cases.reserve(message,lease,key,max_tokens)[1]
                        except cases.DomainError as error:return error.code
                    with ThreadPoolExecutor(max_workers=2) as pool:answers=list(pool.map(attempt,[(first,first_lease,'race-first'),(second,second_lease,'race-second')]))
                    assert answers.count(True)==1 and answers.count('MODEL_BUDGET_EXHAUSTED')==1
                    with cases.connection(True) as c:
                        count=c.execute('SELECT count(*) AS n FROM model_calls WHERE operation_id IN (%s,%s)',(first['operation_id'],second['operation_id'])).fetchone()['n']
                    assert count==expected_count
                    checks.append('concurrent shared-command '+label+' budget does not oversubscribe')
            seen=[];objects={}
            with cases.executions() as ((message,lease),):
                def handler(request):
                    seen.append(True)
                    with cases.connection(True) as c:c.execute("UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",(message['operation_id'],))
                    cases.engine.reconcile(once=True)
                    return httpx.Response(200,json={'id':'late-response-fixture','model':'deepseek-flash','choices':[{'message':{'role':'assistant','content':'{"ok":true}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':4,'total_tokens':6}})
                client=cases.DeepSeekClient(api_key='explicit-fixture-only',transport=httpx.MockTransport(handler))
                with patch.object(cases.DeepSeekClient,'from_env',classmethod(lambda cls:client)),patch.object(cases.storage,'put',lambda key,data,media:objects.__setitem__(key,data)),patch.object(cases.storage,'get',lambda key:objects[key]):
                    value,call=cases.model_calls.generate(message,lease,logical_key='late-cancelled-result',messages=[{'role':'user','content':'fixture'}],json_schema=cases.SCHEMA)
                assert len(seen)==1 and value['usage']['total_tokens']==6
                assert cases.engine.finish(message,lease,{}) is False
                with cases.connection(True) as c:
                    state=c.execute('SELECT state,usage FROM model_calls WHERE id=%s',(call,)).fetchone()
                    assert state['state']=='succeeded' and state['usage']['actual']['total_tokens']==6
                    assert c.execute('SELECT state FROM operations WHERE id=%s',(message['operation_id'],)).fetchone()['state']=='cancelled'
                    assert c.execute('SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s',(message['operation_id'],)).fetchone()['n']==0
                checks.append('late response preserves billing truth after cancellation but old fence cannot publish effects')
    verify_sources()
    output={'scope':'candidate01 ordinary ledger with isolated real PG/nonowner roles and explicit MockTransport/in-memory objects','candidate_sha256':record['candidate_sha256'],'developer_cases_independently_run':6,'additional_concurrent_and_cancel_checks':checks,'source_hashes_unchanged_before_after':True,'credentials_read':False,'paid_calls':0,'real_object_requests':0,'Kafka_or_model_efficacy_accepted':False}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps({'developer_cases':6,'additional_checks':len(checks),'passed':True,'paid_calls':0}))


if __name__=='__main__':main()
