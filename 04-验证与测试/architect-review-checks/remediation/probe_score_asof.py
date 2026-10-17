"""Independent regression probe for business as_of versus audit frozen_at."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import time
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DESIGN=ROOT/'05-技术与设计/detailed-design'
FILES=['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'

def u(n):return str(uuid.UUID(int=n))

def run():
    inputs={f:(DESIGN/f).read_bytes() for f in FILES}
    name='tender-architect-asof-'+uuid.uuid4().hex[:10]
    env=dict(os.environ)
    for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
    def cmd(args,text=None):
        p=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],input=text,text=True,capture_output=True,timeout=90,env=env)
        if p.returncode:raise RuntimeError(p.stderr or p.stdout)
        return p.stdout.strip()
    def sql(text,role='postgres'):
        return cmd(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U',role,'-d','postgres'],text)
    h='a'*64
    cmd(['run','-d','--name',name,'--network','none','--memory','1g','--tmpfs','/var/lib/postgresql/data:rw','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE])
    try:
        for _ in range(100):
            try:cmd(['exec',name,'pg_isready','-U','postgres']);break
            except RuntimeError:time.sleep(.2)
        else:raise RuntimeError('PG not ready')
        for b in inputs.values():sql(b.decode())
        sql(f"""CREATE ROLE architect_score LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT tender_score_read TO architect_score;
INSERT INTO design.members VALUES('{u(1)}','合成成员',true);
INSERT INTO design.bids(id,title,settings,created_by) VALUES('{u(10)}','合成评审','{{}}','{u(1)}');
INSERT INTO design.blobs VALUES('{u(20)}','{h}','fixture',1,'application/pdf',now());
INSERT INTO design.document_versions VALUES('{u(30)}','{u(10)}','{u(20)}','body.pdf',now(),'bid_body','{u(1)}',now());
INSERT INTO design.material_snapshots VALUES('{u(40)}','{u(10)}',1,'{{}}','{h}',now());
INSERT INTO design.commands VALUES('{u(50)}','{u(1)}','review','{u(10)}','asof-fixture','{h}',null,now());
INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{u(60)}','{u(10)}','{u(50)}','review','{h}','running','fixture');
INSERT INTO design.review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest)
VALUES('{u(70)}','{u(10)}','{u(40)}','{u(30)}','{h}','{h}','{{}}','2026-02-15','{u(20)}','{h}','2026-01-15','{h}');
INSERT INTO design.review_runs VALUES('{u(80)}','{u(10)}','{u(70)}','{u(60)}',now());
INSERT INTO design.parse_runs VALUES('{u(200)}','{u(10)}','{u(30)}','{h}','{h}',1,'complete','[]',now());
INSERT INTO design.pages VALUES('{u(10)}','{u(200)}',1,null,595,842,0,'usable','[]');
INSERT INTO design.evidence_blocks VALUES('{u(201)}','{u(10)}','{u(200)}',1,0,'text','合成内容',null,null,'{h}');
""")
        for source,known,until in [(301,'2026-01-01',"'2026-01-31'"),(302,'2026-02-01','null'),(303,'2026-01-01','null')]:
            sql(f"""INSERT INTO retrieval.source_nodes VALUES('{u(source)}','{u(30)}','document','prebid_content','{known}','{h}');
INSERT INTO retrieval.source_heads VALUES('{u(source)}',1,'published','2026-01-01',{until});
INSERT INTO retrieval.chunks(id,source_id,parse_id,physical_page,ordinal,chapter_path,evidence_ids,text_content,lexemes,token_count,digest)
VALUES('{u(source+100)}','{u(source)}','{u(200)}',1,{source-301},ARRAY['施工'],ARRAY['{u(201)}']::uuid[],'合成内容','合成 内容',4,'{h}');
INSERT INTO retrieval.score_input_sources VALUES('{u(80)}','{u(source)}');""")
        allowed="ARRAY["+','.join("'"+u(n)+"'" for n in (301,302,303))+"]::uuid[]"
        expected=sql(f"SELECT source_id FROM retrieval.eligible_chunks({allowed},'2026-01-15',ARRAY[]::uuid[],true) ORDER BY source_id;").splitlines()
        actual=sql(f"SELECT source_id FROM retrieval.formal_score_context('{u(80)}') ORDER BY source_id;",'architect_score').splitlines()
        assert expected==[u(301),u(303)],expected
        result=dict(scope='正式评分读取的业务时点回归；合成PG机制，非业务API',
                    inputs={k:hashlib.sha256(v).hexdigest() for k,v in inputs.items()},
                    postgres_version=sql('SHOW server_version;'),image=IMAGE,
                    as_of='2026-01-15T00:00:00Z',frozen_at='2026-02-15T00:00:00Z',
                    expected_sources=expected,actual_sources=actual,
                    matches_business_as_of=expected==actual,
                    future_source_included=u(302) in actual,
                    previously_effective_source_omitted=u(301) not in actual,
                    executed_with_real_login=True,product_end_to_end_executed=False)
        (HERE/'score-asof-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('inputs','image')},ensure_ascii=False))
    finally:cmd(['rm','-f','-v',name])

if __name__=='__main__':run()
