"""Read only precisely identified synthetic outputs in this fresh checks database."""
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'frozen-tree'))
from packages.domain.runtime import connection,clean
with connection() as c:
    db=c.execute('SELECT current_database() AS name').fetchone()['name']
    if db!='tender_checks_authoring_complete01':
        raise RuntimeError('unexpected check database')
    rows=c.execute("SELECT DISTINCT bl.digest,bl.object_key,bl.byte_size,bl.media_type FROM builds bu JOIN build_outputs bo ON bo.build_id=bu.id AND bo.bid_id=bu.bid_id JOIN document_versions v ON v.id=bo.document_version_id AND v.bid_id=bo.bid_id JOIN blobs bl ON bl.id=v.blob_id JOIN bids b ON b.id=bu.bid_id JOIN members m ON m.id=b.created_by WHERE b.title='合成章节测试' AND m.display_name='合成章节测试成员' AND bo.state='succeeded' ORDER BY bl.digest").fetchall()
report={'scope':'Owned synthetic build blob inventory only in this fresh isolated checks DB, including preflight and final legacy export regressions; no bucket scan. Some listed outputs use explicit memory object fixtures, so inventory is not independent proof that every row was PUT to OSS. Legacy test_real_worker_build and followup[failed]/preflight[adopted] used configured existing OSS and round-trip digest checks; no model calls.','database':db,'objects':clean(rows),'cleanup':'No blob with a persisted document reference is deleted; no bucket policy/CORS change or pre-existing object deletion.'}
(HERE/'evidence/resource-scope.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'synthetic_distinct_digests':len(rows),'private_values_printed':False}))
