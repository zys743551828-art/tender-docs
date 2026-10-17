"""在临时pgvector容器执行全新建库、过滤、角色和租约机制；无真实资料／Embedding。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid
from datetime import datetime,timezone

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'
SCRIPTS=['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'


def run():
    name='tender-implementation-pg-'+uuid.uuid4().hex[:8]
    created=False;checks=[]
    def command(args,stdin=None):
        p=subprocess.run(args,input=stdin,text=True,capture_output=True,timeout=90)
        if p.returncode:raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    def sql(statement):return command(['docker','exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres'],statement)
    def u(i):return str(uuid.UUID(int=i))
    def check(label,statement):sql(statement);checks.append({'name':label,'passed':True})
    def reject(label,statement,state):check(label,"SELECT public.expect_error($case$"+statement+"$case$,'"+state+"');")
    h='a'*64
    try:
        command(['docker','run','-d','--name',name,'--network','none','--memory','1g','--tmpfs','/var/lib/postgresql/data:rw','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE]);created=True
        for _ in range(100):
            try:command(['docker','exec',name,'pg_isready','-U','postgres']);break
            except RuntimeError:time.sleep(.2)
        else:raise RuntimeError('PG not ready')
        for script in SCRIPTS:check('初始化 '+script,(DESIGN/script).read_text())
        version=sql('SHOW server_version;');vector_version=sql("SELECT extversion FROM pg_extension WHERE extname='vector';")
        sql("""CREATE FUNCTION public.eq(actual bigint,want bigint) RETURNS void LANGUAGE plpgsql AS $$ BEGIN IF actual<>want THEN RAISE EXCEPTION 'expected %, actual %',want,actual; END IF; END $$;
CREATE FUNCTION public.expect_error(statement text,want text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN BEGIN EXECUTE statement; EXCEPTION WHEN OTHERS THEN IF SQLSTATE=want THEN RETURN; END IF; RAISE; END; RAISE EXCEPTION 'expected error %',want; END $$;""")
        sql(f"""INSERT INTO design.members VALUES('{u(1)}','测试成员',true);
INSERT INTO design.bids(id,title,settings,created_by) VALUES('{u(10)}','合成测试项目','{{}}','{u(1)}');
INSERT INTO design.blobs VALUES('{u(20)}','{h}','synthetic/source',1,'application/pdf',now());
INSERT INTO design.document_versions VALUES('{u(30)}','{u(10)}','{u(20)}','synthetic.pdf','2025-01-01','bid_body','{u(1)}',now());
INSERT INTO design.material_snapshots VALUES('{u(40)}','{u(10)}',1,'{{}}','{h}',now());
INSERT INTO design.commands VALUES('{u(50)}','{u(1)}','review','{u(10)}','fixture','{h}',null,now());
INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{u(60)}','{u(10)}','{u(50)}','review','{h}','running','handler-1');
INSERT INTO design.review_inputs VALUES('{u(70)}','{u(10)}','{u(40)}','{u(30)}','{h}','{h}','{{}}','2026-01-01');
INSERT INTO design.review_runs VALUES('{u(80)}','{u(10)}','{u(70)}','{u(60)}',now());
INSERT INTO design.parse_runs VALUES('{u(200)}','{u(10)}','{u(30)}','{h}','{h}',1,'complete','[]',now());
INSERT INTO design.pages VALUES('{u(10)}','{u(200)}',1,null,595,842,0,'usable','[]');
INSERT INTO design.evidence_blocks VALUES('{u(201)}','{u(10)}','{u(200)}',1,0,'text','合成 排水 措施',null,null,'{h}');
INSERT INTO retrieval.embedding_profiles VALUES('{u(500)}','synthetic-1024','fixture','{h}',1024,'cosine',true,'{h}');""")
        policies=['prebid_content','official_label','prebid_content','prebid_content','prebid_content','prebid_content','prebid_content']
        for i,policy in enumerate(policies,301):
            known='2030-01-01' if i==304 else '2025-01-01';status='withdrawn' if i==305 else 'published'
            vector=[0,1]+[0]*1022 if i==301 else [1]+[0]*1023
            sql(f"""INSERT INTO retrieval.source_nodes VALUES('{u(i)}','{u(30)}','document','{policy}','{known}','{h}');
INSERT INTO retrieval.source_heads VALUES('{u(i)}',1,'{status}','2020-01-01',null);
INSERT INTO retrieval.chunks(id,source_id,parse_id,physical_page,ordinal,chapter_path,evidence_ids,text_content,lexemes,token_count,digest) VALUES('{u(i+100)}','{u(i)}','{u(200)}',1,{i-301},ARRAY['施工'],ARRAY['{u(201)}']::uuid[],'合成排水措施','合成 排水 措施',6,'{h}');
INSERT INTO retrieval.embeddings VALUES('{u(i+100)}','{u(500)}','{json.dumps(vector)}');""")
        sql(f"INSERT INTO retrieval.lineage_edges VALUES('{u(303)}','{u(302)}'),('{u(307)}','{u(306)}'); INSERT INTO retrieval.source_groups VALUES('{u(900)}','synthetic forbidden group'); INSERT INTO retrieval.source_group_memberships VALUES('{u(306)}','{u(900)}');")
        allowed="ARRAY["+','.join("'"+u(i)+"'" for i in range(301,308))+"]::uuid[]"
        eligible=f"retrieval.eligible_chunks({allowed},'2026-01-01',ARRAY['{u(900)}']::uuid[],true)"
        check('标签、派生标签、未来、停用、禁止组及派生组均排除',f'SELECT public.eq(count(*),1) FROM {eligible};')
        q=json.dumps([1]+[0]*1023)
        check('最相似的禁止来源不能挤入允许结果',f"SELECT public.eq(count(*),1) FROM (SELECT c.id FROM {eligible} c JOIN retrieval.embeddings e ON e.chunk_id=c.id WHERE e.profile_id='{u(500)}' ORDER BY e.embedding <=> '{q}'::vector LIMIT 8) x WHERE id='{u(401)}';")
        check('中文预分词FTS与允许集共同生效',f"SELECT public.eq(count(*),1) FROM {eligible} WHERE lex_tsv @@ plainto_tsquery('simple','排水');")
        reject('来源图循环被拒绝',f"INSERT INTO retrieval.lineage_edges VALUES('{u(302)}','{u(303)}')",'23514')
        reject('错误向量维度被拒绝',f"UPDATE retrieval.embeddings SET embedding='[1,0,0]' WHERE chunk_id='{u(401)}'",'22000')
        reject('非单位向量被拒绝',f"INSERT INTO retrieval.embeddings VALUES('{u(401)}','{u(501)}','{json.dumps([2]+[0]*1023)}')",'23514')
        # Bad norm is tested before profile FK; missing profile would produce a different SQLSTATE.
        sql(f"INSERT INTO retrieval.score_input_sources VALUES('{u(80)}','{u(301)}'),('{u(80)}','{u(302)}'),('{u(80)}','{u(303)}');")
        check('评分角色只能取得冻结白名单中的合格正文',f"SET ROLE tender_score_read; SELECT public.eq(count(*),1) FROM retrieval.formal_score_context('{u(80)}'); RESET ROLE;")
        reject('评分角色不能直接读取官方结果','SET LOCAL ROLE tender_score_read; SELECT * FROM design.outcomes','42501')
        reject('评分角色不能直接读取聊天','SET LOCAL ROLE tender_score_read; SELECT * FROM design.messages','42501')
        reject('评分角色不能自行调用宽用途检索',f"SET LOCAL ROLE tender_score_read; SELECT * FROM retrieval.eligible_chunks({allowed},now(),ARRAY[]::uuid[],false)",'42501')
        sql(f"INSERT INTO design.outbox(id,operation_id,topic,deduplication_key,payload) VALUES('{u(700)}','{u(60)}','tender.task.review.v1','synthetic-event','{{}}');")
        check('Outbox第一次领取一条',f"SELECT public.eq(count(*),1) FROM design.claim_outbox(100,'{u(701)}');")
        check('有效租约下第二领取者不能取走',f"SELECT public.eq(count(*),0) FROM design.claim_outbox(100,'{u(702)}');")
        check('Outbox错误token不能确认',f"SELECT public.eq(design.ack_outbox('{u(700)}','{u(702)}')::integer,0);")
        check('Outbox正确token可以续租并确认',f"SELECT public.eq(design.renew_outbox('{u(700)}','{u(701)}')::integer,1); SELECT public.eq(design.ack_outbox('{u(700)}','{u(701)}')::integer,1);")
        sql(f"INSERT INTO design.step_receipts(operation_id,step_key,input_digest,fence,attempt_count,state,owner_token,lease_until) VALUES('{u(60)}','judge','{h}',2,1,'running','{u(800)}',now()+interval '120 seconds');")
        check('过期fence不能续租',f"SELECT public.eq(design.renew_step('{u(60)}','judge','{h}',1,'{u(800)}')::integer,0);")
        check('当前fence及owner可以续租',f"SELECT public.eq(design.renew_step('{u(60)}','judge','{h}',2,'{u(800)}')::integer,1);")
        sql(f"INSERT INTO design.submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES('{u(850)}','{u(10)}','{u(30)}','offline_fact','{{}}',now(),'{u(1)}');")
        reject('成绩缺满分被拒绝',f"INSERT INTO design.outcomes(id,bid_id,submission_id,score_kind,value,source_version_id,known_at,created_by) VALUES('{u(851)}','{u(10)}','{u(850)}','technical_total',80,'{u(30)}',now(),'{u(1)}')",'23514')
        check('真实零分与未知可分别保存',f"INSERT INTO design.outcomes(id,bid_id,submission_id,score_kind,value,maximum,source_version_id,known_at,created_by) VALUES('{u(852)}','{u(10)}','{u(850)}','technical_total',0,100,'{u(30)}',now(),'{u(1)}'),('{u(853)}','{u(10)}','{u(850)}','unknown',null,null,'{u(30)}',now(),'{u(1)}');")
        check('停用head后既有向量不能新增可用引用',f"UPDATE retrieval.source_heads SET status='withdrawn',revision=2 WHERE source_id='{u(301)}'; SELECT public.eq(count(*),0) FROM {eligible};")
        sql(f"INSERT INTO design.model_artifacts VALUES('{u(950)}','{u(20)}','{h}','{{}}','{h}',now()),('{u(951)}','{u(20)}','{h}','{{}}','{h}',now()); INSERT INTO design.model_acceptances VALUES('{u(960)}','{u(950)}','{h}','{h}','{u(20)}','accepted','{u(1)}'),('{u(961)}','{u(951)}','{h}','{h}','{u(20)}','rejected','{u(1)}');")
        reject('不能借用其他模型的验收发布',f"INSERT INTO design.model_routes(route_key,model_id,acceptance_id,revision) VALUES('wrong-model','{u(951)}','{u(960)}',1)",'23503')
        reject('被拒绝验收不能成为业务路由',f"INSERT INTO design.model_routes(route_key,model_id,acceptance_id,revision) VALUES('rejected-model','{u(951)}','{u(961)}',1)",'23503')
        check('回退至不可用保留路由修订',f"INSERT INTO design.model_routes(route_key,model_id,acceptance_id,revision) VALUES('test-route','{u(950)}','{u(960)}',1); UPDATE design.model_routes SET model_id=null,acceptance_id=null,revision=2 WHERE route_key='test-route'; SELECT public.eq(revision,2) FROM design.model_routes WHERE route_key='test-route' AND model_id IS NULL AND acceptance_id IS NULL;")
        tables=int(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('design','retrieval') AND table_type='BASE TABLE';"))
        return {'passed':True,'scope':'真实PG与pgvector上的建库、合成向量过滤、权限和租约；不代表真实RAG或评分效果','executed_at':datetime.now(timezone.utc).isoformat(),'postgres_version':version,'pgvector_version':vector_version,'image':IMAGE,'table_count':tables,'checks':checks,'inputs':{str((DESIGN/x).relative_to(ROOT)):hashlib.sha256((DESIGN/x).read_bytes()).hexdigest() for x in SCRIPTS},'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'real_embedding_executed':False,'product_end_to_end_executed':False}
    finally:
        if created:command(['docker','rm','-f','-v',name])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'tables':result['table_count'],'checks':len(result['checks']),'postgres':result['postgres_version'],'pgvector':result['pgvector_version']},ensure_ascii=False))
