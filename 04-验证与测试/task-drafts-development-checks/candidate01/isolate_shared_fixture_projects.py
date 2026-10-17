"""Terminate only our positively identified synthetic fixtures, preserving all evidence."""
import json
from pathlib import Path

from packages.domain.runtime import Command, audit, clean, connection, uid
from packages.lifecycle.service import change, lock

result = []
with connection() as c:
    assert c.execute('SELECT current_database() AS name').fetchone()['name'] == 'tender_checks_workbench08'
    fixtures = c.execute("""SELECT b.id,b.created_by,p.phase,p.archived_at FROM bids b
      JOIN members m ON m.id=b.created_by JOIN bid_profiles p ON p.bid_id=b.id
      WHERE b.title='任务合成项目' AND m.display_name='任务合成测试成员'
      AND NOT EXISTS(SELECT 1 FROM uploads u WHERE u.bid_id=b.id)
      AND NOT EXISTS(SELECT 1 FROM submission_records s WHERE s.bid_id=b.id)
      ORDER BY b.id""").fetchall()
for row in fixtures:
    with connection() as c:
        lock(c, row['id'])
        cmd = Command(c,row['created_by'],'test-fixture-isolate',row['id'],str(uid()),{'reason':'Shared-runtime regression fixtures; preserve evidence and prevent any future dispatch'})
        if row['phase'] not in ('closed','terminated'):
            change(c,row['id'],row['created_by'],'terminate','PM自有合成回归项目隔离；保留失败证据，禁止继续派发',cmd.id)
        cancelled = c.execute("""UPDATE operations SET state='cancel_requested',cancel_requested_at=clock_timestamp()
          WHERE bid_id=%s AND state IN('queued','running') RETURNING id,kind,state""",(row['id'],)).fetchall()
        for operation in cancelled:
            c.execute("UPDATE step_receipts SET lease_until=clock_timestamp() WHERE operation_id=%s AND state='running'",(operation['id'],))
            audit(c,row['created_by'],row['id'],'operation.cancel_requested',{'operation_id':operation['id'],'reason':'PM synthetic fixture isolation'},cmd.id)
        states = c.execute('SELECT kind,state FROM operations WHERE bid_id=%s ORDER BY id',(row['id'],)).fetchall()
        cmd.finish({'isolated':True,'operations':clean(states)})
        result.append({'bid_id':str(row['id']),'cancellation_requests':clean(cancelled),'operation_states':clean(states)})
with connection() as c:
    calls = c.execute('SELECT count(*) AS n FROM model_calls').fetchone()['n']
    assert calls == 0
out=Path(__file__).resolve().parent
(out/'shared-fixture-isolation.json').write_text(json.dumps({'fixtures':result,'model_calls_in_workbench08':calls,'deleted_rows':0,'business_upload_projects_selected':False,'other_member_projects_selected':False},indent=2)+'\n')
print({'isolated_synthetic_projects':len(result),'model_calls_in_workbench08':calls,'deleted_rows':0})
