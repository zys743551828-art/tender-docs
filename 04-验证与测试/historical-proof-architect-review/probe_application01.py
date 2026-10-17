"""Frozen historical proof component; approved fresh nonowner profiles, no model/object ports."""
import os,sys,json,hashlib,importlib
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'research-backend-checks/historical-authority/candidate-01';FROZEN=BASE/'frozen-inputs';REFS=BASE/'reference-inputs'
sys.path.insert(0,str(APP))
manifest=BASE/'historical-proof-candidate-01.json';assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='67169daa361ee5017357dd4759035b84b6e4811187e00dab3824eed11e05af95';m=json.loads(manifest.read_text())
for section,root in [('sources',FROZEN),('reference_inputs',REFS)]:
 for entry in m[section]:assert hashlib.sha256((root/entry['path']).read_bytes()).hexdigest()==entry['sha256']
for key in list(os.environ):
 if any(x in key for x in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
os.environ.update(RUN_HISTORICAL_PROOF_INTEGRATION='1',RUN_RESEARCH_INTEGRATION='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
for name in ('apps.api','packages.domain','packages.research','packages.identity','packages.model_governance','packages.rag','tests'):
 mod=importlib.import_module(name);rel=name.replace('.','/');mod.__path__=[str(FROZEN/rel),str(REFS/rel),*mod.__path__]
from packages.domain import runtime,storage
runtime.ROOT=APP
from packages.model_governance import historical_repository as repo
from apps.api import historical_routes
assert Path(repo.__file__).is_relative_to(FROZEN)
runtime.assert_role()
source=(FROZEN/'tests/test_model_governance_historical_integration.py').read_text()
source+=r'''


def test_architect_RR_confirmation_replay_accepts_withdrawn_proof_counterexample(client,monkeypatch):
    from contextlib import contextmanager
    from packages.rag.indexing import original_source_id
    from apps.api import historical_routes
    from packages.model_governance import historical_repository
    from pathlib import Path
    import json
    web,_,headers,_=client
    item,proof,projection=fixture(client)
    pending=save_subject(client,item,proof,projection)
    assert pending.status_code==201,pending.text
    identity=pending.json()['id'];path=f'/api/v1/historical-proof/subjects/{identity}/confirm'
    confirm_body={'reason':'合成独立RR重放来源撤回反例'}
    stable=headers(pending.headers['etag'])
    confirmed=web.post(path,json=confirm_body,headers=stable)
    assert confirmed.status_code==200,confirmed.text
    with connection() as stale:
        stale.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        stale.execute('SELECT count(*) FROM historical_backtest.subjects').fetchone()
        with connection() as writer:
            writer.execute('SELECT pg_advisory_xact_lock(90127,1)')
            writer.execute("UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",(original_source_id(proof['document_version_id']),))
        @contextmanager
        def old_snapshot():yield stale
        with monkeypatch.context() as scoped:
            scoped.setattr(historical_routes,'connection',old_snapshot)
            replay=web.post(path,json=confirm_body,headers=stable)
        assert replay.status_code==200,replay.text
        assert replay.headers.get('Idempotency-Replayed')=='true'
        assert replay.json()['proofs_current'] is True
    with connection() as fresh:
        value,_=historical_repository.view(fresh,'subject',identity)
        assert value['proofs_current'] is False
        status=fresh.execute('SELECT status FROM retrieval.source_heads WHERE source_id=%s',(original_source_id(proof['document_version_id']),)).fetchone()['status']
        assert status=='withdrawn'
    evidence={'reproduced':True,'mode':'actual nonowner PostgreSQL+real session/CSRF ASGI, synthetic domain text; no external ports','stale_isolation':'repeatable read','actual_source_status':status,'RR_replay_status':replay.status_code,'RR_replay_proofs_current':True,'RC_proofs_current':False,'formal_eligibility':'unavailable','paid_calls':0}
    Path('/workspace/tender-docs/04-验证与测试/historical-proof-architect-review/application01-RR-replay-counterexample.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
'''
(HERE/'application01-frozen-plus-delta-tests.py').write_text(source)
import pytest
mode=sys.argv[1] if len(sys.argv)>1 else 'counterexample'
paths=[str(HERE/'application01-frozen-plus-delta-tests.py')]
args=['-q','--assert=plain','--tb=short']
if mode=='counterexample':args+=['-k','architect_RR_confirmation']
else:
 paths += [str(FROZEN/'tests/test_model_governance_historical_requests.py'),str(FROZEN/'tests/test_model_governance_historical_contracts.py')];args+=['-k','not architect_RR_confirmation']
def forbidden(*a,**k):raise AssertionError('external object port forbidden in architect proof probe')
with patch.object(storage,'client',forbidden),patch.object(storage,'get',forbidden),patch.object(storage,'put',forbidden):
 code=pytest.main([*paths,*args,'--junitxml='+str(HERE/('application01-'+mode+'-junit.xml'))]);assert code==0
result={'candidate_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'sources_verified':len(m['sources']),'references_verified':len(m['reference_inputs']),'loaded_source':{'repo':repo.__file__,'routes':historical_routes.__file__},'mode':mode,'actual_postgresql_nonowner':True,'real_auth_ASGI':True,'domain_fixture':'explicit synthetic SQL offline submission and proof, not real upload','paid_calls':0,'object_requests':0,'consumers_started':False}
(HERE/('application01-'+mode+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
