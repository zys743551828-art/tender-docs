"""HIST02 original scenario against the complete frozen02 runtime; nonowner DB only."""
import os,sys,json,hashlib,importlib
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=HERE.parent/'research-backend-checks/historical-authority/candidate-02'
p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='c69aaf845c23db74b794a4e2b20e3bf78a9bf86a9d3d12202fdd5ca33ee91876';m=json.loads(p.read_text());ROOT=Path(m['runtime_root'])
for e in m['sources']:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
sys.path.insert(0,str(ROOT))
for key in list(os.environ):
 if any(x in key for x in ('DEEPSEEK','ACCESS_KEY','SECRET_KEY')) or key=='OWNER_DATABASE_DSN':os.environ.pop(key,None)
os.environ.update(RUN_HISTORICAL_PROOF_INTEGRATION='1',RUN_RESEARCH_INTEGRATION='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
# Frozen runtime is the public code root; explicit selected profile suppresses .env fallback.
from packages.domain import runtime,storage
runtime.ROOT=ROOT
runtime.assert_role()
from packages.model_governance import historical_repository as repo
from apps.api import historical_routes
source=(ROOT/'tests/test_model_governance_historical_integration.py').read_text()+r'''


def test_architect_original_RR_replay_rejects_while_fresh_RC_replay_tracks_withdrawal(client,monkeypatch):
    from contextlib import contextmanager
    from packages.rag.indexing import original_source_id
    from apps.api import historical_routes
    from packages.model_governance import historical_repository
    from pathlib import Path
    import json
    web,_,headers,_=client
    item,proof,projection=fixture(client)
    pending=save_subject(client,item,proof,projection);assert pending.status_code==201,pending.text
    identity=pending.json()['id'];path=f'/api/v1/historical-proof/subjects/{identity}/confirm'
    body={'reason':'合成独立RR重放来源撤回反例'};stable=headers(pending.headers['etag'])
    first=web.post(path,json=body,headers=stable);assert first.status_code==200,first.text
    healthy=web.post(path,json=body,headers=stable);assert healthy.status_code==200 and healthy.headers['Idempotency-Replayed']=='true'
    with connection() as current:
        before=current.execute("SELECT count(*) AS n FROM historical_backtest.decisions WHERE object_id=%s",(identity,)).fetchone()['n']
    with connection() as stale:
        stale.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');stale.execute('SELECT count(*) FROM historical_backtest.subjects').fetchone()
        with connection() as writer:
            writer.execute('SELECT pg_advisory_xact_lock(90127,1)')
            writer.execute("UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",(original_source_id(proof['document_version_id']),))
        @contextmanager
        def old_snapshot():yield stale
        with monkeypatch.context() as scoped:
            scoped.setattr(historical_routes,'connection',old_snapshot)
            refused=web.post(path,json=body,headers=stable)
        assert refused.status_code==409,refused.text
        assert refused.json()['code']=='HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED'
    fresh=web.post(path,json=body,headers=stable)
    assert fresh.status_code==409 and fresh.json()['code']=='HISTORICAL_AUTHORITY_STALE',fresh.text
    with connection() as current:
        value,_=historical_repository.view(current,'subject',identity);assert value['proofs_current'] is False
        after=current.execute("SELECT count(*) AS n FROM historical_backtest.decisions WHERE object_id=%s",(identity,)).fetchone()['n'];assert after==before
    evidence={'candidate02':True,'actual_nonowner_PG_ASGI':True,'synthetic_fixture':True,'healthy_RC_replay':healthy.status_code,'withdrawn_RR_replay':refused.status_code,'withdrawn_RC_replay':fresh.status_code,'decisions_before':before,'decisions_after':after,'paid_calls':0,'object_requests':0}
    Path('/workspace/tender-docs/04-验证与测试/historical-proof-architect-review/application02-original-scenario.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
'''
(HERE/'application02-frozen-plus-delta-tests.py').write_text(source)
import pytest
def forbidden(*a,**k):raise AssertionError('external object port forbidden in architect proof probe')
with patch.object(storage,'client',forbidden),patch.object(storage,'get',forbidden),patch.object(storage,'put',forbidden):
 code=pytest.main([str(HERE/'application02-frozen-plus-delta-tests.py'),str(ROOT/'tests/test_model_governance_historical_requests.py'),str(ROOT/'tests/test_model_governance_historical_contracts.py'),'-q','--assert=plain','--tb=short','--junitxml='+str(HERE/'application02-independent-junit.xml')]);assert code==0
for e in m['sources']:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
(HERE/'application02-independent-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'runtime_files_verified':len(m['sources']),'reverified_after_execution':True,'actual_PG_nonowner':True,'real_session_CSRF_ASGI':True,'synthetic_SQL_proofs_not_real_upload':True,'paid_calls':0,'object_requests':0,'consumers_started':False,'loaded_source':repo.__file__},ensure_ascii=False,indent=2)+'\n')
