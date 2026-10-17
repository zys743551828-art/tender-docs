"""FR011 frozen module real isolated nonowner PG/ASGI; no external I/O."""
import hashlib,json,os,sys
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=APP/'docs/metrics-development-checks';FROZEN=BASE/'frozen-inputs'
sys.path[:0]=[str(FROZEN),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'candidate.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='740266ccd1b9aff91adf96414806dbabf6c6f66f66289c0d9f3f96a47bd5e27a'
    manifest=json.loads(cand.read_text())
    for rel,d in manifest['sources'].items():assert hashlib.sha256((FROZEN/rel).read_bytes()).hexdigest()==d
    observed={}
    for rel,d in manifest['observed_dependencies'].items():
        current=hashlib.sha256((APP/rel).read_bytes()).hexdigest();observed[rel]={'expected':d,'actual':current}
        assert current==d
    source=(FROZEN/'tests/test_metrics_integration.py').read_text()
    source+=r'''

@pytest.mark.parametrize('state',['candidate','future_known'])
def test_architect_not_published_official_source_is_verified_and_counted(context,state):
    from pathlib import Path
    import json
    client,actor,headers=context
    with connection() as c:
        p=make_project(c,actor)
        from packages.rag.indexing import original_source_id
        root=original_source_id(p['official'])
        c.execute("INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) SELECT %s,v.id,'document','official_label',CASE WHEN %s='future_known' THEN clock_timestamp()+interval '1 day' ELSE clock_timestamp() END,b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",(root,state,p['official']))
        c.execute("INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,%s,CASE WHEN %s='missing_valid_from' THEN NULL ELSE clock_timestamp() END)",(root,'candidate' if state=='candidate' else 'published',state))
    body=payload(p,'awarded',True)
    created=client.post(endpoint(p),json=body,headers=hdr(headers,p['submission']))
    assert created.status_code==201,created.text
    rid=created.json()['id']
    proof={'state':'verified','reason':'架构合成原页核验','identity_confirmed':True,'evidence_refs':body['evidence_refs']}
    verified=client.post(f'/api/v1/business-results/{rid}/verification-decisions',json=proof,headers=hdr(headers,p['submission'],1))
    assert verified.status_code==200 and verified.json()['verification']=='verified' and verified.json()['usable'] is True,verified.text
    value=client.get('/api/v1/metrics',params={'bid_ids':str(p['bid']),'verification_scope':'verified_only'}).json()
    assert value['win_rate']=={'numerator':1,'denominator':1,'value':'1'}
    Path(os.environ['ARCHITECT_METRICS_OUT']).joinpath('candidate01-source-'+state+'-counterexample.json').write_text(json.dumps({'reproduced':True,'source_state':state,'verification_response':verified.status_code,'verification':'verified','usable':True,'verified_only_win_rate':value['win_rate'],'paid_calls':0},indent=2)+'\n')
'''
    (HERE/'candidate01-frozen-plus-source-counterexamples.py').write_text(source)
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
            code=pytest.main([str(FROZEN/'tests/test_metrics.py'),str(HERE/'candidate01-frozen-plus-source-counterexamples.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate01-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':12,'dependencies_observed':observed,'candidate_cases':19,'independent_counterexamples_reproduced':2,'actual_postgresql_nonowner':True,'actual_login_asgi':True,'paid_calls':0,'cloud_requests':0,'consumers_started':False,'private_config_read':False,'loaded_modules':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
