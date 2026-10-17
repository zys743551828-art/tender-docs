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
        op,msg=operation();lease=engine.acquire(msg)
        sql(f"UPDATE design.step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id='{op['id']}';")
        engine.reconcile(once=True)
        with connection(True) as c:
            new=c.execute('SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at DESC LIMIT 1',(op['id'],)).fetchone()['payload']
            renewed=c.execute('SELECT design.renew_step(%s,%s,%s,%s,%s) AS yes',(op['id'],msg['step_key'],msg['input_digest'],lease['fence'],lease['owner'])).fetchone()['yes']
        acquired_new=engine.acquire(new)
        published_old=engine.finish(msg,lease,{'unused':'not published'})
        first={'original_generation':msg['generation'],'new_generation':new['generation'],
               'old_owner_renewed_after_takeover':renewed,'new_generation_acquire':acquired_new,
               'old_generation_finish':published_old,
               'counterexample_reproduced':renewed and acquired_new is None and not published_old}
        assert first['counterexample_reproduced'],first
        sql(f"UPDATE design.operations SET state='failed',finished_at=now() WHERE id='{op['id']}';")
        op2,msg2=operation();lease2=engine.acquire(msg2)
        engine.finish(msg2,lease2,None,RuntimeError('synthetic transient failure'))
        with connection(True) as c:
            future=c.execute('SELECT payload,available_at>clock_timestamp() AS not_due FROM outbox WHERE operation_id=%s ORDER BY available_at DESC LIMIT 1',(op2['id'],)).fetchone()
        assert future['not_due'],future
        engine.reconcile(once=True)
        with connection(True) as c:
            latest=c.execute('SELECT payload,available_at<=clock_timestamp() AS due_now FROM outbox WHERE operation_id=%s ORDER BY available_at DESC LIMIT 1',(op2['id'],)).fetchone()
            # A future scheduled generation 2 sorts after the immediate generation 3.
            all_rows=c.execute('SELECT payload,available_at<=clock_timestamp() AS due_now FROM outbox WHERE operation_id=%s',(op2['id'],)).fetchall()
        newest=max(all_rows,key=lambda r:r['payload']['generation'])
        second={'retry_generation':future['payload']['generation'],'retry_was_not_due':future['not_due'],
                'reconciler_generation':newest['payload']['generation'],'new_dispatch_due_now':newest['due_now'],
                'counterexample_reproduced':newest['payload']['generation']>future['payload']['generation'] and newest['due_now']}
        assert second['counterexample_reproduced'],second
        result={'scope':'实际Worker acquire/finish/reconcile及真实隔离PG；无Kafka/S3/业务API执行',
                'input_sha256':inputs,'image':IMAGE,'lease_takeover':first,'retry_backoff':second,
                'source_modified':False,'shared_dev_or_qa_used':False}
        (HERE/'reconcile-counterexamples.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'lease_takeover':first,'retry_backoff':second},ensure_ascii=False))
    finally:cmd(['rm','-f','-v',name])

if __name__=='__main__':main()
