"""身份模块冻结候选，隔离受限PG/ASGI；无收费或外部对象。"""
from pathlib import Path
import hashlib,json,os,sys
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'identity-development-checks/candidate-01'
sys.path[:0]=[str(BASE/'frozen-inputs'),str(APP),str(HERE.parent/'rag-architect-review')]
from isolated_pg import database

def main():
    cand=BASE/'candidate.json';assert hashlib.sha256(cand.read_bytes()).hexdigest()=='ff7156ad56f19eab2a1b8041e318efa0e93f0081e4c06befaf723a86305c3b7e'
    manifest=json.loads(cand.read_text())
    for row in manifest['sources']:assert hashlib.sha256(Path(row['frozen_copy']).read_bytes()).hexdigest()==row['sha256']
    source=(BASE/'frozen-inputs/tests/test_identity.py').read_text()
    source+='''\n\ndef test_architect_delayed_transaction_default_is_not_latest(env):
    from pathlib import Path
    import json
    with connection() as delayed:
        delayed.execute('SELECT transaction_timestamp()')
        with connection() as other:
            other.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('identity-default:'+str(env['actor']),))
            identities.set_default(other,env['actor'],env['company']['id'],None)
        delayed.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('identity-default:'+str(env['actor']),))
        response=identities.set_default(delayed,env['actor'],env['other']['id'],None)
    with connection() as c:actual=identities.default_bidder(c,env['actor'])
    assert response['bidder']['id']==env['company']['id'] and actual['bidder']['id']==env['company']['id']
    Path(os.environ['ARCHITECT_IDENTITY_OUT']).joinpath('default-order-counterexample.json').write_text(json.dumps({'reproduced':True,'last_explicit_choice_returned':False,'scope':'API-role delayed transaction; identical per-member advisory-lock ordering; no concurrent conflicting writes'},indent=2)+'\\n')


def test_architect_later_correction_appears_in_earlier_as_of(env):
    from pathlib import Path
    import json
    with connection() as c:bid=new_project(c,env,{'bidder_id':env['company']['id']})
    with connection() as delayed:
        started=delayed.execute('SELECT transaction_timestamp() AS at').fetchone()['at']
        with connection() as witness:cutoff=witness.execute('SELECT clock_timestamp() AS at').fetchone()['at']
        delayed.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(bid,))
        fixed=identities.correct(delayed,bid,env['actor'],None,{'bidder_id':env['other']['id'],'reason':'合成延迟事务：在冻结时点之后才取锁更正'})
    with connection() as c:historical=identities.at_time(c,bid,cutoff)
    assert historical['bidder_id']==env['other']['id'] and cutoff>started
    Path(os.environ['ARCHITECT_IDENTITY_OUT']).joinpath('historical-time-counterexample.json').write_text(json.dumps({'reproduced':True,'correction_lock_acquired_after_cutoff':True,'as_of_resolves_later_correction':True,'scope':'API-role delayed transaction; no production data'},indent=2)+'\\n')
'''
    (HERE/'frozen-plus-independent-tests.py').write_text(source)
    orig=Path.read_text
    def private_off(p,*a,**kw):return '' if p.resolve()==APP/'.env' else orig(p,*a,**kw)
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api=c.info.dsn
        with connect() as c:worker=c.info.dsn
        env={'PATH':os.environ['PATH'],'API_DATABASE_DSN':api,'WORKER_DATABASE_DSN':worker,'APP_ORIGIN':'http://127.0.0.1:5175','SESSION_SECRET':'synthetic-identity-only','CSRF_SECRET':'synthetic-csrf-only','RUN_IDENTITY_PG':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','ARCHITECT_IDENTITY_OUT':str(HERE)}
        with patch.dict(os.environ,env,clear=True),patch.object(Path,'read_text',private_off):
            from packages.identity import repository,contracts
            from apps.api import identity_routes
            expected={Path(row['path']).relative_to(APP).as_posix():row['sha256'] for row in manifest['sources']}
            for mod in [repository,contracts,identity_routes]:assert hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()==expected[mod.__name__.replace('.','/')+'.py']
            import pytest
            code=pytest.main([str(HERE/'frozen-plus-independent-tests.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate01-independent-junit.xml')]);assert code==0
    result={'candidate_sha256':hashlib.sha256(cand.read_bytes()).hexdigest(),'original_cases_passed':8,'additional_counterexamples_reproduced':2,'actual_nonowner_postgresql':True,'actual_login_asgi':True,'paid_calls':0,'external_requests':0,'credentials_read':False}
    (HERE/'candidate01-independent-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
