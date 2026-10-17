"""在自动销毁的无网络PostgreSQL容器中执行设计DDL；不连接应用或现有数据库。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DDL = ROOT / '05-技术与设计/detailed-design/core.sql'


def run(image):
    name = 'tender-design-check-' + uuid.uuid4().hex[:10]
    results = []
    created = False
    def docker(*args, data=None):
        p = subprocess.run(['docker', *args], input=data, text=True, capture_output=True, timeout=90)
        if p.returncode:
            raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    def sql(statement):
        return docker('exec', '-i', name, 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres', data=statement)
    def check(label, statement):
        sql(statement)
        results.append({'name': label, 'passed': True})
    def uid(n):
        return str(uuid.UUID(int=n))
    h = 'a' * 64
    try:
        docker('run', '-d', '--name', name, '--network', 'none', '--memory', '768m', '--cpus', '1',
               '--tmpfs', '/var/lib/postgresql/data:rw', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', image)
        created = True
        for attempt in range(80):
            try:
                docker('exec', name, 'pg_isready', '-U', 'postgres')
                break
            except RuntimeError:
                time.sleep(.25)
        else:
            raise RuntimeError('PostgreSQL did not become ready')
        version = sql('SHOW server_version;')
        sql(DDL.read_text())
        results.append({'name': 'DDL在真实PostgreSQL建库成功', 'passed': True})
        # Helpers live only in this disposable database.
        sql("""
CREATE FUNCTION public.expect_error(statement text, expected_state text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  BEGIN EXECUTE statement;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = expected_state THEN RETURN; END IF;
    RAISE;
  END;
  RAISE EXCEPTION 'expected failure did not occur: %', expected_state;
END $$;
CREATE FUNCTION public.assert_equal(actual bigint, expected bigint) RETURNS void LANGUAGE plpgsql AS $$
BEGIN IF actual <> expected THEN RAISE EXCEPTION 'expected %, got %', expected, actual; END IF; END $$;
""")
        def reject(label, statement, state):
            check(label, 'SELECT public.expect_error($test$' + statement + '$test$,\'' + state + "');")
        sql(f"""
SET search_path=design,public;
INSERT INTO members(id,display_name) VALUES('{uid(1)}','成员甲'),('{uid(2)}','成员乙');
INSERT INTO bids(id,title,settings,created_by) VALUES('{uid(10)}','项目甲','{{}}','{uid(1)}'),('{uid(11)}','项目乙','{{}}','{uid(1)}');
INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES('{uid(20)}','{h}','original/a',12,'application/pdf');
INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES
('{uid(30)}','{uid(10)}','{uid(20)}','body.pdf',now(),'bid_body','{uid(1)}'),
('{uid(31)}','{uid(11)}','{uid(20)}','other.pdf',now(),'bid_body','{uid(1)}'),
('{uid(32)}','{uid(10)}','{uid(20)}','report.pdf',now(),'review_report','{uid(1)}');
INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES('{uid(40)}','{uid(10)}',1,'{{}}','{h}');
INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES('{uid(50)}','{uid(1)}','review','{uid(10)}','key','{h}');
INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{uid(60)}','{uid(10)}','{uid(50)}','review','{h}','running','handler-1');
INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at)
VALUES('{uid(70)}','{uid(10)}','{uid(40)}','{uid(30)}','{h}','{h}','{{}}',now());
INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES('{uid(80)}','{uid(10)}','{uid(70)}','{uid(60)}');
INSERT INTO step_receipts(operation_id,step_key,input_digest,fence,attempt_count,state,heartbeat_at) VALUES('{uid(60)}','item-1','{h}',1,1,'running',now());
INSERT INTO stream_heads(id,bid_id) VALUES('{uid(90)}','{uid(10)}');
""")
        # search_path must be set per invocation, not inherited from a prior psql process.
        def reject_design(label, statement, state):
            reject(label, 'SET LOCAL search_path=design,public; ' + statement, state)
        reject_design('跨项目正文外键拒绝', f"INSERT INTO review_inputs VALUES('{uid(71)}','{uid(10)}','{uid(40)}','{uid(31)}','{h}','{h}','{{}}',now())", '23503')
        reject_design('相同命令幂等键不可重复', f"INSERT INTO commands VALUES('{uid(51)}','{uid(1)}','review','{uid(10)}','key','{h}',null,now())", '23505')
        reject_design('同输入活跃操作不可重复', f"INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{uid(61)}','{uid(10)}','{uid(50)}','review','{h}','queued','v1')", '23505')
        reject_design('已冻结输入不可覆盖', f"UPDATE review_inputs SET manifest='{{\"overwritten\":true}}' WHERE id='{uid(70)}'", '55000')
        reject_design('原件不可删除', f"DELETE FROM document_versions WHERE id='{uid(30)}'", '55000')
        reject_design('非法摘要拒绝', f"INSERT INTO blobs VALUES('{uid(21)}','invalid','bad',1,'x',now())", '23514')
        reject_design('终态必须有结束时刻', f"UPDATE operations SET state='succeeded' WHERE id='{uid(60)}'", '23514')
        reject_design('取消请求必须记时刻', f"UPDATE operations SET state='cancel_requested' WHERE id='{uid(60)}'", '23514')
        reject_design('标准登记不得把报告当正文', f"INSERT INTO submission_records(id,bid_id,body_version_id,review_id,kind,gate_snapshot,evidence,submitted_at,created_by) VALUES('{uid(100)}','{uid(10)}','{uid(32)}','{uid(80)}','standard','{{}}','{{}}',now(),'{uid(1)}')", '23514')
        check('同正文标准提交可登记', f"INSERT INTO design.submission_records(id,bid_id,body_version_id,review_id,kind,gate_snapshot,evidence,submitted_at,created_by) VALUES('{uid(101)}','{uid(10)}','{uid(30)}','{uid(80)}','standard','{{}}','{{}}',now(),'{uid(1)}')")
        check('线下事实可以忠实补录错传报告', f"INSERT INTO design.submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES('{uid(102)}','{uid(10)}','{uid(32)}','offline_fact','{{}}',now(),'{uid(1)}')")
        reject_design('一个项目只能有一个有效实交头', f"INSERT INTO submission_heads VALUES('{uid(10)}','{uid(101)}',1),('{uid(10)}','{uid(102)}',2)", '23505')
        check('旧fence不能提交阶段结果', f"UPDATE design.step_receipts SET fence=2 WHERE operation_id='{uid(60)}'; WITH x AS (UPDATE design.step_receipts SET state='succeeded' WHERE operation_id='{uid(60)}' AND fence=1 RETURNING 1) SELECT public.assert_equal(count(*),0) FROM x;")
        check('取消后正常完成回调无法发布', f"UPDATE design.operations SET state='cancel_requested',cancel_requested_at=now() WHERE id='{uid(60)}'; WITH x AS (UPDATE design.operations SET state='succeeded',finished_at=now() WHERE id='{uid(60)}' AND state='running' RETURNING 1) SELECT public.assert_equal(count(*),0) FROM x;")
        # Real concurrent transactions, each calling a distinct psql process.
        def effect(_):
            sql(f"INSERT INTO design.effect_receipts VALUES('{uid(60)}','publish-result','{{\"ok\":true}}',now()) ON CONFLICT DO NOTHING;")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(effect, range(24)))
        check('24份重复效果请求只保留一份收据', 'SELECT public.assert_equal(count(*),1) FROM design.effect_receipts;')
        def event(_):
            sql(f"BEGIN; WITH head AS (UPDATE design.stream_heads SET last_seq=last_seq+1 WHERE id='{uid(90)}' RETURNING id,last_seq) INSERT INTO design.stream_events SELECT id,last_seq,'{uuid.uuid4()}','operation.updated','{{}}',now() FROM head; COMMIT;")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(event, range(24)))
        check('24个并发事件序号连续且无重复', 'SELECT public.assert_equal(count(*),24),public.assert_equal(min(seq),1),public.assert_equal(max(seq),24) FROM design.stream_events;')
        sql(f"INSERT INTO design.proposed_actions VALUES('{uid(110)}','{uid(10)}',1,'{h}',now()+interval '1 hour','{{}}'); INSERT INTO design.action_consumptions VALUES('{uid(110)}','{uid(50)}','{uid(1)}','{{}}',now());")
        reject_design('不同成员不能重复消费同一提案', f"INSERT INTO action_consumptions VALUES('{uid(110)}','{uid(50)}','{uid(2)}','{{}}',now())", '23505')
        sql(f"""
INSERT INTO design.drafts VALUES('{uid(130)}','{uid(10)}',1),('{uid(131)}','{uid(10)}',1);
INSERT INTO design.sections VALUES('{uid(140)}','{uid(10)}','{uid(130)}',1),('{uid(141)}','{uid(10)}','{uid(131)}',1);
INSERT INTO design.section_revisions VALUES('{uid(150)}','{uid(10)}','{uid(131)}','{uid(141)}',1,'{h}','{{}}','{h}','{uid(1)}',now()),
('{uid(151)}','{uid(10)}','{uid(130)}','{uid(140)}',1,'{h}','{{}}','{h}','{uid(1)}',now());
INSERT INTO design.draft_snapshots VALUES('{uid(160)}','{uid(10)}','{uid(130)}',1,'{{}}','{h}',now());
""")
        reject_design('同项目不同草稿的章节也不能混入快照',f"INSERT INTO draft_snapshot_sections VALUES('{uid(160)}','{uid(10)}','{uid(130)}','{uid(150)}',0)",'23503')
        check('同一草稿的已保存章节可进入快照',f"INSERT INTO design.draft_snapshot_sections VALUES('{uid(160)}','{uid(10)}','{uid(130)}','{uid(151)}',0)")
        check('事务回滚不遗留outbox', f"BEGIN; INSERT INTO design.outbox(id,topic,deduplication_key,payload) VALUES('{uid(120)}','next','unique-next','{{}}'); ROLLBACK; SELECT public.assert_equal(count(*),0) FROM design.outbox;")
        tables = sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='design';")
        digest = docker('image','inspect',image,'--format','{{index .RepoDigests 0}}')
        return {'passed':True,'scope':'独立关系模型及SQL事务机制；不连接产品API、不运行Celery或真实模型','executed_at':datetime.now(timezone.utc).isoformat(),'postgres_version':version,'image':digest,'table_count':int(tables),'ddl_sha256':hashlib.sha256(DDL.read_bytes()).hexdigest(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':results,'product_end_to_end_executed':False,'broker_faults_executed':False}
    finally:
        if created:
            docker('rm','-f','-v',name)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='postgres:17')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.image)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'checks':len(result['checks']),'postgres_version':result['postgres_version']},ensure_ascii=False))
