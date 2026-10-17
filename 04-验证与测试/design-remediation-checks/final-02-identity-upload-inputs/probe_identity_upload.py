"""A02/A03真实PG并发与约束探针，测试侧合成协议，不是应用API实现。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse, hashlib, json, os, subprocess, time, uuid
HERE=Path(__file__).resolve().parent
DESIGN=HERE.parents[1]/'05-技术与设计/detailed-design'
FILES=['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
H='a'*64
def u(n):return str(uuid.UUID(int=n))

def run(snapshot,output):
    snapshot.mkdir(exist_ok=True,parents=True)
    (snapshot/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    inputs={f:(DESIGN/f).read_bytes() for f in FILES}
    for f,b in inputs.items():(snapshot/f).write_bytes(b)
    vectors=(DESIGN/'review-identity-vectors.json').read_bytes();(snapshot/'review-identity-vectors.json').write_bytes(vectors)
    ids={v['name']:v['expected']['execution_digest'] for v in json.loads(vectors)['vectors']}
    env=dict(os.environ)
    for k in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(k,None)
    name='tender-test-identity-'+uuid.uuid4().hex[:10];checks=[];trace=[]
    def cmd(args,text=None):return subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],input=text,text=True,capture_output=True,timeout=90,env=env)
    def sql(text,actor='postgres',error=None):
        p=cmd(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose','-h','127.0.0.1','-U',actor,'-d','postgres'],text)
        trace.append(dict(actor=actor,sql=text,stdout=p.stdout.strip(),stderr=p.stderr.strip(),exit_code=p.returncode))
        if error:assert p.returncode and error in p.stderr,trace[-1]
        elif p.returncode:raise RuntimeError(trace[-1])
        return p.stdout.strip()
    def check(label,text,actor='postgres',expected=None,error=None):
        got=sql(text,actor,error)
        if expected is not None:assert got==expected,(label,got,expected)
        checks.append(label)
    started=False
    try:
        p=cmd(['run','-d','--name',name,'--network','none','--memory','1g','--tmpfs','/var/lib/postgresql/data:rw','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE]);assert not p.returncode,p.stderr;started=True
        for _ in range(100):
            if not cmd(['exec',name,'pg_isready','-U','postgres']).returncode:break
            time.sleep(.2)
        for f in FILES:sql(inputs[f].decode())
        sql("CREATE ROLE qa_api LOGIN INHERIT NOSUPERUSER; GRANT tender_api TO qa_api;")
        sql(f"INSERT INTO design.members VALUES('{u(1)}','合成成员',true); INSERT INTO design.bids(id,title,settings,created_by) VALUES('{u(10)}','合成A','{{}}','{u(1)}'),('{u(11)}','合成B','{{}}','{u(1)}');")
        # 不同入口统一bid锁并查询相同execution身份，真实并发连接。
        def request(n,digest):
            return f"""BEGIN; SELECT id FROM design.bids WHERE id='{u(10)}' FOR UPDATE;
INSERT INTO design.commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES('{u(n+1)}','{u(1)}','RequestReview','{u(10)}','entry-{n}','{H}');
WITH existing AS (SELECT id FROM design.operations WHERE bid_id='{u(10)}' AND kind='review' AND input_digest='{digest}' AND state IN ('queued','running','cancel_requested')),
made AS (INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) SELECT '{u(n)}','{u(10)}','{u(n+1)}','review','{digest}','queued','test' WHERE NOT EXISTS(SELECT 1 FROM existing) RETURNING id)
SELECT id FROM existing UNION ALL SELECT id FROM made; COMMIT;"""
        with ThreadPoolExecutor(max_workers=3) as pool:res=list(pool.map(lambda n:sql(request(n,ids['initial']),'qa_api'),[1000,1010,1020]))
        assert len(set(r.splitlines()[-1] for r in res))==1,res;checks.append('自动/按钮/聊天合成并发入口共享同一operation')
        check('相同执行身份在途只有一条',f"SELECT count(*) FROM design.operations WHERE input_digest='{ids['initial']}';",expected='1')
        check('显式nonce可独立建立执行',request(1030,ids['explicit_rerun']),'qa_api')
        check('有效来源改变可建立新执行',request(1040,ids['source_effect_changed']),'qa_api')
        check('三种执行身份并存',"SELECT count(*) FROM design.operations;",expected='3')
        sql(f"""INSERT INTO design.blobs VALUES('{u(20)}','{H}','fixture0',1,'application/pdf',now()),('{u(21)}','{'b'*64}','fixture1',1,'application/pdf',now()),('{u(22)}','{'c'*64}','fixture2',1,'application/pdf',now());
INSERT INTO design.document_versions VALUES('{u(30)}','{u(10)}','{u(20)}','old.pdf',now(),'bid_body','{u(1)}',now()),('{u(31)}','{u(10)}','{u(21)}','new1.pdf',now(),'bid_body','{u(1)}',now()),('{u(32)}','{u(10)}','{u(22)}','new2.pdf',now(),'bid_body','{u(1)}',now());
INSERT INTO design.document_files VALUES('{u(100)}','{u(10)}','body',2,null),('{u(101)}','{u(11)}','body',1,null),('{u(102)}','{u(10)}','tender',1,null);
INSERT INTO design.document_file_versions VALUES('{u(10)}','{u(100)}','{u(30)}',1,null); UPDATE design.document_files SET head_version_id='{u(30)}' WHERE id='{u(100)}';""")
        for batch,upload,binding,version in [(200,201,202,31),(210,211,212,32)]:
            sql(f"""INSERT INTO design.commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES('{u(binding+1)}','{u(1)}','CreateUpload','{u(upload)}','initial','{H}');
INSERT INTO design.upload_batches VALUES('{u(batch)}','{u(10)}',1,'open','{u(1)}');
INSERT INTO design.uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at,verified_version_id,verified_retain_until) VALUES('{u(upload)}','{u(10)}','{u(batch)}','fixture.pdf','body','{H}',1,'upload-{upload}','verified',now()+interval '1 day','{u(version)}',now()+interval '7 days');
INSERT INTO design.upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,reason,command_id,created_by) VALUES('{u(binding)}','{u(10)}','{u(upload)}','{u(100)}',2,'初次确认','{u(binding+1)}','{u(1)}');
INSERT INTO design.upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,part_size,part_count,revision,current_binding_id) VALUES('{u(upload)}','{u(10)}','{u(100)}',2,'single',1,1,1,'{u(binding)}');""")
        # 测试侧事务函数只表达已批准规约，固定角色为调用者；无网络/字节传输。
        sql("CREATE SCHEMA qa; GRANT USAGE ON SCHEMA qa TO qa_api;")
        helper=f"""CREATE FUNCTION qa.commit_upload(upload uuid,binding uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE b uuid; chosen design.upload_bindings; f design.document_files; t design.upload_transfers; v design.uploads;
BEGIN
 SELECT bid_id INTO b FROM design.uploads WHERE id=upload;
 PERFORM 1 FROM design.bids WHERE id=b FOR UPDATE;
 SELECT * INTO v FROM design.uploads WHERE id=upload FOR UPDATE;
 SELECT * INTO t FROM design.upload_transfers WHERE upload_id=upload FOR UPDATE;
 IF t.committed_binding_id IS NOT NULL THEN
 IF t.committed_binding_id=binding THEN RETURN 'REUSE'; END IF;
 RAISE EXCEPTION 'UPLOAD_NOT_REBINDABLE' USING ERRCODE='55000'; END IF;
 IF t.current_binding_id<>binding THEN RAISE EXCEPTION 'BINDING_STALE' USING ERRCODE='55000'; END IF;
 SELECT * INTO chosen FROM design.upload_bindings WHERE id=binding;
 SELECT * INTO f FROM design.document_files WHERE id=chosen.file_id FOR UPDATE;
 IF v.verified_retain_until<=clock_timestamp() THEN RAISE EXCEPTION 'VERIFIED_BYTES_EXPIRED' USING ERRCODE='55000'; END IF;
 IF f.revision<>chosen.expected_file_revision THEN RAISE EXCEPTION 'FILE_REVISION_CONFLICT' USING ERRCODE='40001'; END IF;
 INSERT INTO design.document_file_versions VALUES(b,f.id,v.verified_version_id,f.revision,f.head_version_id);
 UPDATE design.document_files SET revision=revision+1,head_version_id=v.verified_version_id WHERE id=f.id;
 UPDATE design.upload_transfers SET committed_binding_id=binding WHERE upload_id=upload;
 UPDATE design.upload_batches SET state='committed',revision=revision+1 WHERE id=v.batch_id;
 RETURN 'COMMITTED'; END $$;
CREATE FUNCTION qa.rebind(upload uuid,target_file uuid,expected bigint,new_id uuid,command uuid,key text) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE b uuid; t design.upload_transfers; v design.uploads; f design.document_files; replay jsonb;
BEGIN
 SELECT response INTO replay FROM design.commands WHERE actor_id='{u(1)}' AND command_kind='rebind' AND target_id=upload AND idempotency_key=key;
 IF FOUND THEN RETURN (replay->>'binding_id')::uuid; END IF;
 SELECT bid_id INTO b FROM design.uploads WHERE id=upload; PERFORM 1 FROM design.bids WHERE id=b FOR UPDATE;
 SELECT * INTO v FROM design.uploads WHERE id=upload FOR UPDATE;
 SELECT * INTO t FROM design.upload_transfers WHERE upload_id=upload FOR UPDATE;
 SELECT * INTO f FROM design.document_files WHERE id=target_file FOR UPDATE;
 IF f.bid_id<>b OR f.role<>v.role THEN RAISE EXCEPTION 'UPLOAD_TARGET_MISMATCH' USING ERRCODE='23514'; END IF;
 IF v.state<>'verified' OR t.committed_binding_id IS NOT NULL THEN RAISE EXCEPTION 'UPLOAD_NOT_REBINDABLE' USING ERRCODE='55000'; END IF;
 IF v.verified_retain_until<=clock_timestamp() THEN RAISE EXCEPTION 'VERIFIED_BYTES_EXPIRED' USING ERRCODE='55000'; END IF;
 IF f.revision<>expected THEN RAISE EXCEPTION 'FILE_REVISION_CONFLICT' USING ERRCODE='40001'; END IF;
 INSERT INTO design.commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest,response) VALUES(command,'{u(1)}','rebind',upload,key,'{H}',jsonb_build_object('binding_id',new_id));
 INSERT INTO design.upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,previous_binding_id,reason,command_id,created_by) VALUES(new_id,b,upload,target_file,expected,t.current_binding_id,'测试显式选择',command,'{u(1)}');
 UPDATE design.upload_transfers SET current_binding_id=new_id,revision=revision+1 WHERE upload_id=upload;
 INSERT INTO design.audit_entries(id,actor_id,bid_id,command_id,event_type,payload) VALUES(new_id,'{u(1)}',b,command,'upload.target_reconfirmed',jsonb_build_object('previous',t.current_binding_id,'next',new_id));
 RETURN new_id; END $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA qa TO qa_api;"""
        (snapshot/'test-protocol-helpers.sql').write_text(helper);sql(helper)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda pair:cmd(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','qa_api','-d','postgres'],f"SELECT qa.commit_upload('{u(pair[0])}','{u(pair[1])}');"),[(201,202),(211,212)]))
        assert sum(p.returncode==0 for p in results)==1,[(p.stdout,p.stderr) for p in results]
        winner,wb,loser,lb=(201,202,211,212) if not results[0].returncode else (211,212,201,202)
        checks.append('两个并发修改稿只有一个先推进head，另一个CAS冲突')
        check('并发后head只推进一次',f"SELECT revision FROM design.document_files WHERE id='{u(100)}';",expected='3')
        check('失败者核验对象和原目标保留',f"SELECT v.state='verified' AND t.file_id='{u(100)}' AND t.expected_file_revision=2 AND t.current_binding_id='{u(lb)}' AND t.committed_binding_id IS NULL FROM design.uploads v JOIN design.upload_transfers t ON v.id=t.upload_id WHERE v.id='{u(loser)}';",expected='t')
        check('重复成功commit返回原结果',f"SELECT qa.commit_upload('{u(winner)}','{u(wb)}');",'qa_api','REUSE')
        check('跨bid重绑定拒绝',f"SELECT qa.rebind('{u(loser)}','{u(101)}',1,'{u(300)}','{u(301)}','cross-bid');",'qa_api',error='23514')
        check('角色不符重绑定拒绝',f"SELECT qa.rebind('{u(loser)}','{u(102)}',1,'{u(300)}','{u(301)}','cross-role');",'qa_api',error='23514')
        check('已提交对象不能重绑定',f"SELECT qa.rebind('{u(winner)}','{u(100)}',3,'{u(300)}','{u(301)}','committed');",'qa_api',error='55000')
        sql(f"UPDATE design.uploads SET verified_retain_until=now()-interval '1 second' WHERE id='{u(loser)}';")
        check('已过期核验字节不能重绑定',f"SELECT qa.rebind('{u(loser)}','{u(100)}',3,'{u(300)}','{u(301)}','expired');",'qa_api',error='55000')
        sql(f"UPDATE design.uploads SET verified_retain_until=now()+interval '7 days' WHERE id='{u(loser)}';")
        check('无需重传字节显式绑定最新head',f"SELECT qa.rebind('{u(loser)}','{u(100)}',3,'{u(300)}','{u(301)}','valid');",'qa_api',u(300))
        check('同key重放不创建第二binding',f"SELECT qa.rebind('{u(loser)}','{u(100)}',3,'{u(302)}','{u(303)}','valid');",'qa_api',u(300))
        check('保留不可变原目标及历史binding链',f"SELECT t.expected_file_revision=2 AND t.current_binding_id='{u(300)}' AND b.expected_file_revision=3 AND b.previous_binding_id='{u(lb)}' AND (SELECT count(*)=2 FROM design.upload_bindings WHERE upload_id='{u(loser)}') FROM design.upload_transfers t JOIN design.upload_bindings b ON b.id=t.current_binding_id WHERE t.upload_id='{u(loser)}';",expected='t')
        check('不能用旧binding冒充当前选择',f"SELECT qa.commit_upload('{u(loser)}','{u(lb)}');",'qa_api',error='55000')
        # 二次竞争不静默刷新expected。
        sql(f"UPDATE design.document_files SET revision=4 WHERE id='{u(100)}';")
        check('重绑定后再次竞争仍CAS拒绝',f"SELECT qa.commit_upload('{u(loser)}','{u(300)}');",'qa_api',error='40001')
        sql(f"UPDATE design.document_files SET revision=3 WHERE id='{u(100)}';")
        check('基于明确选择成功提交未重传对象',f"SELECT qa.commit_upload('{u(loser)}','{u(300)}');",'qa_api','COMMITTED')
        check('最终head与提交绑定一致',f"SELECT f.revision=4 AND t.committed_binding_id='{u(300)}' FROM design.document_files f JOIN design.upload_transfers t ON t.file_id=f.id WHERE t.upload_id='{u(loser)}';",expected='t')
        check('binding不可原地改写',f"UPDATE design.upload_bindings SET expected_file_revision=999 WHERE id='{u(300)}';",error='55000')
        result=dict(passed=True,scope='真实PG并发、外键与可实现事务协议。qa函数是测试侧合成驱动，未执行业务API/对象存储/浏览器。',image=IMAGE,postgres_version=sql('SHOW server_version;'),checks=len(checks),check_names=checks,input_sha256={f:hashlib.sha256(b).hexdigest() for f,b in inputs.items()},vectors_sha256=hashlib.sha256(vectors).hexdigest(),helper_sha256=hashlib.sha256(helper.encode()).hexdigest(),probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),software_end_to_end_executed=False,statements=trace)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(passed=True,checks=len(checks))))
    finally:
        if started:cmd(['rm','-f','-v',name])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.snapshot,a.output)
