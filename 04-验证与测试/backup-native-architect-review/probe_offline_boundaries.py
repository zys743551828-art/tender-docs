"""Frozen backup ports: local tests only, opt-in network/PG/native paths remain skipped."""
import os,sys,hashlib,json,importlib.util
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;APP=Path('/workspace/tender-workbench');FROZEN=HERE/'frozen-inputs01'
sys.path.insert(0,str(APP))
import deploy.backups
for name in ('run','generic'):
 path=FROZEN/'deploy/backups'/(name+'.py');spec=importlib.util.spec_from_file_location('deploy.backups.'+name,path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;setattr(deploy.backups,name,m);spec.loader.exec_module(m)
aux={}
for name in ('test_backup.py','test_backup_generic.py'):
 raw=(APP/'tests'/name).read_bytes();dest=HERE/('observed-'+name);dest.write_bytes(raw);aux[name]=hashlib.sha256(raw).hexdigest()
import pytest
with patch.dict(os.environ,{'PATH':os.environ['PATH'],'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},clear=True):
 code=pytest.main([str(HERE/'observed-test_backup.py'),str(HERE/'observed-test_backup_generic.py'),str(FROZEN/'tests/test_backup_generic_native.py'),'-q','--assert=plain','--junitxml='+str(HERE/'offline-boundaries-junit.xml')]);assert code==0
(HERE/'offline-boundaries-result.json').write_text(json.dumps({'code':code,'mode':'offline tests only; opt-in PG/native paths skipped','auxiliary_test_source_sha256':aux,'loaded_generic':str(deploy.backups.generic.__file__),'loaded_safe':str(deploy.backups.run.__file__),'paid_calls':0,'private_config_read':False,'DB_connections':0,'cloud_requests':0},ensure_ascii=False,indent=2)+'\n')
