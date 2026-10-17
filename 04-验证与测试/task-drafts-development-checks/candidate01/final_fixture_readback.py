"""Read back and archive only terminated, positively identified PM synthetic fixtures."""
import json
from pathlib import Path

from packages.domain.runtime import Command,clean,connection,uid
from packages.lifecycle.service import change,lock

with connection() as c:
    assert c.execute('SELECT current_database() AS name').fetchone()['name']=='tender_checks_workbench08'
    calls=c.execute("""SELECT mc.state,mc.logical_call_key,mc.provider_request_id,mc.usage,b.title,m.display_name
      FROM model_calls mc JOIN operations o ON o.id=mc.operation_id JOIN bids b ON b.id=o.bid_id
      JOIN members m ON m.id=b.created_by ORDER BY mc.created_at""").fetchall()
    assert len(calls)==3 and all(r['logical_call_key'] in ('synthetic-no-provider','synthetic-preparation-no-provider')
      and r['provider_request_id'] is None and r['usage'] is None and r['title']=='任务合成项目'
      and r['display_name']=='任务合成测试成员' for r in calls)
    rows=c.execute("""SELECT b.id,b.created_by,p.archived_at FROM bids b JOIN members m ON m.id=b.created_by
      JOIN bid_profiles p ON p.bid_id=b.id WHERE b.title='任务合成项目' AND m.display_name='任务合成测试成员'
      AND p.phase='terminated' AND NOT EXISTS(SELECT 1 FROM uploads u WHERE u.bid_id=b.id)
      AND NOT EXISTS(SELECT 1 FROM submission_records s WHERE s.bid_id=b.id)
      AND EXISTS(SELECT 1 FROM commands cmd WHERE cmd.target_id=b.id AND cmd.command_kind='test-fixture-isolate') ORDER BY b.id""").fetchall()
records=[]
for row in rows:
    with connection() as c:
        lock(c,row['id'])
        active=c.execute("SELECT id FROM operations WHERE bid_id=%s AND state IN('queued','running','cancel_requested')",(row['id'],)).fetchall()
        assert not active
        if not row['archived_at']:
            cmd=Command(c,row['created_by'],'test-fixture-archive',row['id'],str(uid()),{})
            value=change(c,row['id'],row['created_by'],'archive','PM合成回归夹具完成隔离；保留全部原始结果/unknown账本',cmd.id)
            cmd.finish(value)
        records.append({'bid_id':str(row['id']),'active_operations':0,'archived':True})
out=Path(__file__).resolve().parent
(out/'shared-fixture-isolation-final.json').write_text(json.dumps({'projects':records,'synthetic_ledger_rows':clean(calls),
    'model_call_rows_are_not_network_calls':True,'actual_model_requests':0,'deleted_rows':0,
    'original_pricing_upload_project_touched':False,'original_ledger_states_changed':False},indent=2)+'\n')
print({'archived_synthetic_projects':len(records),'synthetic_ledger_rows':len(calls),'actual_provider_requests':0,'deleted_rows':0})
