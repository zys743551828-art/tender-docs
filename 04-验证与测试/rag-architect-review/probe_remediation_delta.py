"""对早期四项发现的独立delta复验；真实PG、显式机制向量，不运行模型。"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
from uuid import uuid4

import psycopg

from isolated_pg import database

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'delta-observed-inputs'))
from packages.rag.core import SuppliedEvidence
from packages.rag.indexing import prepare_index,compute_index,publish_index,withdraw_source
from packages.rag.repository import RetrievalScope,source_manifest,assert_current_sources,resolve_project_scope


class Tokenizer:
    def offsets(self,text):return [(i,i+1) for i in range(len(text))]
class Lexical:
    def lexemes(self,text):return ' '.join(text)
class Embedder:
    identity={'model_name':'explicit-mechanism-fixture','model_revision':'fixture',
              'weights_digest':'a'*64,'preprocessing_digest':'b'*64}
    hook=None
    def embed(self,texts):
        if self.hook:self.hook()
        return [[1.0]+[0.0]*1023 for _ in texts]


def main():
    checks=[]
    with database() as connect:
        def seed(document=None,bid=None,actor=None):
            with connect('api') as c:
                if document is None:
                    actor,bid,blob,document=[uuid4() for _ in range(4)]
                    c.execute('INSERT INTO design.members VALUES(%s,%s,true)',(actor,'架构合成成员'))
                    c.execute('INSERT INTO design.bids(id,title,settings,created_by) VALUES(%s,%s,%s,%s)',(bid,'RAG修复机制反例','{}',actor))
                    c.execute('INSERT INTO design.blobs VALUES(%s,%s,%s,1,%s,now())',(blob,hashlib.sha256(str(blob).encode()).hexdigest(),'synthetic-only/'+str(blob),'application/pdf'))
                    c.execute("INSERT INTO design.document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'source.pdf',now()-interval '1 day','source_original',%s)",(document,bid,blob,actor))
                parse,evidence=uuid4(),uuid4();text='原件来源撤回后不能通过重新解析恢复。'
                c.execute("INSERT INTO design.parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial','[]')",(parse,bid,document,'d'*64,'e'*64))
                c.execute("INSERT INTO design.pages VALUES(%s,%s,1,NULL,100,100,0,'usable','[]')",(bid,parse))
                c.execute("INSERT INTO design.evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(evidence,bid,parse,text,hashlib.sha256(text.encode()).hexdigest()))
            return actor,bid,document,parse
        def compute(seed_value,embedder=None):
            _,bid,_,parse=seed_value
            with connect() as c:prepared=prepare_index(c,bid,parse,Tokenizer(),Lexical())
            return compute_index(prepared,embedder or Embedder())
        def publish(computed):
            with connect() as c:return publish_index(c,computed,Lexical())
        def revision(source):
            with connect() as c:return c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(source,)).fetchone()['revision']
        def retrieved(index,bid,groups=()):
            scope=RetrievalScope(bid,'assist',(index['source_id'],),datetime.now(timezone.utc),groups)
            with connect() as c:
                row=c.execute('SELECT * FROM retrieval.chunks WHERE source_id=%s LIMIT 1',(index['source_id'],)).fetchone()
                manifest=source_manifest(c,scope)
            return {'scope':scope,'source_manifest':manifest,'evidence':[SuppliedEvidence(str(uuid4()),index['source_id'],2,row['parse_id'],row['physical_page'],row['id'],row['text_content'])]}
        def expect_value_error(action):
            try:action()
            except ValueError:return
            raise AssertionError('Expected ValueError')

        first=seed();computed=compute(first);index=publish(computed)
        with connect() as c:
            assert c.execute('SELECT count(*) AS n FROM retrieval.lineage_edges WHERE child_id=%s AND parent_id=%s',(index['source_id'],index['original_source_id'])).fetchone()['n']==1
        with connect() as c:withdraw_source(c,first[1],index['source_id'],revision(index['original_source_id']))
        new_parse=seed(first[2],first[1],first[0]);later=compute(new_parse)
        expect_value_error(lambda:publish(later))
        with connect() as c:
            assert c.execute('SELECT count(*) AS n FROM retrieval.eligible_chunks(%s,now(),%s,false)',([index['source_id']],[])).fetchone()['n']==0
        checks.append({'id':'G01','passed':True,'source_child_normalizes_withdrawal_to_stable_root':True,'new_parse_cannot_republish':True})

        second=seed();second_index=publish(compute(second));group=uuid4()
        with connect() as c:c.execute("INSERT INTO retrieval.source_groups VALUES(%s,'explicit excluded fixture')",(group,))
        result=retrieved(second_index,second[1],(group,))
        with connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(90127,1)')
            c.execute('INSERT INTO retrieval.source_group_memberships VALUES(%s,%s)',(second_index['original_source_id'],group))
        with connect() as c:expect_value_error(lambda:assert_current_sources(c,result))
        clean=retrieved(second_index,second[1])
        with connect('api') as application:
            assert_current_sources(application,clean)
            try:
                with connect() as revoker:
                    revoker.execute("SET LOCAL lock_timeout='200ms'")
                    withdraw_source(revoker,second[1],second_index['source_id'],revision(second_index['original_source_id']))
                raise AssertionError('Withdrawal bypassed held application lock')
            except psycopg.errors.LockNotAvailable:pass
            application.execute("INSERT INTO retrieval.retrieval_traces(id,bid_id,purpose,query_digest,allowed_manifest_digest,profile_id,policy_digest,selected_chunk_ids,excluded_reasons) VALUES(%s,%s,'assist',%s,%s,%s,%s,'{}','{}')",(uuid4(),second[1],'f'*64,'f'*64,second_index['profile_id'],'f'*64))
        with connect() as revoker:withdraw_source(revoker,second[1],second_index['source_id'],revision(second_index['original_source_id']))
        with connect() as c:expect_value_error(lambda:assert_current_sources(c,clean))
        with connect(autocommit=True) as c:expect_value_error(lambda:assert_current_sources(c,clean))
        with connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            expect_value_error(lambda:assert_current_sources(c,clean))
        checks.append({'id':'G02','passed':True,'new_excluded_ancestor_group_rejected':True,'guard_holds_until_same_transaction_effect_commit':True,'withdrawal_before_guard_rejected':True,'autocommit_and_repeatable_read_rejected':True})

        third=seed();third_index=publish(compute(third));new_third=seed(third[2],third[1],third[0])
        with connect() as c:prepared=prepare_index(c,third[1],new_third[3],Tokenizer(),Lexical())
        entered=threading.Event();release=threading.Event();values=[];errors=[];slow=Embedder()
        def hook():
            entered.set()
            if not release.wait(10):raise RuntimeError('Synthetic compute release timed out')
        slow.hook=hook
        def work():
            try:values.append(compute_index(prepared,slow))
            except BaseException as error:errors.append(type(error).__name__)
        task=threading.Thread(target=work);task.start();assert entered.wait(10)
        try:
            with connect() as c:
                c.execute("SET LOCAL lock_timeout='200ms'")
                withdraw_source(c,third[1],third_index['source_id'],revision(third_index['original_source_id']))
        finally:
            release.set();task.join(10)
        assert not errors and not task.is_alive() and len(values)==1
        expect_value_error(lambda:publish(values[0]))
        checks.append({'id':'G04','passed':True,'withdrawal_commits_while_compute_paused':True,'computed_index_rejected_after_root_withdrawal':True})
    result={'scope':'unfrozen remediation delta; actual PG and nonowner roles with explicit fixture vectors','input_sources':json.loads((HERE/'delta-inputs.json').read_text())['sources'],'checks':checks,'paid_calls':0,'credentials_read':False,'real_model_inference':False,'final_architecture_pass':False}
    (HERE/'delta-pg-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'groups':len(checks),'final_pass':False}))


if __name__=='__main__':main()
