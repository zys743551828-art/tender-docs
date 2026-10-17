"""Read-only PostgreSQL boolean expressions, not initialization/execution of Word schema."""
import os,json,hashlib
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
HERE=Path(__file__).resolve().parent
bad={'state':None,'coverage':'complete','gaps':[],'chain':{},'conversion_attempted':False}
query="""WITH fixture AS (SELECT %s::jsonb AS o)
SELECT
(jsonb_typeof(o)='object' AND o ?& ARRAY['state','coverage','gaps','chain','conversion_attempted'] AND o->>'state' IN ('succeeded','failed') AND jsonb_typeof(o->'gaps')='array' AND jsonb_typeof(o->'chain')='object' AND jsonb_typeof(o->'conversion_attempted')='boolean') AS old_shape,
((o->>'state'='succeeded')=false) AS old_pdf_pair,
(o->>'coverage'<>'complete' OR (o->>'state'='succeeded' AND o->'gaps'='[]'::jsonb)) AS old_complete,
(o->>'state'='succeeded') AS old_success_trigger_branch,
coalesce((jsonb_typeof(o)='object' AND o ?& ARRAY['state','coverage','gaps','chain','conversion_attempted'] AND jsonb_typeof(o->'state')='string' AND o->>'state' IN ('succeeded','failed') AND jsonb_typeof(o->'coverage')='string' AND o->>'coverage' IN ('complete','partial','failed') AND jsonb_typeof(o->'gaps')='array' AND jsonb_typeof(o->'chain')='object' AND jsonb_typeof(o->'conversion_attempted')='boolean'),false) AS fixed_shape
FROM fixture"""
with psycopg.connect(os.environ['API_DATABASE_DSN']) as c:
 c.execute('SET TRANSACTION READ ONLY')
 who=c.execute('SELECT current_database(),current_user,rolsuper FROM pg_roles WHERE rolname=current_user').fetchone();assert who[0]=='tender_checks_delivery_protocols01' and not who[2]
 row=c.execute(query,(Jsonb(bad),)).fetchone()
assert row[:4]==(None,None,None,None) and row[4] is False
result={'mode':'actual PostgreSQL READ ONLY scalar expression evaluation; no Word DDL or rows','fixture':bad,'old_shape':row[0],'old_pdf_pair':row[1],'old_complete':row[2],'old_success_trigger_branch':row[3],'fixed_shape':row[4],'postgres_CHECK_accepts_true_or_null':True,'database_nonowner':True,'DDL_executed':False,'object_requests':0,'paid_calls':0}
(HERE/'json-null-counterexample-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
