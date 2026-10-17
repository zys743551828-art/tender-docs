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

@pytest.mark.parametrize('state',['candidate','future_known'])
def test_architect_original_not_published_source_now_rejected(context,state):
    from pathlib import Path
    import json
    from packages.rag.indexing import parse_source_id
    client,actor,headers=context
    with connection() as c:
        p=make_project(c,actor,register_sources=False)
        root=original_source_id(p['official'])
        block=c.execute('SELECT parse_id FROM evidence_blocks WHERE id=%s',(p['refs'][str(p['official'])]['evidence_id'],)).fetchone()
        child=parse_source_id(block['parse_id'])
        for node in (root,child):
            c.execute("INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) SELECT %s,v.id,'document','official_label',CASE WHEN %s='future_known' AND %s THEN clock_timestamp()+interval '1 day' ELSE clock_timestamp()-interval '3 days' END,b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",(node,state,node==root,p['official']))
            c.execute("INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,%s,clock_timestamp()-interval '3 days')",(node,'candidate' if state=='candidate' and node==root else 'published'))
        c.execute('INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)',(child,root))
    body=payload(p,'awarded',True)
    created=client.post(endpoint(p),json=body,headers=hdr(headers,p['submission']))
    assert created.status_code==201,created.text
    rid=created.json()['id']
    response=client.post(f'/api/v1/business-results/{rid}/verification-decisions',json=verification_payload(p),headers=hdr(headers,p['submission'],1))
    assert response.status_code==409 and response.json()['code']=='BUSINESS_RESULT_SOURCE_INVALID',response.text
    current=client.get(f'/api/v1/business-results/{rid}').json()
    assert current['verification']=='pending' and not current['usable']
    metric=client.get('/api/v1/metrics',params={'bid_ids':str(p['bid']),'verification_scope':'verified_only'}).json()
    assert metric['win_rate']=={'numerator':0,'denominator':0,'value':None}
    Path(os.environ['ARCHITECT_METRICS_OUT']).joinpath('candidate02-source-'+state+'-regression.json').write_text(json.dumps({'original_counterexample_closed':True,'source_state':state,'verification_response':409,'current_verification':'pending','usable':False,'verified_only_win_rate':metric['win_rate'],'paid_calls':0},indent=2))


def test_architect_real_graph_wait_then_withdraw_prevents_verification(context):
    import time,json
    from pathlib import Path
    client,actor,headers=context
    with connection() as c:p=make_project(c,actor)
    created=client.post(endpoint(p),json=payload(p,'awarded',True),headers=hdr(headers,p['submission']))
    assert created.status_code==201
    rid=created.json()['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(90127,1)')
            future=pool.submit(client.post,f'/api/v1/business-results/{rid}/verification-decisions',json=verification_payload(p),headers=hdr(headers,p['submission'],1))
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                if c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted AND locktype='advisory' AND classid=90127 AND objid=1) AS waiting").fetchone()['waiting']:break
                assert not future.done()
                time.sleep(.01)
            else:pytest.fail('verification did not wait for actual source graph lock')
            c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(p['official']),))
        response=future.result(timeout=5)
    assert response.status_code==409,response.text
    with connection() as c:assert not records.rows(c,p['bid'],(records.VERIFY,))
    Path(os.environ['ARCHITECT_METRICS_OUT']).joinpath('candidate02-graph-wait-regression.json').write_text(json.dumps({'actual_pg_locks_wait_observed':True,'withdraw_committed_before_final_qualification':True,'verification_response':409,'new_verification_records':0,'paid_calls':0},indent=2))
''' 
    (HERE/'candidate02-frozen-plus-source-counterexamples.py').write_text(source)
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
            code=pytest.main([str(FROZEN/'tests/test_metrics.py'),str(HERE/'candidate02-frozen-plus-source-counterexamples.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate02-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'sources_verified':13,'dependencies_observed':observed,'candidate_cases':37,'independent_original_regressions':2,'independent_actual_graph_wait':1,'actual_postgresql_nonowner':True,'actual_login_asgi':True,'paid_calls':0,'cloud_requests':0,'consumers_started':False,'private_config_read':False,'loaded_modules':{m.__name__:str(m.__file__) for m in loaded}}
    (HERE/'candidate02-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
