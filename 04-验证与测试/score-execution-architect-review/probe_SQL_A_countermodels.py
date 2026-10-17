"""Read-only scalar models of two SQL-A guards; no scoring schema execution or paid work."""
from pathlib import Path
import hashlib,json,sys
HERE=Path(__file__).resolve().parent
source=HERE.parent/'research-backend-checks/score-execution/sql-draft-A/score-execution.sql'
assert hashlib.sha256(source.read_bytes()).hexdigest()=='aea80880ed78108a29fce669e4b2a015fb4cb5e8c62b1f87c12aa8545a1206fa'
sys.path.insert(0,str(HERE.parent/'research-backend-checks/historical-authority/candidate-02/runtime'))
from packages.domain.runtime import connection,assert_role
assert_role()
with connection() as c:
    c.execute('SET TRANSACTION READ ONLY')
    identity=c.execute('''WITH inputs(id,review_id,method,chapter,request) AS (
      VALUES ('I1','same-review','same-method','same-chapter','same-request'),('I2','same-review','same-method','same-chapter','same-request')),
      jobs(id,input_id) AS (VALUES ('J1','I1')),
      calls(job_id,state) AS (VALUES ('J1','unknown'))
      SELECT i.id AS input_id,NOT EXISTS(SELECT FROM calls call JOIN jobs old ON old.id=call.job_id WHERE old.input_id=i.id AND call.state IN ('reserved','unknown')) AS start_unknown_guard_allows
      FROM inputs i ORDER BY i.id''').fetchall()
    points=c.execute('''WITH cases(maximum) AS (VALUES (100::numeric),('NaN'::numeric),('Infinity'::numeric))
      SELECT maximum::text AS maximum, maximum<=0 AS early_maximum_rejected,
       10::numeric NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND 10>=0 AND 10<=maximum AS inherited_band_points_accepted
      FROM cases''').fetchall()
assert identity[0]['start_unknown_guard_allows'] is False and identity[1]['start_unknown_guard_allows'] is True
assert all(not row['early_maximum_rejected'] and row['inherited_band_points_accepted'] for row in points)
value={'candidate_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'mode':'actual nonowner PostgreSQL read-only scalar WHERE models; not full score schema/RPC execution','equivalent_input_unknown':identity,'nonfinite_maximum':points,'DDL':0,'model_calls':0,'object_calls':0,'consumers':0}
(HERE/'SQL-A-countermodels.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(value,ensure_ascii=False))
