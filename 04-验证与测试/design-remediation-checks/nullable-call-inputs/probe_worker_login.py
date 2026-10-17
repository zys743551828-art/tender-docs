"""独立PG事务探针：真实LOGIN权限与合成协议，不是应用Worker执行。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent
DESIGN = HERE.parents[1] / '05-技术与设计/detailed-design'
FILES = ['core.sql', 'domain.sql', 'retrieval.sql', 'worker-functions.sql', 'roles.sql']
IMAGE = 'pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
H = 'a' * 64

def u(n):
    return str(uuid.UUID(int=n))


def run(snapshot, output):
    snapshot.mkdir(exist_ok=True, parents=True)
    inputs = {}
    for f in FILES:
        b = (DESIGN/f).read_bytes()
        (snapshot/f).write_bytes(b)
        inputs[f] = b
    name = 'tender-test-login-' + uuid.uuid4().hex[:10]
    env = dict(os.environ)
    for key in ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH']:
        env.pop(key, None)
    checks = []
    statements = []
    def cmd(args, text=None):
        return subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                              input=text, text=True, capture_output=True, timeout=90, env=env)
    def sql(text, actor='postgres', error=None):
        p = cmd(['exec', '-i', name, 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                 '-v', 'VERBOSITY=verbose', '-h', '127.0.0.1', '-U', actor, '-d', 'postgres'], text)
        statements.append(dict(actor=actor, sql=text, stdout=p.stdout.strip(), stderr=p.stderr.strip(), exit_code=p.returncode))
        if error:
            assert p.returncode and error in p.stderr, statements[-1]
        elif p.returncode:
            raise RuntimeError(statements[-1])
        return p.stdout.strip()
    def check(label, text, actor='postgres', expected=None, error=None):
        got = sql(text, actor, error)
        if expected is not None:
            assert got == expected, (label, got, expected)
        checks.append(label)
    started=False
    try:
        p=cmd(['run', '-d', '--name', name, '--network', 'none', '--memory', '1g', '--tmpfs',
               '/var/lib/postgresql/data:rw', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', IMAGE])
        assert p.returncode == 0, p.stderr
        started=True
        for _ in range(100):
            if cmd(['exec', name, 'pg_isready', '-U', 'postgres']).returncode == 0:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('PG not ready')
        for f in FILES:
            sql(inputs[f].decode())
        sql("CREATE ROLE qa_worker LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE; GRANT tender_worker TO qa_worker; CREATE ROLE qa_research LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE; GRANT tender_research_worker TO qa_research;")
        for actor in ['qa_worker','qa_research']:
            check(actor+'是真实LOGIN且非superuser/owner', "SELECT current_user||':'||rolcanlogin||':'||rolsuper||':'||(current_user<>pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='design.bids'::regclass))) FROM pg_roles WHERE rolname=current_user;",actor,actor+':true:false:true')
        sql(f"""INSERT INTO design.members VALUES('{u(1)}','合成成员',true);
INSERT INTO design.bids(id,title,settings,created_by) VALUES('{u(10)}','合成项目','{{}}','{u(1)}');
INSERT INTO design.blobs VALUES('{u(20)}','{H}','fixture',1,'application/pdf',now());
INSERT INTO design.document_versions VALUES('{u(30)}','{u(10)}','{u(20)}','a.pdf',now(),'bid_body','{u(1)}',now());
INSERT INTO design.submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES('{u(40)}','{u(10)}','{u(30)}','offline_fact','{{}}',now(),'{u(1)}');
INSERT INTO design.outcomes VALUES('{u(41)}','{u(10)}','{u(40)}','unknown',null,null,'{u(30)}',now(),null,'{u(1)}');
INSERT INTO design.samples VALUES('{u(42)}','{u(10)}','{u(40)}','{u(41)}','{u(43)}',now(),'admitted','[]');
INSERT INTO design.experiments VALUES('{u(50)}','{H}','{u(20)}','{u(20)}','{H}',1,'created',null);
INSERT INTO design.experiment_previews VALUES('{u(51)}','{u(50)}',1,'{H}','{{}}','{H}',5,100,10,10,'CNY',now()+interval '1 day','{u(1)}');
INSERT INTO design.research_runs(id,experiment_id,preview_id,revision,manifest_digest,state,fee_limit,currency) VALUES('{u(52)}','{u(50)}','{u(51)}',1,'{H}','running',10,'CNY');
""")
        # 普通和研究worker都按bid→operation→plan→receipt顺序领取。
        for op, actor in [(60,'qa_worker'),(70,'qa_research')]:
            sql(f"INSERT INTO design.commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES('{u(op+1)}','{u(1)}','fixture','{u(10)}','key-{op}','{H}'); INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{u(op)}','{u(10)}','{u(op+1)}','fixture-{op}','{H}','queued','test'); INSERT INTO design.operation_steps VALUES('{u(op)}','infer','{H}','test',true,ARRAY[]::text[],1); INSERT INTO design.stream_heads VALUES('{u(op+3)}','{u(10)}',0);")
            check(actor+'按规定锁序完成领取',f"BEGIN; SELECT design.lock_worker_bid('{u(10)}'); SELECT id FROM design.operations WHERE id='{u(op)}' FOR UPDATE; SELECT step_key FROM design.operation_steps WHERE operation_id='{u(op)}' FOR UPDATE; UPDATE design.operations SET state='running' WHERE id='{u(op)}'; INSERT INTO design.step_receipts(operation_id,step_key,input_digest,fence,attempt_count,state,owner_token,lease_until) VALUES('{u(op)}','infer','{H}',1,1,'running','{u(op+2)}',clock_timestamp()+interval '120 seconds'); COMMIT;",actor)
        sql(f"INSERT INTO design.research_run_items VALUES('{u(52)}','{u(42)}','{u(10)}',1,'running','{u(70)}',null,null); INSERT INTO design.research_run_attempts VALUES('{u(52)}','{u(42)}',1,'{u(10)}',1,'{u(70)}',now());")
        def reserve(call_id=80, key='replicate1', amount=4, generation=1, fence=1, owner=72, request=H):
            return f"SELECT design.reserve_research_call('{u(52)}','{u(70)}','infer','{H}',{generation},{fence},'{u(owner)}','{u(call_id)}','{key}',1,'{request}','{H}',{amount});"
        check('研究LOGIN原子预留费用并写调用账本',reserve().rstrip(';')+"->>'new_reservation';", 'qa_research', 'true')
        check('预留与账本一致',f"SELECT reserved_fee=4 AND (SELECT count(*)=1 FROM design.model_calls WHERE research_run_id='{u(52)}' AND reserved_amount=4) FROM design.research_runs WHERE id='{u(52)}';",expected='t')
        check('重复调用只读复用且禁止新请求',reserve(call_id=81).rstrip(';')+"->>'new_reservation';",'qa_research','false')
        check('重复调用返回原id',reserve(call_id=81).rstrip(';')+"->>'call_id';",'qa_research',u(80))
        check('重复后预算仍为4',f"SELECT reserved_fee FROM design.research_runs WHERE id='{u(52)}';",expected='4')
        check('重复key但不同请求拒绝',reserve(request='b'*64),'qa_research',error='23514')
        sql(f"INSERT INTO design.model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state) VALUES('{u(86)}','{u(70)}','ordinary-existing',1,'{H}','{H}','reserved');")
        check('已有普通调用不能冒充研究预算账本',reserve(call_id=87,key='ordinary-existing',amount=1),'qa_research',error='23514')
        check('调用INSERT失败使费用回滚',reserve(call_id=80,key='replicate2',amount=2),'qa_research',error='23505')
        check('INSERT失败后无漏占预算',f"SELECT reserved_fee=4 AND (SELECT count(*)=1 FROM design.model_calls WHERE research_run_id='{u(52)}') FROM design.research_runs WHERE id='{u(52)}';",expected='t')
        for label,kwargs in [('旧generation',{'generation':2}),('旧fence',{'fence':2}),('错误owner',{'owner':999})]:
            check(label+'不得预留新付费调用',reserve(call_id=82,key='replicate2',**kwargs),'qa_research',error='55000')
        sql(f"UPDATE design.step_receipts SET lease_until=clock_timestamp()-interval '1 second' WHERE operation_id='{u(70)}';")
        check('过期租约不得预留',reserve(call_id=82,key='replicate2'),'qa_research',error='55000')
        sql(f"UPDATE design.step_receipts SET lease_until=clock_timestamp()+interval '120 seconds' WHERE operation_id='{u(70)}';")
        # 真实并发连接用相同剩余预算竞争，两者都要求4，只能一个成功。
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda n: cmd(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','qa_research','-d','postgres'],reserve(call_id=n,key=f'replicate{n}',amount=4)),[83,84]))
        assert sorted(p.returncode==0 for p in results)==[False,True],[(p.stdout,p.stderr) for p in results]
        checks.append('两研究LOGIN并发付费预留仅一个成功且不超预算')
        check('并发后预算为8且仅两条调用',f"SELECT reserved_fee=8 AND (SELECT count(*)=2 FROM design.model_calls WHERE research_run_id='{u(52)}') FROM design.research_runs WHERE id='{u(52)}';",expected='t')
        sql(f"UPDATE design.research_runs SET cancel_requested_at=now() WHERE id='{u(52)}';")
        check('研究批次取消后不能新增调用',reserve(call_id=85,key='replicate3',amount=1),'qa_research',error='55000')
        sql(f"UPDATE design.operations SET state='cancel_requested',cancel_requested_at=now() WHERE id='{u(70)}';")
        check('操作取消后不能新增调用',reserve(call_id=85,key='replicate3',amount=1),'qa_research',error='55000')
        # 合成finish协议执行实际SQL：CAS保护的收据决定effect发布资格。
        def finish(op, actor, fence, effect):
            return f"""BEGIN; SELECT design.lock_worker_bid('{u(10)}');
SELECT id FROM design.operations WHERE id='{u(op)}' FOR UPDATE;
WITH valid AS (
 UPDATE design.step_receipts s SET state='succeeded',output='{{}}'
 WHERE operation_id='{u(op)}' AND step_key='infer' AND input_digest='{H}' AND fence={fence}
 AND owner_token='{u(op+2)}' AND lease_until>clock_timestamp() AND state='running'
 AND EXISTS(SELECT 1 FROM design.operations o WHERE o.id=s.operation_id AND o.state='running' AND o.cancel_requested_at IS NULL)
 RETURNING operation_id
), effects AS (
 INSERT INTO design.effect_receipts(operation_id,effect_key,result) SELECT operation_id,'{effect}','{{}}' FROM valid ON CONFLICT DO NOTHING RETURNING operation_id
), next_event AS (
 INSERT INTO design.outbox(id,operation_id,topic,deduplication_key,payload) SELECT '{u(op*1000+fence)}',operation_id,'fixture','{op}-{effect}','{{}}' FROM effects
), audit AS (
 INSERT INTO design.audit_entries(id,bid_id,event_type,payload) SELECT '{u(op*2000+fence)}','{u(10)}','fixture.finish','{{}}' FROM effects
), stream AS (
 UPDATE design.stream_heads SET last_seq=last_seq+1 WHERE id='{u(op+3)}' AND EXISTS(SELECT 1 FROM effects) RETURNING id,last_seq
) INSERT INTO design.stream_events(stream_id,seq,event_id,event_type,payload) SELECT id,last_seq,'{u(op*3000+fence)}','fixture.finish','{{}}' FROM stream;
COMMIT;"""
        check('普通LOGIN合法finish写收据及effect',finish(60,'qa_worker',1,'done'),'qa_worker')
        check('普通finish确有发布',f"SELECT count(*) FROM design.effect_receipts WHERE operation_id='{u(60)}';",expected='1')
        check('普通重复finish不产生第二effect',finish(60,'qa_worker',1,'duplicate'),'qa_worker')
        check('取消后的研究finish拒绝发布',finish(70,'qa_research',1,'cancelled'),'qa_research')
        check('取消研究无effect',f"SELECT count(*) FROM design.effect_receipts WHERE operation_id='{u(70)}';",expected='0')
        sql(f"UPDATE design.operations SET state='running',cancel_requested_at=null WHERE id='{u(70)}'; UPDATE design.step_receipts SET fence=2 WHERE operation_id='{u(70)}';")
        check('迟到fence不能发布',finish(70,'qa_research',1,'late'),'qa_research')
        check('迟到研究无effect',f"SELECT count(*) FROM design.effect_receipts WHERE operation_id='{u(70)}';",expected='0')
        check('研究新fence合法finish',finish(70,'qa_research',2,'valid'),'qa_research')
        check('研究finish确有发布',f"SELECT count(*) FROM design.effect_receipts WHERE operation_id='{u(70)}';",expected='1')
        for actor in ['qa_worker','qa_research']:
            for label,statement in [('官方标签','SELECT * FROM design.outcomes;'),('人工决定','UPDATE design.decisions SET decision=decision;'),('业务模型路由','UPDATE design.model_routes SET revision=revision+1;'),('bid身份写入','UPDATE design.bids SET title=title;')]:
                check(actor+'不能越权'+label,statement,actor,error='42501')
        check('PUBLIC不能调用锁入口',"SELECT has_function_privilege('public','design.lock_worker_bid(uuid)','EXECUTE');",expected='f')
        result=dict(passed=True,scope='真实PostgreSQL非owner LOGIN与合成acquire/finish事务、研究预算账本；没有执行应用Worker',image=IMAGE,postgres_version=sql('SHOW server_version;'),checks=len(checks),check_names=checks,software_end_to_end_executed=False,input_sha256={f:hashlib.sha256(b).hexdigest() for f,b in inputs.items()},probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),statements=statements)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(passed=True,checks=len(checks)),ensure_ascii=False))
    finally:
        if started:
            cmd(['rm','-f','-v',name])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    run(args.snapshot,args.output)
