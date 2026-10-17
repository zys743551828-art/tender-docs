"""A2冻结候选的来源身份反例；独立PG/非owner，无付费或对象操作。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import UUID,uuid4,uuid5

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
sys.path.insert(0,str(APP))
sys.path.insert(0,str(HERE/'candidate03-observed-inputs'))
sys.path.insert(0,str(HERE.parent/'rag-architect-review'))
from isolated_pg import database

NAMESPACE=UUID('8d6cdd5c-1a28-45c6-ac26-e293b1d16f42')


def main():
    original_read=Path.read_text
    def no_private(path,*args,**kwargs):
        return '' if path.resolve()==APP/'.env' else original_read(path,*args,**kwargs)
    with patch.object(Path,'read_text',no_private):
        from packages.authoring.generation import freeze_generation_plan
        from packages.authoring.repository import create_draft,save_draft
        from packages.authoring.content import SCHEMA_DIGEST
        from packages.domain.runtime import connection,sha,DomainError,etag
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api_dsn=c.info.dsn
        with connect() as c:worker_dsn=c.info.dsn
        with patch.dict(os.environ,{'API_DATABASE_DSN':api_dsn,'WORKER_DATABASE_DSN':worker_dsn}):
            actor,bid,project,round_id,bidder,blob,version,material,parse,evidence=[uuid4() for _ in range(10)]
            root=uuid5(NAMESPACE,f'document-original:{version}')
            child=uuid5(NAMESPACE,f'document-parse:{parse}')
            with connection() as c:
                c.execute("INSERT INTO members VALUES(%s,'架构合成成员',true)",(actor,))
                c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'A2来源身份反例','{}',%s)",(bid,actor))
                c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成工程')",(project,))
                c.execute('INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)',(round_id,project))
                c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成主体')",(bidder,))
                c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,round_id,bidder))
                c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",(blob,sha(str(blob)),'explicit-fixture-only/'+str(blob)))
                c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'fixture.pdf',now()-interval '1 day','source_original',%s)",(version,bid,blob,actor))
                c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,sha({})))
                c.execute("INSERT INTO material_assignments VALUES(%s,%s,%s,'tender',0)",(material,bid,version))
                c.execute('UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s',(material,bid))
                c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(parse,bid,version,'a'*64,'b'*64))
                c.execute("INSERT INTO pages VALUES(%s,%s,1,NULL,595,842,0,'usable','[]')",(bid,parse))
                c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','合成要求',%s)",(evidence,bid,parse,sha('合成要求')))
                for node in (root,child):
                    c.execute("INSERT INTO retrieval.source_nodes VALUES(%s,%s,'document','prebid_content',now()-interval '1 day',%s)",(node,version,sha(str(blob))))
                    c.execute("INSERT INTO retrieval.source_heads VALUES(%s,2,'published',now()-interval '1 day',NULL)",(node,))
                c.execute('SELECT pg_advisory_xact_lock(90127,1)')
                c.execute('INSERT INTO retrieval.lineage_edges VALUES(%s,%s)',(child,root))
                draft,_=create_draft(c,bid,actor,'合成正文')
                section=c.execute('SELECT * FROM section_revisions WHERE draft_id=%s',(draft,)).fetchone()
            request={'section_id':str(section['section_id']),'base_section_revision':1,'instruction':'提供建议','source_version_ids':[str(version)]}
            with connection() as c:plan=freeze_generation_plan(c,bid,request)
            blocks=plan['plan']['source_blocks'];assert len(blocks)==1 and len({block['evidence_id'] for block in blocks})==1
            duplicate={'actual_evidence_blocks':1,'offered_source_blocks':len(blocks),'unique_offered_evidence_ids':1,'canonical_original_root_and_parse_child':True}
            with connection() as c:
                for ordinal in range(1,251):
                    c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,%s,'text','合成',%s)",(uuid4(),bid,parse,ordinal,sha('合成')))
            with connection() as c:
                plan251=freeze_generation_plan(c,bid,request)
                assert len(plan251['plan']['source_blocks'])==251
            with connection() as c:
                for ordinal in range(251,500):
                    c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,%s,'text','合成',%s)",(uuid4(),bid,parse,ordinal,sha('合成')))
            with connection() as c:
                plan500=freeze_generation_plan(c,bid,request)
                assert len(plan500['plan']['source_blocks'])==500
            with connection() as c:
                c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,500,'text','合成',%s)",(uuid4(),bid,parse,sha('合成')))
            with connection() as c:
                try:freeze_generation_plan(c,bid,request);raise AssertionError('501 true blocks must reject')
                except DomainError as error:
                    assert error.code=='AUTHORING_SOURCE_NOT_READY'
                    limit={'accepted_unique_counts':[251,500],'true_501_blocks_rejected':True,'error':error.code}
            new_parse,new_evidence=uuid4(),uuid4()
            with connection() as c:
                c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(new_parse,bid,version,'a'*64,'d'*64))
                c.execute("INSERT INTO pages VALUES(%s,%s,1,NULL,595,842,0,'usable','[]')",(bid,new_parse))
                c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','新解析证据',%s)",(new_evidence,bid,new_parse,sha('新解析证据')))
            content=copy.deepcopy(section['content']);content['attrs']['source_refs']=[{'source_id':str(child),'source_revision':2,'evidence_id':str(new_evidence),'document_version_id':str(version)}]
            try:
                with connection() as c:
                    save_draft(c,bid,draft,actor,{'base_draft_revision':1,'changes':[{'section_id':str(section['section_id']),'base_section_revision':1,'schema_digest':SCHEMA_DIGEST,'content':content}]},etag('draft',draft,1))
                raise AssertionError('Cross-parse reference accepted')
            except DomainError as error:
                assert error.code=='AUTHORING_SOURCE_STALE'
            with connection() as c:
                assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==1
                assert c.execute('SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s',(draft,)).fetchone()['n']==1
                assert c.execute('SELECT count(*) AS n FROM draft_snapshots WHERE draft_id=%s',(draft,)).fetchone()['n']==1
                assert c.execute('SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s',(bid,)).fetchone()['n']==0
                new_child=uuid5(NAMESPACE,f'document-parse:{new_parse}')
                c.execute('SELECT pg_advisory_xact_lock(90127,1)')
                c.execute("INSERT INTO retrieval.source_nodes VALUES(%s,%s,'document','prebid_content',now()-interval '1 day',%s)",(new_child,version,sha(str(blob))))
                c.execute("INSERT INTO retrieval.source_heads VALUES(%s,2,'published',now()-interval '1 day',NULL)",(new_child,))
                c.execute('INSERT INTO retrieval.lineage_edges VALUES(%s,%s)',(new_child,root))
            content['attrs']['source_refs'][0]['source_id']=str(new_child)
            with connection() as c:
                saved=save_draft(c,bid,draft,actor,{'base_draft_revision':1,'changes':[{'section_id':str(section['section_id']),'base_section_revision':1,'schema_digest':SCHEMA_DIGEST,'content':content}]},etag('draft',draft,1))
            assert saved['draft_revision']==2
            mismatched={'cross_parse_rejected':True,'rejected_attempt_left_revision_snapshot_citation_unchanged':True,'correct_new_parse_source_saved':True}
    result={'scope':'candidate03 actual domain functions with nonowner isolated PG; no provider/storage network','candidate_sha256':json.loads((HERE/'candidate03-inputs.json').read_text())['candidate_sha256'],'root_child_deduplication':duplicate,'unique_evidence_capacity':limit,'parse_source_binding':mismatched,'credentials_read':False,'paid_calls':0,'source_modified':False}
    (HERE/'candidate03-source-regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'duplicate_root_child_eliminated':True,'251_and_500_unique_accepted_501_rejected':True,'cross_parse_rejected_correct_parse_saved':True}))


if __name__=='__main__':main()
