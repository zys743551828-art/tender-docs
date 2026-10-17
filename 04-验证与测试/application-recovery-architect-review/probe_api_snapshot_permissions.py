"""Exact restricted API snapshot capability: read-only queries, no owner or DDL."""
from pathlib import Path
import sys,json
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'research-backend-checks/historical-authority/candidate-02/runtime'))
from packages.domain.runtime import connection,assert_role
import psycopg
assert_role()
with connection() as c:
    c.execute('SET TRANSACTION READ ONLY')
    capabilities=c.execute("SELECT has_schema_privilege(current_user,'model_release','USAGE') AS schema_usage,has_table_privilege(current_user,'model_release.registrations','SELECT') AS registrations_select,has_table_privilege(current_user,'model_release.certifications','SELECT') AS certifications_select").fetchone()
    error=None
    try:
        with c.transaction():c.execute('SELECT count(*) FROM model_release.certifications').fetchone()
    except psycopg.errors.InsufficientPrivilege as exc:error=exc.sqlstate
assert capabilities['schema_usage'] and capabilities['registrations_select']
assert capabilities['certifications_select'] is False and error=='42501'
value={'mode':'actual restricted API LOGIN read-only probe against existing same release schema; not pg_dump or restore execution','capabilities':capabilities,'certifications_read_sqlstate':error,'DDL':0,'owner_connections':0,'object_requests':0,'model_calls':0,'consumers':0}
(HERE/'api-snapshot-capability-counterexample.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(value,ensure_ascii=False))
