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
        from fastapi.testclient import TestClient
        from apps.api.auth import password_hash
        os.environ['APP_ORIGIN']='http://architect.local'
        os.environ['SESSION_SECRET']=str(uuid.uuid4())
        os.environ['CSRF_SECRET']=str(uuid.uuid4())
        from apps.api.main import app
        for input_name in ['apps/api/main.py','apps/api/views.py','apps/api/auth.py']:
            inputs[input_name]=hashlib.sha256((APP/input_name).read_bytes()).hexdigest()
        with connection() as c:
            c.execute('INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)',(actor,'architect-synthetic',password_hash('SyntheticOnly_123456')))
        op,msg=operation(); checks=[]
        with TestClient(app) as client:
            logged=client.post('/api/v1/auth/login',headers={'Origin':os.environ['APP_ORIGIN']},json={'login_name':'architect-synthetic','password':'SyntheticOnly_123456'})
            assert logged.status_code==200
            headers={'Origin':os.environ['APP_ORIGIN'],'X-CSRF-Token':logged.json()['csrf_token']}
            url='/api/v1/operations/'+op['id'];cancel_url=url+'/cancel'
            initial=client.get(url);queued_tag=initial.headers['ETag']
            lease=engine.acquire(msg)
            live=client.get(url);live_tag=live.headers['ETag'];live_body=live.json()
            assert live_tag!=queued_tag
            assert engine.renew(msg,lease) is True
            assert client.get(url).headers['ETag']==live_tag
            sql(f"UPDATE design.operation_steps SET dispatch_generation=dispatch_generation+1 WHERE operation_id='{op['id']}';")
            moved=client.get(url);moved_tag=moved.headers['ETag']
            assert moved.json()==live_body and moved_tag!=live_tag
            checks.append('stable heartbeat tag; same body with changed generation gets new strong tag')
            body={'reason':'synthetic architecture cancellation','cancel_pending_same_input':False}
            def cancel(key,tag=None):
                h=headers|{'Idempotency-Key':key}
                if tag is not None:h['If-Match']=tag
                return client.post(cancel_url,headers=h,json=body)
            assert cancel(str(uid())).status_code==428
            assert cancel(str(uid()),live_tag).status_code==412
            assert client.get(url).json()['state']=='running'
            checks.append('missing If-Match 428 and previous generation 412 without state change')
            key=str(uid());accepted=cancel(key,moved_tag)
            assert accepted.status_code==200 and accepted.json()['state']=='cancel_requested'
            assert accepted.headers['ETag']!=moved_tag
            engine.reconcile(once=True)
            terminal=client.get(url);assert terminal.json()['state']=='cancelled'
            replay=cancel(key,moved_tag)
            assert replay.status_code==200 and replay.headers['Idempotent-Replayed']=='true'
            assert replay.json()==accepted.json() and replay.headers['ETag']==accepted.headers['ETag']
            assert cancel(key,terminal.headers['ETag']).status_code==409
            with connection() as c:
                n=c.execute("SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='operation.cancel_requested'",(bid,)).fetchone()['n']
            assert n==1
            checks.append('valid cancel and terminal-afterward replay keep original result; changed condition 409; one cancel audit')
        result={'scope':'actual ASGI routes with isolated PG/nonowner connections; no shared dev/QA or Kafka/S3',
                'passed':True,'input_sha256':inputs,'checks':checks,'source_modified':False,'shared_dev_or_qa_used':False}
        (HERE/'backend03-cancel-regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':True,'checks':checks},ensure_ascii=False))
    finally:cmd(['rm','-f','-v',name])

if __name__=='__main__':main()
