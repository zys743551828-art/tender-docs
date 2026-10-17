"""独立PG、非owner、明确合成向量；仅证明早期草稿机制，不证明RAG质量。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
APP = Path('/workspace/tender-workbench')
SNAPSHOT = HERE / 'draft-observed-inputs'
IMAGE = 'pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'


def main():
    env = dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        env.pop(key, None)
    name = 'tender-rag-architect-' + uuid4().hex[:10]
    def docker(args, text=None):
        result = subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],
                                input=text,text=True,capture_output=True,timeout=90,env=env)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout.strip()
    def owner_sql(statement):
        return docker(['exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1',
                       '-h','127.0.0.1','-U','postgres'], statement)
    docker(['run','-d','--name',name,'--memory','1g','--tmpfs','/var/lib/postgresql/data:rw',
            '-p','127.0.0.1::5432','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE])
    try:
        for _ in range(100):
            try:
                docker(['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres'])
                break
            except RuntimeError:
                time.sleep(.2)
        else:
            raise RuntimeError('PG did not start')
        for filename in ('core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql'):
            owner_sql((APP/'packages/contracts/sql'/filename).read_text())
        owner_sql('CREATE ROLE rag_fixture_api LOGIN INHERIT NOSUPERUSER; GRANT tender_api TO rag_fixture_api; CREATE ROLE rag_fixture_worker LOGIN INHERIT NOSUPERUSER; GRANT tender_worker TO rag_fixture_worker;')
        port = docker(['port',name,'5432/tcp']).rsplit(':',1)[1]
        def connect(role='worker'):
            return psycopg.connect(f'postgresql://rag_fixture_{role}@127.0.0.1:{port}/postgres',row_factory=dict_row)
        sys.path.insert(0,str(APP))
        sys.path.insert(0,str(SNAPSHOT))
        from packages.rag.indexing import index_parse, withdraw_source
        from packages.rag.repository import RetrievalScope, source_manifest, assert_current_sources
        from packages.rag import indexing, repository
        assert Path(indexing.__file__).is_relative_to(SNAPSHOT)
        assert Path(repository.__file__).is_relative_to(SNAPSHOT)
        class FixtureTokenizer:
            def offsets(self, text):
                return [(i,i+1) for i in range(len(text))]
        class FixtureLexical:
            def lexemes(self, text):
                return ' '.join(text)
        class FixtureEmbedder:
            identity = {'model_name':'explicit-mechanism-fixture','model_revision':'fixture',
                        'weights_digest':'a'*64,'preprocessing_digest':'b'*64}
            hook = None
            def embed(self, texts):
                if self.hook:
                    self.hook()
                return [[1.0] + [0.0]*1023 for _ in texts]
        actor,bid,blob,document = [uuid4() for _ in range(4)]
        with connect('api') as connection:
            connection.execute('INSERT INTO design.members VALUES(%s,%s,true)',(actor,'架构合成成员'))
            connection.execute('INSERT INTO design.bids(id,title,settings,created_by) VALUES(%s,%s,%s,%s)',(bid,'架构RAG合成来源','{}',actor))
            connection.execute('INSERT INTO design.blobs VALUES(%s,%s,%s,1,%s,now())',(blob,'c'*64,'synthetic-only/source.pdf','application/pdf'))
            connection.execute("INSERT INTO design.document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'source.pdf',now(),'source_original',%s)",(document,bid,blob,actor))
        def prepare_parse():
            parse,evidence = uuid4(),uuid4()
            with connect('api') as connection:
                connection.execute("INSERT INTO design.parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial','[]')",(parse,bid,document,'d'*64,'e'*64))
                connection.execute("INSERT INTO design.pages VALUES(%s,%s,1,NULL,100,100,0,'usable','[]')",(bid,parse))
                text='原件来源不得在撤回后通过重新解析恢复。'
                connection.execute("INSERT INTO design.evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(evidence,bid,parse,text,hashlib.sha256(text.encode()).hexdigest()))
            return parse
        embedder=FixtureEmbedder(); tokenizer=FixtureTokenizer(); lexical=FixtureLexical()
        first_parse=prepare_parse()
        with connect() as connection:
            first=index_parse(connection,bid,first_parse,embedder,tokenizer,lexical)
        with connect() as connection:
            withdraw_source(connection,bid,first['source_id'],2)
        second_parse=prepare_parse()
        with connect() as connection:
            second=index_parse(connection,bid,second_parse,embedder,tokenizer,lexical)
        with connect() as connection:
            states=connection.execute('SELECT source_id,status FROM retrieval.source_heads ORDER BY source_id').fetchall()
            first_count=connection.execute('SELECT count(*) AS n FROM retrieval.eligible_chunks(%s,now(),%s,false)',([first['source_id']],[])).fetchone()['n']
            second_count=connection.execute('SELECT count(*) AS n FROM retrieval.eligible_chunks(%s,now(),%s,false)',([second['source_id']],[])).fetchone()['n']
        assert first['source_id'] != second['source_id'] and first_count == 0 and second_count > 0
        reparse={'same_document_version':str(document),'distinct_sources':True,'withdrawn_source_eligible_chunks':first_count,'new_parse_eligible_chunks':second_count,'heads':states}
        scope=RetrievalScope(bid,'assist',(second['source_id'],),datetime.now(timezone.utc))
        with connect() as application:
            result={'scope':scope,'source_manifest':source_manifest(application,scope)}
            assert_current_sources(application,result)
            with connect() as revoker:
                revoker.execute("SET LOCAL lock_timeout='200ms'")
                withdraw_source(revoker,bid,second['source_id'],2)
            trace=uuid4()
            application.execute("INSERT INTO retrieval.retrieval_traces(id,bid_id,purpose,query_digest,allowed_manifest_digest,profile_id,policy_digest,selected_chunk_ids,excluded_reasons) VALUES(%s,%s,'assist',%s,%s,%s,%s,'{}','{}')",(trace,bid,'f'*64,'f'*64,second['profile_id'],'f'*64))
        guard={'revocation_committed_while_application_transaction_open':True,'trace_write_after_revocation_committed':str(trace),'scope':'guard serialization counterexample; no application route exists yet'}
        third_parse=prepare_parse(); entered=threading.Event(); release=threading.Event(); background=[]
        slow=FixtureEmbedder()
        def hook():
            entered.set()
            if not release.wait(10):
                raise RuntimeError('Synthetic embed release timed out')
        slow.hook=hook
        def index_background():
            try:
                with connect() as connection:
                    index_parse(connection,bid,third_parse,slow,tokenizer,lexical)
            except BaseException as error:
                background.append(type(error).__name__)
        thread=threading.Thread(target=index_background);thread.start()
        assert entered.wait(10)
        try:
            with connect() as revoker:
                revoker.execute("SET LOCAL lock_timeout='200ms'")
                withdraw_source(revoker,bid,first['source_id'],3)
            blocked=False
        except psycopg.errors.LockNotAvailable:
            blocked=True
        finally:
            release.set();thread.join(10)
        assert blocked and not thread.is_alive() and not background
        output={'scope':'unfrozen observed draft; isolated PG and explicit fixture models only',
                'input_sources':json.loads((HERE/'draft-inputs.json').read_text())['sources'],
                'credentials_read':False,'paid_calls':0,'shared_environments_used':False,
                'reparse_after_withdrawal':reparse,'apply_guard_race':guard,
                'embedding_holds_global_revocation_lock':blocked,'background_errors':background}
        (HERE/'draft-boundary-counterexamples.json').write_text(json.dumps(output,ensure_ascii=False,default=str,indent=2)+'\n')
        print(json.dumps({'reparse_bypasses_old_source_withdrawal':True,'apply_guard_not_serialized':True,'embedding_blocks_withdrawal':blocked,'all_business_connections_nonowner':True}))
    finally:
        docker(['rm','-f','-v',name])


if __name__ == '__main__':
    main()
