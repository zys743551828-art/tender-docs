"""Read-only scalar countermodel for exact full-body page coverage; no DDL or business I/O."""
import hashlib,json,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'research-backend-checks/historical-input/draft-A/historical-input-qualification.sql'
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()=='2268c35a4822bf2adef4663485a439317ca6776e65d2ed307066dfe7d0831510'
FIXED=HERE.parent/'research-backend-checks/historical-authority/candidate-02/runtime'
sys.path.insert(0,str(FIXED))
from packages.domain.runtime import connection,assert_role
assert_role()
results=[]
with connection() as c:
    for pages in ([1,2],[2,3],[1,3]):
        row=c.execute('''WITH pages(physical_page) AS (SELECT unnest(%s::int[]))
          SELECT 2=(SELECT count(*) FROM pages) AS candidate_guard,
          2=(SELECT count(*) FROM pages) AND NOT EXISTS(SELECT FROM pages WHERE physical_page NOT BETWEEN 1 AND 2) AS exact_guard''',(pages,)).fetchone()
        results.append({'physical_pages':pages,**row})
assert results[0]['candidate_guard'] and results[0]['exact_guard']
assert all(r['candidate_guard'] and not r['exact_guard'] for r in results[1:])
value={'candidate_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'mode':'actual nonowner PostgreSQL read-only scalar countermodel, not full seal_input execution','results':results,'DDL':0,'paid_calls':0,'object_requests':0,'consumers':0}
(HERE/'pages-countermodel.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(value,ensure_ascii=False))
