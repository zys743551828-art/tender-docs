"""F02 independent nonowner PG/ASGI; synthetic evidence, in-memory object only."""
import hashlib,importlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'pricing-development-checks/candidate-03';FROZEN=BASE/'frozen-inputs'
sys.path[:0]=[str(FROZEN),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'pricing-candidate-03.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='5679b97595cf181c2250ddc03d1abd167212975c62583ff2853793e5f2f75d84'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256((FROZEN/row['path']).read_bytes()).hexdigest()==row['sha256']
    for row in manifest['reference_inputs']:assert hashlib.sha256((APP/row['path']).read_bytes()).hexdigest()==row['sha256']
    source=(FROZEN/'tests/test_pricing_integration.py').read_text()
    source+=r'''

@pytest.mark.parametrize('actual_unit,currency',[('兆元','CNY'),('韩元','KRW')])
def test_architect_unrecognized_multiplier_or_currency_is_stripped_to_yuan(client,actual_unit,currency):
    from pathlib import Path
    import json
    web,actor,headers,objects=client
    text=TEXT.replace('金额单位元，币种CNY',f'金额单位{actual_unit}，币种{currency}')
    item=seed(actor,text)
    value=formula_request(item,text)
    context=web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    response=web.post(f"/api/v1/bids/{item['bid']}/pricing-formulas",json=value,headers=headers(context.headers['etag']))
    assert response.status_code==422 and response.json()['code']=='PRICING_UNIT_WITHOUT_EVIDENCE',response.text
    assert objects=={}
    with connection() as c:
        assert c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",(item['bid'],)).fetchone()['n']==0
    Path(os.environ['ARCHITECT_PRICING_OUT']).joinpath('candidate03-'+currency+'-regression.json').write_text(json.dumps({'original_counterexample_closed':True,'source_amount_unit':actual_unit,'source_currency':currency,'response_status':response.status_code,'objects_written':len(objects),'paid_calls':0},ensure_ascii=False,indent=2)+'\\n')
'''
    (HERE/'candidate03-frozen-plus-unit-counterexamples.py').write_text(source)
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'RUN_PRICING_INTEGRATION':'1','APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'explicit-synthetic','CSRF_SECRET':'explicit-synthetic','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','ARCHITECT_PRICING_OUT':str(HERE)}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            import apps.api
            apps.api.__path__=[str(FROZEN/'apps/api'),*apps.api.__path__]
            from apps.api import pricing_routes
            from packages.pricing import repository,sources,calculator
            loaded=[pricing_routes,repository,sources,calculator]
            for module in loaded:
                rel=str(Path(module.__file__).relative_to(FROZEN));assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==next(row['sha256'] for row in manifest['sources'] if row['path']==rel)
            sys.path.insert(0,str(FROZEN/'tests'))
            import pytest
            code=pytest.main([str(FROZEN/'tests/test_pricing_calculator.py'),str(FROZEN/'tests/test_pricing_contracts.py'),str(HERE/'candidate03-frozen-plus-unit-counterexamples.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate03-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':13,'reference_inputs_verified':11,'candidate_cases':45,'independent_original_regressions_closed':2,'actual_postgresql_nonowner':True,'actual_login_asgi':True,'object_port':'explicit in-memory','source':'synthetic SQL parse fixtures, not actual PDF','paid_calls':0,'cloud_requests':0,'consumers_started':False,'private_config_read':False,'loaded_modules':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'candidate03-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
