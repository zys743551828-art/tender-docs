"""Notify one existing completed synthetic local run; no scheduling or task dispatch."""
import os,sys,json,shlex,hashlib,datetime
from pathlib import Path
base=Path('/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/fixture05')
result=base/'local-notification-once-result.json';assert not result.exists()
candidate=Path('/workspace/tender-docs/04-验证与测试/task-drafts-development-checks/candidate05/candidate.json');assert hashlib.sha256(candidate.read_bytes()).hexdigest()=='4f91d6aafcc397e89c19acbf626631853663a5c53e8ed95db300bc802856d650'
m=json.loads(candidate.read_text());fixed=Path(m['runtime_root'])
def verify():
 for r in m['sources']:assert hashlib.sha256((fixed/r['path']).read_bytes()).hexdigest()==r['sha256']
verify()
values={}
for line in Path('/workspace/tender-workbench/.local/assistant_ui01-runtime.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);parts=shlex.split(v,comments=True);values[k.strip()]=parts[0] if parts else ''
os.environ.clear();os.environ.update({'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','WORKBENCH_ENV_FILE':'/dev/null','API_DATABASE_DSN':values['API_DATABASE_DSN']})
sys.path.insert(0,str(fixed))
from packages.domain.runtime import connection,assert_role
from packages.tasks import runs
from uuid import UUID
assert_role()
dev=json.loads(Path('/workspace/tender-workbench/apps/web/qa-previews/assistant-task-10/tasks-browser-result.json').read_text());bid=UUID(dev['project_id']);run=UUID(next(r['run_id'] for r in dev['checks'] if r['case']=='manual-supplement-local-result-fixed-input-not-semantic-success'))
with connection() as c:
 assert c.execute('SELECT current_database() AS db').fetchone()['db']=='tender_checks_assistant_ui01'
 before={table:c.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in ['model_calls','outbox','task_runs']}
 v=runs.view(c,bid,run);assert v['state']=='succeeded' and v['frozen_manifest']['task_kind']=='supplement_impact'
 first=runs.notify(c,bid,run);second=runs.notify(c,bid,run);assert first is True and second is False
 notice=c.execute("SELECT id,actor_id,payload FROM audit_entries WHERE bid_id=%s AND event_type='task.notification' AND payload->>'run_id'=%s",(bid,str(run))).fetchall();assert len(notice)==1
 after={table:c.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in before};assert before==after
verify()
record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'candidate_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'source_count_verified_before_after':len(m['sources']),'project_id':str(bid),'run_id':str(run),'state':v['state'],'task_kind':'supplement_impact','notification_id':str(notice[0]['id']),'actor_id':str(notice[0]['actor_id']),'first_notify':first,'second_notify':second,'notification_rows_for_run':1,'unchanged_table_counts':after,'task_dispatches':0,'scheduler_ticks':0,'provider_requests':0,'consumers_started':False,'source':'original fixed runs.notify on exact previously completed local synthetic task','prior_notify_preflight_failure':'notify_local_once.py referenced nonexistent outbox_events before any mutation; rolled back', 'preliminary_read_only_failure':'notify_local_readback.py selected nonexistent task_runs.state before SQL transaction rollback; no mutation'}
result.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');print(json.dumps(record,ensure_ascii=False))
