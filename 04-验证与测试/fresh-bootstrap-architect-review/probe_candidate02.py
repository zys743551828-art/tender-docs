"""Frozen helper offline regression plus actual bootstrap-flow LOGIN mismatch rejection."""
from pathlib import Path
import importlib.util,sys,os,json,hashlib
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');BASE=APP/'docs/fresh-combination-development-checks/candidate02';FROZEN=BASE/'frozen-inputs'
sys.path.insert(0,str(APP))
manifest=BASE/'candidate.json';assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='e3cdce1f6a30e419c989b7a7e4438ec5d39d8d3336422133118a48d779d275dd';m=json.loads(manifest.read_text())
for rel,d in m['sources'].items():assert hashlib.sha256((FROZEN/rel).read_bytes()).hexdigest()==d
for rel,d in m['observed_dependencies'].items():assert hashlib.sha256((APP/rel).read_bytes()).hexdigest()==d
import deploy
spec=importlib.util.spec_from_file_location('deploy.bootstrap_fresh',FROZEN/'deploy/bootstrap_fresh.py');fresh=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fresh;deploy.bootstrap_fresh=fresh;spec.loader.exec_module(fresh)
src=(FROZEN/'tests/test_bootstrap_fresh.py').read_text()+r'''

@pytest.mark.parametrize('rejected_role',['api','worker','research_worker','score_read','model_train','model_evaluate'])
def test_architect_every_new_LOGIN_is_checked_before_emitting_business_profiles(tmp_path,monkeypatch,rejected_role):
    from psycopg.conninfo import conninfo_to_dict
    from contextlib import nullcontext
    monkeypatch.setattr(fresh,'ROOT',tmp_path)
    monkeypatch.setattr(fresh,'sql_plan',lambda _: (fresh.ReviewedSql('deploy/sql/model-governance.sql','a'*64,b'SELECT 1'),))
    profile=tmp_path/'base.env'
    fresh.write_private(profile,{'OWNER_DATABASE_DSN':'postgresql://synthetic_owner:synthetic@127.0.0.1:1/seed','OBJECT_STORE_PREFIX':'synthetic/'})
    seen=[]
    class Fake:
        def __init__(self,fields):self.fields=fields
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def transaction(self):return nullcontext()
        def execute(self,statement,params=()):
            query=str(statement);rows=[]
            if 'current_database(),current_user' in query:
                user=self.fields['user']
                for role in sorted((*fresh.CAPABILITIES,*fresh.PURPOSES),key=len,reverse=True):
                    if user.endswith('_'+role):
                        seen.append(role)
                        if role==rejected_role:user='synthetic_wrong_user'
                        break
                rows=[(self.fields['dbname'],user)]
            elif 'rolcanlogin' in query:rows=[(g,False,False,False,False,False,False) for g in (*fresh.GROUPS,'tender_model_train','tender_model_evaluate','tender_model_score')]
            elif 'SELECT rolsuper' in query:rows=[(False,False,False,False,False)]
            elif 'SELECT EXISTS' in query:rows=[(False,)]
            return type('Result',(),{'fetchone':lambda self:rows[0] if rows else None,'fetchall':lambda self:rows})()
    monkeypatch.setattr(fresh.psycopg,'connect',lambda dsn,**kw:Fake(conninfo_to_dict(dsn)))
    with pytest.raises(fresh.BootstrapError,match='CONNECTED_DATABASE_OR_USER_MISMATCH'):
        fresh.bootstrap(base_profile=profile,name='all_login_guard',model_purposes=True)
    assert rejected_role in seen
    assert not (tmp_path/'.local/all_login_guard-checks.env').exists()
    assert not list((tmp_path/'.local').glob('*-model_*.env'))
'''
(HERE/'candidate02-frozen-plus-delta-tests.py').write_text(src)
import pytest
with patch.dict(os.environ,{'PATH':os.environ['PATH'],'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},clear=True):
 code=pytest.main([str(HERE/'candidate02-frozen-plus-delta-tests.py'),'-q','--assert=plain','--junitxml='+str(HERE/'candidate02-independent-junit.xml')]);assert code==0
result={'candidate_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'sources_verified':len(m['sources']),'dependencies_verified':len(m['observed_dependencies']),'loaded_source':str(fresh.__file__),'mode':'offline mocks and real libpq parser; no database connections or DDL','paid_calls':0,'private_config_read':False,'new_environment_created':False}
(HERE/'candidate02-independent-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
