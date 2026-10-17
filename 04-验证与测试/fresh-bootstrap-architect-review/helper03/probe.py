"""Exact bootstrap03 offline checks only. All connect paths forbidden unless explicit test Fake."""
from pathlib import Path
import sys,os,json,hashlib
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;ROOT=HERE/'runtime';manifest=json.loads((HERE/'execution-manifest.json').read_text())
def verify():
    for e in manifest:assert hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()==e['sha256']
verify();sys.path.insert(0,str(ROOT));os.chdir(ROOT);os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
from deploy import bootstrap_fresh
import pytest
def forbidden(*a,**k):raise AssertionError('Actual DB connect forbidden in bootstrap03 architect offline checks')
with patch.object(bootstrap_fresh.psycopg,'connect',forbidden):
    code=pytest.main([str(ROOT/'tests/test_bootstrap_fresh.py'),'-q','--assert=plain','--tb=short','-c','/dev/null','--junitxml='+str(HERE/'independent-junit.xml')])
verify();assert code==0
(HERE/'result.json').write_text(json.dumps({'candidate_sha256':'632f7419d561b00e1d060e87cd9ccca1a99f86185e6ba20653ac12ffb68b54ab','candidate_files':3,'additional_public_SQL_resources':15,'SQL_executed':0,'actual_DB_connections':0,'live_profiles_created':0,'paid_calls':0,'loaded_source':bootstrap_fresh.__file__},ensure_ascii=False,indent=2)+'\n')
