"""Independent real TCP parser -> restricted fenced PG publication; no embedding or Kafka."""
import hashlib,importlib.util,json,os,shlex
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from apps.worker import engine
from packages.domain import storage
from packages.domain.runtime import DomainError,assert_role,connection,enqueue_plan,sha
from packages.domain.parsing_profile import configured_parser,profile
from packages.identity.repository import create_association
from packages.knowledge.repository import create_candidate,source_positions
from packages.rag.indexing import original_source_id,parse_source_id
from qa.a1_mock_probe import pdf


def main():
 root=Path(os.environ['QA_PARSE_ROOT']);out=root/'parse-readable-execution';out.mkdir(exist_ok=False);assert_role();assert_role(True);assert Path(os.environ['WORKBENCH_ENV_FILE']).name=='rag-checks.env'
 manifest=json.loads((root/'runtime-manifest.json').read_text());assert all(hashlib.sha256((root/'execution-inputs'/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in manifest)
 spec=importlib.util.spec_from_file_location('qa_parse_schedule_only',root/'execution-inputs/tests/test_parse_readable_integration.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
 result=dict(passed=False,checks=[],model_requests=0,object_network_requests=0,consumers_started=0,scope='Real TCP Parser03 computation plus actual restrictedPG/ordinary engine publication and knowledge source_positions function. Memory original bytes and explicit partial/failed outcomes. No embedding/vector/LLM request, no Kafka dispatch.')
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 actor,bid,version,blob,material,command=[uuid4() for _ in range(6)];raw=pdf(['Independent readable source before vector index '+uuid4().hex,'Physical page two independent source quote.']);(out/'original.pdf').write_bytes(raw);key='qa-memory-original/'+str(blob);objects={key:raw};stage='setup'
 env={}
 for line in Path('/workspace/tender-workbench/.local/parsing.env').read_text().splitlines():
  if '=' in line and not line.startswith('#'):
   k,v=line.split('=',1);env[k]=shlex.split(v)[0]
 try:
  with patch.dict(os.environ,env),patch.object(storage,'get',side_effect=lambda k:objects[k]):
   parser=configured_parser();assert parser and parser.parser_digest=='0578f501d377855240d5dae3e42cd295a43496cfc3bf28674e63a1e83ab49165'
   with connection() as c:
    c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立解析即时来源')",(actor,));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立可读发布原件','{}',%s)",(bid,actor));ids=create_association(c,{'title':'独立可读发布原件'});c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,ids['round_id'],ids['bidder_id']));c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),bid))
    c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",(blob,sha(raw),key,len(raw)));c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'独立双页原件.pdf',now(),'source_original',%s)",(version,bid,blob,actor));md=sha(str(material));c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,md));c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'other',0)",(material,bid,version));c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-readable-parse',%s,%s,%s)",(command,actor,bid,str(command),sha(str(command))))
    op=enqueue_plan(c,bid,command,'parse-materials',dict(id=str(material),digest=md,parse_profile=profile(parser)),stages=[dict(key='parse-materials',depends_on=[])],handler_version='document/2',topic='tender.task.document.v1');msg=c.execute('SELECT payload FROM outbox WHERE operation_id=%s LIMIT 1',(op['id'],)).fetchone()['payload']
   stage='real_parser_and_fence'
   lease=engine.acquire(msg);assert lease;outcome=engine.run_compute(msg,lease);assert len(outcome)==1
   fake=dict(lease,owner=uuid4());check('invalid_owner_cannot_publish_parse_or_readable_authority',engine.finish(msg,fake,outcome) is False)
   with connection() as c:check('rejected_publish_leaves_no_partial_parse',c.execute('SELECT count(*) AS n FROM parse_runs WHERE document_version_id=%s',(version,)).fetchone()['n']==0)
   assert engine.finish(msg,lease,outcome)
   with connection() as c:
    parsed=c.execute('SELECT * FROM parse_runs WHERE document_version_id=%s',(version,)).fetchone();heads=c.execute('SELECT source_id,revision,status FROM retrieval.source_heads WHERE source_id=ANY(%s::uuid[])',([original_source_id(version),parse_source_id(parsed['id'])],)).fetchall();chunks=c.execute('SELECT count(*) AS n FROM retrieval.chunks WHERE parse_id=%s',(parsed['id'],)).fetchone()['n'];entry=create_candidate(c,actor,dict(title='独立原页即时选取',content='合成方法',scope='本项目',source_version_ids=[str(version)]));positions=source_positions(c,entry['id'],version,1,-1,100)
   check('actual_parser_publish_provides_both_readable_heads_before_vector',parsed['coverage']=='complete' and len(heads)==2 and all(x['status']=='published' for x in heads) and chunks==0)
   check('knowledge_positions_usable_immediately_with_exact_page_and_quote',positions['readiness']=='readable_text_positions_only' and positions['items'] and positions['items'][0]['reference']['source_id']==str(parse_source_id(parsed['id'])) and positions['items'][0]['reference']['physical_page']==1 and 'Independent readable source before vector' in positions['items'][0]['text_content'],dict(readiness=positions['readiness'],items=positions['items']))
   stage='repeat_and_withdrawal'
   with connection() as c:repeat,repeated=helper.scheduled(c,actor,bid,version,parser='explicit-independent-repeat');repeated[0]['parser_digest']=parsed['parser_digest'];repeated[0]['config_digest']=parsed['config_digest']
   repeat_lease=engine.acquire(repeat);assert engine.finish(repeat,repeat_lease,repeated)
   with connection() as c:
    repeated_head=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(parse_source_id(parsed['id']),)).fetchone()['revision'];check('existing_parse_repeat_does_not_add_head_revision',repeated_head==next(x['revision'] for x in heads if x['source_id']==parse_source_id(parsed['id'])))
    c.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(bid,));c.execute('SELECT pg_advisory_xact_lock(90127,1)');c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(original_source_id(version),));late,late_result=helper.scheduled(c,actor,bid,version,parser='new-parser-after-withdrawal')
   late_lease=engine.acquire(late)
   try:engine.finish(late,late_lease,late_result);rejected=False
   except DomainError as error:
    rejected=error.code=='PARSE_SOURCE_AUTHORITY_CHANGED';assert rejected;engine.finish(late,late_lease,None,error)
   with connection() as c:
    count=c.execute('SELECT count(*) AS n FROM parse_runs WHERE document_version_id=%s',(version,)).fetchone()['n'];status=c.execute('SELECT status FROM retrieval.source_heads WHERE source_id=%s',(original_source_id(version),)).fetchone()['status']
   check('withdrawn_original_not_revived_and_new_parse_transaction_rolls_back',rejected and count==1 and status=='withdrawn')
   stage='partial_failed_empty'
   for label,coverage,text,published in [('partial','partial','Actually retained readable fragment',True),('failed','failed','',False),('empty','complete','',False)]:
    v,b=uuid4(),uuid4()
    with connection() as c:
     c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",(b,sha(str(b)),'qa-synthetic/'+str(b)));c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'显式缺口夹具.pdf',now(),'source_original',%s)",(v,bid,b,actor));m,o=helper.scheduled(c,actor,bid,v,parser='independent-'+label,coverage=coverage,text=text)
    l=engine.acquire(m);assert engine.finish(m,l,o)
    with connection() as c:
     row=c.execute('SELECT * FROM parse_runs WHERE document_version_id=%s',(v,)).fetchone();hd=c.execute('SELECT status FROM retrieval.source_heads WHERE source_id=%s',(original_source_id(v),)).fetchone()
    check(label+'_coverage_remains_truthful_and_only_text_is_published',row['coverage']==coverage and (hd['status']=='published' if hd else False)==published,dict(coverage=row['coverage'],gaps=row['gaps'],head=hd['status'] if hd else None))
   result.update(passed=all(x['passed'] for x in result['checks']),bid_id=str(bid),parser_digest=parser.parser_digest,parse_id=str(parsed['id']),original_sha256=sha(raw))
 except Exception as error:result['fatal']=dict(stage=stage,type=type(error).__name__,message='Independent readable publication probe incomplete; private configuration not logged')
 assert all(hashlib.sha256((root/'execution-inputs'/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in manifest)
 result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest(),helper_scope='Frozen scheduled() creates explicit task/outcome fixture only; no developer assertion/test function executed')
 (out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
