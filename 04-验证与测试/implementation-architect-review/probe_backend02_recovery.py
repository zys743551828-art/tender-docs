"""Independent counterexamples against actual candidate Worker functions, isolated PG only."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
FILES=['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'

def main():
    env=dict(os.environ)
    for k in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(k,None)
    name='tender-impl-review-'+uuid.uuid4().hex[:10]
    def cmd(args,text=None):
        p=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],input=text,text=True,capture_output=True,timeout=90,env=env)
        if p.returncode:raise RuntimeError(p.stderr or p.stdout)
        return p.stdout.strip()
    def sql(text):return cmd(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','postgres'],text)
    inputs={str(p.relative_to(APP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [APP/'apps/worker/engine.py',APP/'packages/domain/runtime.py',*[APP/'packages/contracts/sql'/f for f in FILES]]}
    cmd(['run','-d','--name',name,'--memory','1g','--tmpfs','/var/lib/postgresql/data:rw','-p','127.0.0.1::5432','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE])
    try:
        for _ in range(100):
            try:cmd(['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres']);break
            except RuntimeError:time.sleep(.2)
        else:raise RuntimeError('PG not ready')
        for f in FILES:sql((APP/'packages/contracts/sql'/f).read_text())
        sql('CREATE ROLE impl_review_api LOGIN INHERIT NOSUPERUSER; GRANT tender_api TO impl_review_api; CREATE ROLE impl_review_worker LOGIN INHERIT NOSUPERUSER; GRANT tender_worker TO impl_review_worker;')
        port=cmd(['port',name,'5432/tcp']).rsplit(':',1)[1]
        os.environ['API_DATABASE_DSN']=f'postgresql://impl_review_api@127.0.0.1:{port}/postgres'
        os.environ['WORKER_DATABASE_DSN']=f'postgresql://impl_review_worker@127.0.0.1:{port}/postgres'
        sys.path.insert(0,str(APP))
        from packages.domain.runtime import connection, enqueue, uid
        from apps.worker import engine
        actor,bid=uid(),uid()
        sql(f"INSERT INTO design.members VALUES('{actor}','架构合成成员',true); INSERT INTO design.bids(id,title,settings,created_by) VALUES('{bid}','架构恢复反例','{{}}','{actor}');")
        def operation():
            command=uid()
            with connection() as c:
                c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'fixture',%s,%s,%s)",(command,actor,bid,str(command),'a'*64))
                op=enqueue(c,bid,command,'parse-materials',{'id':uid()})
                msg=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(op['id'],)).fetchone()['payload']
            return op,msg
        checks=[]
        def published(op,age=180):
            sql(f"UPDATE design.outbox SET published_at=clock_timestamp()-interval '{age} seconds' WHERE operation_id='{op['id']}';")
        def message_for(op):
            with connection(True) as c:
                return c.execute("SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' ORDER BY (payload->>'generation')::bigint DESC LIMIT 1",(op['id'],)).fetchone()['payload']
        def snapshot(op):
            with connection(True) as c:
                return c.execute("SELECT o.state,p.dispatch_generation,(SELECT count(*) FROM outbox x WHERE x.operation_id=o.id AND topic='tender.task.document.v1') AS task_outboxes,(SELECT count(*) FROM effect_receipts e WHERE e.operation_id=o.id) AS effects,(SELECT count(*) FROM outbox x WHERE x.operation_id=o.id AND x.topic='tender.task.dead-letter.v1') AS dead_letters FROM operations o JOIN operation_steps p ON p.operation_id=o.id WHERE o.id=%s",(op['id'],)).fetchone()
        op,msg=operation();published(op);lease=engine.acquire(msg)
        sql(f"UPDATE design.step_receipts SET lease_until=clock_timestamp()-interval '1 second' WHERE operation_id='{op['id']}';")
        assert engine.renew(msg,lease) is False
        engine.reconcile(once=True)
        new=message_for(op)
        assert new['generation']==2
        assert engine.renew(msg,lease) is False
        # The broad frozen SQL helper also rejects the old fence after takeover.
        with connection(True) as c:
            assert c.execute('SELECT design.renew_step(%s,%s,%s,%s,%s) AS yes',(op['id'],msg['step_key'],msg['input_digest'],lease['fence'],lease['owner'])).fetchone()['yes'] is False
        published(op,0)
        new_lease=engine.acquire(new)
        assert new_lease is not None and new_lease['fence']>lease['fence']
        assert engine.finish(msg,lease,[]) is False
        assert engine.finish(new,new_lease,[]) is True
        engine.finish(new,new_lease,[])
        s=snapshot(op);assert s['state']=='succeeded' and s['effects']==1,s
        checks.append({'name':'expired owner cannot revive; new generation acquires and finishes once','state':s})
        op2,msg2=operation();published(op2);lease2=engine.acquire(msg2)
        assert engine.finish(msg2,lease2,None,RuntimeError('synthetic transient failure')) is True
        with connection(True) as c:
            pending=c.execute("SELECT payload,available_at>clock_timestamp() AS future FROM outbox WHERE operation_id=%s ORDER BY (payload->>'generation')::bigint DESC LIMIT 1",(op2['id'],)).fetchone()
        assert pending['future'] is True
        engine.reconcile(once=True)
        s=snapshot(op2);assert s['dispatch_generation']==2 and s['task_outboxes']==2,s
        sql(f"UPDATE design.outbox SET available_at=clock_timestamp()-interval '10 seconds' WHERE operation_id='{op2['id']}' AND (payload->>'generation')::int=2;")
        engine.reconcile(once=True)
        s=snapshot(op2);assert s['dispatch_generation']==2 and s['task_outboxes']==2,s
        published(op2,0);engine.reconcile(once=True)
        s=snapshot(op2);assert s['dispatch_generation']==2 and s['task_outboxes']==2,s
        retry=message_for(op2);retry_lease=engine.acquire(retry)
        assert retry_lease is not None and engine.finish(retry,retry_lease,[]) is True
        checks.append({'name':'future/unpublished retry and fresh published grace preserve generation; same retry completes','state':snapshot(op2)})
        op3,msg3=operation()
        sql(f"UPDATE design.operations SET created_at=clock_timestamp()-interval '180 seconds' WHERE id='{op3['id']}';")
        engine.reconcile(once=True)
        s=snapshot(op3);assert s['dispatch_generation']==1 and s['task_outboxes']==1,s
        published(op3,0);engine.reconcile(once=True)
        s=snapshot(op3);assert s['dispatch_generation']==1,s
        published(op3,180);engine.reconcile(once=True)
        s=snapshot(op3);assert s['dispatch_generation']==2 and s['task_outboxes']==2,s
        final3=message_for(op3);lease3=engine.acquire(final3);assert engine.finish(final3,lease3,[]) is True
        checks.append({'name':'initial unpublished intent retained; only stale published delivery is redispatched','state':snapshot(op3)})
        for path in ['reconcile','acquire']:
            exhausted,m=operation();published(exhausted);l=engine.acquire(m)
            sql(f"UPDATE design.step_receipts SET attempt_count=3,lease_until=clock_timestamp()-interval '1 second' WHERE operation_id='{exhausted['id']}';")
            if path=='reconcile':engine.reconcile(once=True)
            assert engine.acquire(m) is None
            s=snapshot(exhausted)
            assert s['state']=='failed' and s['dispatch_generation']==1 and s['dead_letters']==1 and s['effects']==1,s
            engine.reconcile(once=True);assert snapshot(exhausted)==s
            checks.append({'name':path+' enforces shared three-attempt budget with durable failure and one dead letter','state':s})
        result={'scope':'actual candidate02 engine with isolated real PG and nonowner connections; no Kafka/S3/API/browser execution',
                'passed':True,'input_sha256':inputs,'checks':checks,'source_modified':False,'shared_dev_or_qa_used':False}
        (HERE/'backend02-recovery-regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':True,'scenarios':len(checks)},ensure_ascii=False))
    finally:cmd(['rm','-f','-v',name])

if __name__=='__main__':main()
