"""Independent optional-stage failure/lease exhaustion isolation; no model transport."""
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import os,json,hashlib
from apps.worker import engine
from packages.domain import model_calls
from packages.domain.runtime import connection,assert_role,enqueue_plan,DomainError,sha

def main():
 root=Path(os.environ['QA_CONTENT_ROOT']);out=root/'optional-isolation-execution';out.mkdir(exist_ok=False);files=json.loads((root/'runtime-manifest.json').read_text());assert all(hashlib.sha256((root/'execution-inputs'/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in files);assert_role();assert_role(True)
 result=dict(passed=False,checks=[],paid_calls=0,model_transport_calls=0,kafka_consumers_started=0,scope='Actual restrictedPG ordinary engine and reservation ledger. Synthetic document/1 optional-stage plan and no-op artifact publisher only, no real detector/model/Kafka claim.')
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 stage='setup'
 try:
  for scenario in ['explicit_nonretryable','expired_third_attempt']:
   stage=scenario;actor,bid,command=[uuid4() for _ in range(3)]
   with connection() as c:
    c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立可选失败隔离')",(actor,));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立可选失败计划','{\"auto_review\":false}',%s)",(bid,actor));c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-optional-isolation',%s,%s,%s)",(command,actor,bid,str(command),sha(str(command))));op=enqueue_plan(c,bid,command,'qa-optional-plan',dict(id=str(uuid4())),stages=[dict(key='consistency-check',depends_on=[],required=False),dict(key='scope-residue-check',depends_on=[],required=False),dict(key='finalize-review',depends_on=['consistency-check','scope-residue-check'])],handler_version='document/1',topic='tender.task.document.v1');messages={r['payload']['step_key']:r['payload'] for r in c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(op['id'],)).fetchall()}
   left,right=messages['consistency-check'],messages['scope-residue-check'];ll,rl=engine.acquire(left),engine.acquire(right);assert ll and rl;calls=[]
   for msg,lease in [(left,ll),(right,rl)]:
    call,isnew=model_calls.reserve(msg,lease,logical_key=msg['step_key'],request_digest=sha(msg['step_key']),profile_digest=sha('explicit-zero-transport'),max_tokens=32);assert isnew;calls.append(call['id'])
   if scenario=='explicit_nonretryable':assert engine.finish(left,ll,None,DomainError('QA_OPTIONAL_UNAVAILABLE','显式非重试可选失败',409))
   else:
    with connection(True) as c:c.execute("UPDATE step_receipts SET attempt_count=3,lease_until=now()-interval '1 second' WHERE operation_id=%s AND step_key=%s",(op['id'],left['step_key']))
    assert engine.acquire(left) is None
    check(scenario+'_old_lease_cannot_renew_or_publish',engine.renew(left,ll) is False and engine.finish(left,ll,{}) is False)
   with connection(True) as c:
    state=c.execute('SELECT state FROM operations WHERE id=%s',(op['id'],)).fetchone()['state'];last=c.execute("SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND payload->>'step_key'='finalize-review'",(op['id'],)).fetchone()['n'];ledger=c.execute('SELECT id,state FROM model_calls WHERE id=ANY(%s)',(calls,)).fetchall();states={x['id']:x['state'] for x in ledger}
   check(scenario+'_failed_optional_neither_aborts_sibling_nor_starts_finalizer',state=='running' and states[calls[1]]=='reserved' and last==0,dict(operation_state=state,sibling_call_state=states[calls[1]],finalizer_dispatches=last))
   if scenario=='expired_third_attempt':check(scenario+'_own_unknown_kept_without_poisoning_sibling',states[calls[0]]=='unknown')
   with patch.object(engine,'publish',return_value={'result_ref':None,'mechanism':'explicit_noop_artifact'}):
    assert engine.finish(right,rl,{})
    with connection(True) as c:rows=c.execute("SELECT payload FROM outbox WHERE operation_id=%s AND payload->>'step_key'='finalize-review'",(op['id'],)).fetchall()
    check(scenario+'_required_finalizer_dispatched_exactly_once_after_both_optional_terminals',len(rows)==1)
    final=rows[0]['payload'];lease=engine.acquire(final);assert lease and engine.finish(final,lease,{})
   with connection(True) as c:
    state=c.execute('SELECT state FROM operations WHERE id=%s',(op['id'],)).fetchone()['state'];steps=c.execute('SELECT step_key,state FROM step_receipts WHERE operation_id=%s',(op['id'],)).fetchall();stateby={x['step_key']:x['state'] for x in steps};effects=c.execute('SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s',(op['id'],)).fetchone()['n'];dead=c.execute("SELECT count(*) AS n FROM outbox WHERE operation_id=%s AND topic='tender.task.dead-letter.v1'",(op['id'],)).fetchone()['n'];modelcount=c.execute('SELECT count(*) AS n FROM model_calls WHERE operation_id=%s',(op['id'],)).fetchone()['n']
   check(scenario+'_final_success_keeps_failure_evidence_and_no_extra_paid_attempts',state=='succeeded' and stateby=={'consistency-check':'failed','scope-residue-check':'succeeded','finalize-review':'succeeded'} and effects==3 and dead==1 and modelcount==2,dict(operation_state=state,steps=stateby,effects=effects,dead_letter_intents=dead,model_reservations=modelcount))
  result['passed']=all(x['passed'] for x in result['checks'])
 except Exception as e:result['fatal']=dict(stage=stage,type=type(e).__name__,code=getattr(e,'code',None),message='Independent optional-plan mechanism incomplete')
 result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest());(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
