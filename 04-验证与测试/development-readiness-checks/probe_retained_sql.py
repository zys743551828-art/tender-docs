"""隔离PG验证新增设计约束和费用预留；只使用合成记录。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'
SCRIPTS=['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'


def run():
    name='tender-readiness-pg-'+uuid.uuid4().hex[:8]
    created=False
    checks=[]
    # 先冻结SQL字节，记录与真正执行的内容一致。
    inputs={n:(DESIGN/n).read_bytes() for n in SCRIPTS}
    def cmd(args,stdin=None):
        result=subprocess.run(args,input=stdin,text=True,capture_output=True,timeout=90)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()
    def sql(text):
        return cmd(['docker','exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres'],text)
    def u(i):return str(uuid.UUID(int=i))
    def check(label,text):
        sql(text)
        checks.append(label)
    def reject(label,text,state):
        check(label,"BEGIN; SET CONSTRAINTS ALL IMMEDIATE; SELECT public.expect_error($case$"+text+"$case$,'"+state+"'); COMMIT;")
    h='a'*64
    try:
        cmd(['docker','run','-d','--name',name,'--network','none','--memory','1g','--tmpfs','/var/lib/postgresql/data:rw','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE])
        created=True
        for _ in range(100):
            try:
                cmd(['docker','exec',name,'pg_isready','-U','postgres'])
                break
            except RuntimeError:
                time.sleep(.2)
        else:
            raise RuntimeError('PG启动超时')
        for n,data in inputs.items():sql(data.decode())
        sql("""CREATE FUNCTION public.expect_error(statement text,want text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN BEGIN EXECUTE statement; EXCEPTION WHEN OTHERS THEN IF SQLSTATE=want THEN RETURN; END IF; RAISE; END; RAISE EXCEPTION 'expected error %',want; END $$;
CREATE FUNCTION public.assert_true(value boolean) RETURNS void LANGUAGE plpgsql AS $$ BEGIN IF value IS DISTINCT FROM true THEN RAISE EXCEPTION 'assertion failed'; END IF; END $$;""")
        sql(f"""INSERT INTO design.members VALUES('{u(1)}','合成成员',true);
INSERT INTO design.bids(id,title,settings,created_by) VALUES('{u(10)}','合成项目A','{{}}','{u(1)}'),('{u(11)}','合成项目B','{{}}','{u(1)}');
INSERT INTO design.blobs VALUES('{u(20)}','{h}','synthetic/file',1,'application/pdf',now());
INSERT INTO design.document_versions VALUES('{u(30)}','{u(10)}','{u(20)}','a.pdf',now(),'bid_body','{u(1)}',now()),('{u(31)}','{u(10)}','{u(20)}','b.pdf',now(),'bid_body','{u(1)}',now()),('{u(32)}','{u(11)}','{u(20)}','c.pdf',now(),'bid_body','{u(1)}',now());
INSERT INTO design.document_files VALUES('{u(100)}','{u(10)}','body',1,null),('{u(101)}','{u(10)}','body',1,null),('{u(102)}','{u(11)}','body',1,null);
INSERT INTO design.document_file_versions VALUES('{u(10)}','{u(100)}','{u(30)}',1,null),('{u(10)}','{u(101)}','{u(31)}',1,null),('{u(11)}','{u(102)}','{u(32)}',1,null);""")
        check('文件head可指向自身版本',f"UPDATE design.document_files SET head_version_id='{u(30)}',revision=2 WHERE id='{u(100)}';")
        reject('同bid也不能借用其他文件head',f"UPDATE design.document_files SET head_version_id='{u(31)}' WHERE id='{u(100)}'",'23503')
        reject('文件版本不能跨bid关联',f"INSERT INTO design.document_file_versions VALUES('{u(10)}','{u(100)}','{u(32)}',2,'{u(30)}')",'23503')
        reject('版本记录不可改写',f"UPDATE design.document_file_versions SET revision=3 WHERE version_id='{u(30)}'",'55000')
        sql(f"""INSERT INTO design.upload_batches VALUES('{u(200)}','{u(10)}',1,'open','{u(1)}');
INSERT INTO design.uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at) VALUES('{u(201)}','{u(10)}','{u(200)}','x.pdf','body','{h}',1,'synthetic/upload','authorized',now()+interval '1 day');""")
        reject('single模式必须只有一片',f"INSERT INTO design.upload_transfers VALUES('{u(201)}','{u(10)}','{u(100)}',2,'single',null,1,2,1,null)",'23514')
        sql(f"INSERT INTO design.upload_transfers VALUES('{u(201)}','{u(10)}','{u(100)}',2,'single',null,1,1,1,null);")
        reject('上传分片编号不得为0',f"INSERT INTO design.upload_parts VALUES('{u(201)}',0,1,'etag',now())",'23514')
        sql(f"""INSERT INTO design.fact_sets VALUES('{u(300)}','{u(10)}','{h}'),('{u(301)}','{u(11)}','{h}'),('{u(302)}','{u(10)}','{h}');
INSERT INTO design.fact_items VALUES('{u(303)}','{u(10)}','{u(300)}','本项目','全标段','工期','180','日历天',ARRAY[]::uuid[]);
INSERT INTO design.fact_heads VALUES('{u(10)}',null,0);
INSERT INTO design.fact_set_changes VALUES('{u(304)}','{u(10)}','{u(302)}','{u(300)}','{u(303)}','withdraw','原值错误','{u(1)}',now());""")
        reject('事实head不得跨bid',f"UPDATE design.fact_heads SET fact_set_id='{u(301)}',revision=1 WHERE bid_id='{u(10)}'",'23503')
        reject('事实撤回链不可原地修改',f"UPDATE design.fact_set_changes SET reason='覆盖旧理由' WHERE id='{u(304)}'",'55000')
        check('全部撤回仍保留非零修订的空集',f"UPDATE design.fact_heads SET fact_set_id='{u(302)}',revision=1 WHERE bid_id='{u(10)}'; SELECT public.assert_true((SELECT revision=1 AND NOT EXISTS(SELECT 1 FROM design.fact_items WHERE fact_set_id='{u(302)}') FROM design.fact_heads WHERE bid_id='{u(10)}'));")
        sql(f"""INSERT INTO design.material_snapshots VALUES('{u(400)}','{u(10)}',1,'{{}}','{h}',now());
INSERT INTO design.rule_sets VALUES('{u(401)}','{u(10)}','{u(400)}',1,'{h}','complete');
INSERT INTO design.rule_items VALUES('{u(402)}','{u(10)}','{u(401)}','mandatory','合成规则','[]','[]',null,ARRAY['{u(30)}']::uuid[],'[]');
INSERT INTO design.commands VALUES('{u(410)}','{u(1)}','review','{u(10)}','synthetic','{h}',null,now());
INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{u(411)}','{u(10)}','{u(410)}','review','{h}','running','fixture');
INSERT INTO design.review_inputs VALUES('{u(412)}','{u(10)}','{u(400)}','{u(30)}','{h}','{h}','{{}}',now());
INSERT INTO design.review_runs VALUES('{u(413)}','{u(10)}','{u(412)}','{u(411)}',now());""")
        reject('失败行不能判满足',f"INSERT INTO design.matrix_checks VALUES('{u(10)}','{u(413)}','{u(402)}','failed','satisfied','MODEL_ERROR',ARRAY[]::uuid[],1)",'23514')
        check('成功执行但无法判断可独立表示',f"INSERT INTO design.matrix_checks VALUES('{u(10)}','{u(413)}','{u(402)}','succeeded','unable_to_judge',null,ARRAY[]::uuid[],1);")
        reject('失败行必须带错误原因',f"UPDATE design.matrix_checks SET state='failed',verdict=null,error_code=null WHERE review_id='{u(413)}'",'23514')
        sql(f"""INSERT INTO design.experiments VALUES('{u(500)}','{h}','{u(20)}','{u(20)}','{h}',1,'created',null);
INSERT INTO design.experiment_previews VALUES('{u(501)}','{u(500)}',1,'{h}','{{}}','{h}',3,100,3,10,'CNY',now()+interval '1 day','{u(1)}');
INSERT INTO design.research_runs(id,experiment_id,preview_id,revision,manifest_digest,state,fee_limit,currency) VALUES('{u(502)}','{u(500)}','{u(501)}',1,'{h}','running',10,'CNY');""")
        check('预算可预留且超限预留拒绝',f"SELECT public.assert_true(design.reserve_research_fee('{u(502)}',6)); SELECT public.assert_true(NOT design.reserve_research_fee('{u(502)}',5)); SELECT public.assert_true((SELECT reserved_fee=6 FROM design.research_runs WHERE id='{u(502)}'));")
        reject('预算不接受NaN',f"SELECT design.reserve_research_fee('{u(502)}','NaN')",'23514')
        check('取消请求后不得新增调用预算',f"UPDATE design.research_runs SET cancel_requested_at=now() WHERE id='{u(502)}'; SELECT public.assert_true(NOT design.reserve_research_fee('{u(502)}',1));")
        check('真实超额账单如实记录并暂停',f"UPDATE design.research_runs SET reserved_fee=0,spent_fee=12,state='paused' WHERE id='{u(502)}'; SELECT public.assert_true((SELECT spent_fee=12 FROM design.research_runs WHERE id='{u(502)}'));")
        sql(f"UPDATE design.research_runs SET spent_fee=0,reserved_fee=0,state='running',cancel_requested_at=null WHERE id='{u(502)}';")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:sql(f"SELECT design.reserve_research_fee('{u(502)}',6);"),range(2)))
        assert sorted(results)==['f','t'],results
        check('并发预留只能一个成功且不超预算',f"SELECT public.assert_true((SELECT reserved_fee=6 FROM design.research_runs WHERE id='{u(502)}'));")
        reject('预览manifest不可改写',f"UPDATE design.experiment_previews SET manifest_digest=repeat('b',64) WHERE id='{u(501)}'",'55000')
        reject('研究调度角色不能读官方标签','SET LOCAL ROLE tender_research_worker; SELECT * FROM design.outcomes','42501')
        reject('研究调度角色不能发布业务模型','SET LOCAL ROLE tender_research_worker; UPDATE design.model_routes SET revision=revision+1','42501')
        reject('评分角色不能读取影子研究结果','SET LOCAL ROLE tender_score_read; SELECT * FROM design.shadow_comparisons','42501')
        reject('规则抽取worker不能伪造人工决定','SET LOCAL ROLE tender_worker; UPDATE design.decisions SET decision=decision','42501')
        version=sql('SHOW server_version;')
        tables=int(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('design','retrieval') AND table_type='BASE TABLE'"))
        assert tables==101,tables
        return dict(passed=True,scope='真实PG的新增DDL约束、预算预留和角色隔离；不是业务工作流验收',executed_at=datetime.now(timezone.utc).isoformat(),
                    postgres_version=version,tables=tables,checks=len(checks),check_names=checks,image=IMAGE,
                    sql_sha256={n:hashlib.sha256(data).hexdigest() for n,data in inputs.items()},software_end_to_end_executed=False)
    finally:
        if created:cmd(['docker','rm','-f','-v',name])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('sql_sha256','check_names')},ensure_ascii=False))
