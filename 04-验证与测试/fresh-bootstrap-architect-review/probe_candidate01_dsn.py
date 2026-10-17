"""Offline only: libpq parser applies query options after URI path/userinfo."""
import hashlib,json
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from psycopg.conninfo import conninfo_to_dict
HERE=Path(__file__).resolve().parent
source=HERE/'frozen-inputs01/deploy/bootstrap_fresh.py'
assert hashlib.sha256(source.read_bytes()).hexdigest()=='b22942dd45a961f3529de51dadb0ed4d9569d4e922f4f5fc9071790ccfc1a16b'
base='postgresql://synthetic_owner:synthetic_secret@127.0.0.1:55441/release'
rows=[]
for query in ('dbname=tender_existing_release','user=synthetic_owner&password=synthetic_secret','dbname=tender_existing_release&user=synthetic_owner&password=synthetic_secret'):
 owner=urlsplit(base+'?'+query)
 owner_target=urlunsplit(owner._replace(path='/tender_checks_synthetic'))
 login_target=urlunsplit(owner._replace(netloc='synthetic_new_api:synthetic_new_secret@127.0.0.1:55441',path='/tender_checks_synthetic'))
 for kind,dsn,expected_user in [('new_owner',owner_target,'synthetic_owner'),('new_api',login_target,'synthetic_new_api')]:
  fields=conninfo_to_dict(dsn)
  rows.append({'scenario':kind,'query_keys':[part.split('=',1)[0] for part in query.split('&')],'path_dbname':'tender_checks_synthetic','expected_user':expected_user,'actual_dbname':fields['dbname'],'actual_user':fields['user'],'wrong_database':fields['dbname']!='tender_checks_synthetic','wrong_user':fields['user']!=expected_user})
assert any(x['wrong_database'] for x in rows) and any(x['wrong_user'] for x in rows)
(HERE/'candidate01-dsn-counterexample.json').write_text(json.dumps({'mode':'offline libpq parser only; mirrors frozen URI replacement, not database execution','frozen_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'synthetic_credentials_only':True,'connections':0,'DDL_executed':False,'counterexamples':rows},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cases':len(rows),'wrong_database_cases':sum(r['wrong_database'] for r in rows),'wrong_user_cases':sum(r['wrong_user'] for r in rows),'connections':0}))
