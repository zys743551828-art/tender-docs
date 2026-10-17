"""FR011 frozen module real isolated nonowner PG/ASGI; no external I/O."""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=APP/'docs/metrics-development-checks/candidate02';FROZEN=BASE/'frozen-inputs'
sys.path[:0]=[str(FROZEN),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'candidate.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='654c85600842d21e12f3032be0c360eb74a8f3ad359d0af08de945874bfd83a9'
    manifest=json.loads(cand.read_text())
    for rel,d in manifest['sources'].items():assert hashlib.sha256((FROZEN/rel).read_bytes()).hexdigest()==d
    observed={}
    for rel,d in manifest['observed_dependencies'].items():
        current=hashlib.sha256((APP/rel).read_bytes()).hexdigest();observed[rel]={'expected':d,'actual':current}
        assert current==d
    source=(FROZEN/'tests/test_metrics_integration.py').read_text()
    source+=r'''


def test_architect_historical_source_current_publication_wrongly_uses_business_known_cutoff(context):
    from pathlib import Path
    from datetime import datetime
    import json
    client,actor,headers=context
    with connection() as c:
        p=make_project(c,actor)
        c.execute('SELECT pg_advisory_xact_lock(90127,1)')
        c.execute("UPDATE retrieval.source_heads SET valid_from=clock_timestamp(),revision=revision+1 WHERE source_id IN(SELECT id FROM retrieval.source_nodes WHERE document_version_id=%s)",(p['official'],))
    body=payload(p,'awarded',True)
    created=client.post(endpoint(p),json=body,headers=hdr(headers,p['submission']))
    assert created.status_code==201
    rid=created.json()['id']
    response=client.post(f'/api/v1/business-results/{rid}/verification-decisions',json=verification_payload(p),headers=hdr(headers,p['submission'],1))
    assert response.status_code==409 and response.json()['details']['reasons']==['source_no_longer_effective'],response.text
    with connection() as c:
        row=c.execute("SELECT min(n.known_at) AS known,min(h.valid_from) AS valid,clock_timestamp() AS observed FROM retrieval.source_nodes n JOIN retrieval.source_heads h ON h.source_id=n.id WHERE n.document_version_id=%s",(p['official'],)).fetchone()
    assert row['known']<=datetime.fromisoformat(body['known_at'])<row['valid']<=row['observed']
    Path(os.environ['ARCHITECT_METRICS_OUT']).joinpath('candidate02-historical-publication-counterexample.json').write_text(json.dumps({'reproduced':True,'source_known_at':row['known'].isoformat(),'business_known_at':body['known_at'],'system_publication_valid_from':row['valid'].isoformat(),'verification_observed_at':row['observed'].isoformat(),'expected':'can_verify_current_published_historical_source','actual_status':409,'reason':'source_no_longer_effective','paid_calls':0},indent=2))
'''  
    (HERE/'candidate02-historical-test.py').write_text(source)
    original=Path.read_text
    def private_off(path,*a,**kw):return '' if path.resolve()==APP/'.env' else original(path,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'RUN_METRICS_INTEGRATION':'1','APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'explicit-synthetic','CSRF_SECRET':'explicit-synthetic','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','ARCHITECT_METRICS_OUT':str(HERE)}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            import apps.api
            apps.api.__path__=[str(FROZEN/'apps/api'),*apps.api.__path__]
            from apps.api import metrics_routes
            from packages.metrics import results,views,predictions,snapshots,core,records
            loaded=[metrics_routes,results,views,predictions,snapshots,core,records]
            for module in loaded:
                rel=str(Path(module.__file__).relative_to(FROZEN));assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==manifest['sources'][rel]
            import pytest
            code=pytest.main([str(FROZEN/'tests/test_metrics.py'),str(HERE/'candidate02-historical-test.py'),'-k','architect_historical_source_current','-q','--assert=plain','--junitxml='+str(HERE/'candidate02-historical-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':13,'dependencies_observed':observed,'candidate_cases':0,'independent_historical_counterexample':1,'actual_postgresql_nonowner':True,'actual_login_asgi':True,'paid_calls':0,'cloud_requests':0,'consumers_started':False,'private_config_read':False,'loaded_modules':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'candidate02-historical-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
