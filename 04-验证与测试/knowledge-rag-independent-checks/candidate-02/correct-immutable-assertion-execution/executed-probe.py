"""Independent persisted-trace corruption fixtures and actual ASGI use-path checks."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import UUID,uuid4
from unittest.mock import patch
import hashlib,json,os,secrets
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from apps.api.knowledge_routes import router
from packages.domain import storage
from packages.domain.runtime import Command,DomainError,assert_role,clean,connection,now,secret_hash,sha
from packages.identity.repository import register_bidder,record_binding
from packages.knowledge import repository
from packages.knowledge.rag_index import compute_index
from packages.knowledge.rag_repository import schedule_index,prepare_index,publish_index
from packages.knowledge.rag_search import freeze_scope,read_candidates,save_trace,scope_identity,trace_result
from packages.knowledge.rag_types import text_digest
from packages.rag.indexing import ensure_parse_sources
from qa.a1_mock_probe import pdf

class OfflineDense:
 identity={'model_name':'EXPLICIT_INDEPENDENT_TRACE_GUARD_FIXTURE','model_revision':'no-quality-claim','weights_digest':'1'*64,'preprocessing_digest':'2'*64}
 lexical_dictionary_digest='3'*64
 def offsets(self,text):return [(i,i+1) for i in range(len(text))]
 def embed(self,texts):return [[1.]+[0.]*1023 for t in texts]
 def lexemes(self,text):return ' '.join(text)

def main():
 root=Path(os.environ['QA_KNOWLEDGE_ROOT']);out=root/os.environ.get('QA_KNOWLEDGE_EXECUTION','execution');out.mkdir(exist_ok=False);manifest=json.loads((root/'runtime-manifest.json').read_text())
 def verify():assert all(hashlib.sha256((root/'execution-inputs'/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in manifest)
 verify();assert_role();assert_role(True);assert Path(os.environ['WORKBENCH_ENV_FILE']).name=='combined_delivery01-checks.env'
 result=dict(passed=False,checks=[],scope='Own synthetic nonowner PG setup and actual standalone knowledge router ASGI; explicit malformed immutable trace INSERT fixture by API-role, not a public HTTP injection claim. Explicit synthetic vectors/index and memory original objects.',paid_calls=0,external_model_calls=0,object_network_requests=0,kafka_consumers_started=0)
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 actor,token,csrf=uuid4(),secrets.token_hex(32),secrets.token_hex(24);objects={};text='Independent approved historical method source. Duration requires current project confirmation. '+uuid4().hex;raw=pdf([text]);(out/'original.pdf').write_bytes(raw);dense=OfflineDense();stage='setup'
 def new_bid(c,company,title):
  bid,project,round_id=uuid4(),uuid4(),uuid4();c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{\"auto_review\":false}',%s)",(bid,title,actor));c.execute("INSERT INTO engineering_projects(id,title,region,engineering_type) VALUES(%s,%s,'浙江','市政道路')",(project,title));c.execute('INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)',(round_id,project));c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,round_id,company));c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),bid));c.execute('INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)',(bid,));record_binding(c,bid,actor,None,reason='独立合成明确主体，非真实资质',confirmed=True);return bid
 try:
  with patch.object(storage,'put',side_effect=lambda k,b,m:objects.__setitem__(k,b)),patch.object(storage,'get',side_effect=lambda k:objects[k]):
   with connection() as c:
    role=c.execute('SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone();assert not any(role[x] for x in ['rolsuper','rolbypassrls','rolcreatedb','rolcreaterole']);c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立trace完整性测试')",(actor,));company=UUID(register_bidder(c,actor,'独立合成主体'+uuid4().hex,'仅机制夹具',None)['id']);origin=new_bid(c,company,'独立历史原件');target=new_bid(c,company,'独立当前引用项目');blob,version,parse,evidence=[uuid4() for _ in range(4)];key='qa-memory-original/'+str(blob);objects[key]=raw;c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",(blob,sha(raw),key,len(raw)));c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'独立历史原件.pdf',now(),'source_original',%s)",(version,origin,blob,actor));c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(parse,origin,version,sha('explicit-independent-parse-fixture'),sha({})));c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",(origin,parse));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(evidence,origin,parse,text,sha(text)));_,source=ensure_parse_sources(c,origin,parse,publish_readable=True);rev=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(source,)).fetchone()['revision'];candidate=repository.create_candidate(c,actor,dict(title='独立历史工法参考',content='Historical method for reference; never a current-project fact.',source_version_ids=[str(version)],scope='仅浙江市政本主体，须明确参考'))
    c.execute('INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)',(uuid4(),actor,secret_hash(token),hashlib.sha256(csrf.encode()).hexdigest(),now()+timedelta(hours=1)))
   applicability=dict(bidder_ids=[str(company)],bid_ids=[],regions=['浙江'],engineering_types=['市政道路'],conditions=[],purposes=['assist','authoring','history'],validity_kind='bounded',valid_from=(now()-timedelta(days=1)).isoformat(),valid_until=(now()+timedelta(days=1)).isoformat(),validity_basis='独立合成边界，非真实业务',specificity_note='历史工期不自动成当前项目事实')
   reference=dict(source_id=str(source),source_revision=rev,document_version_id=str(version),parse_id=str(parse),physical_page=1,evidence_id=str(evidence),excerpt=text[:75])
   body=dict(title='独立历史工法参考',content='Historical method for reference; never a current-project fact.',categories=['method'],scope='浙江市政本主体参考',applicability=applicability,source_refs=[reference])
   item=UUID(candidate['id'])
   with connection() as c:repository.add_version(c,item,actor,body,repository.knowledge_etag(candidate))
   for action in ['submit','publish']:
    with connection() as c:current=repository.view(c,item);repository.transition(c,item,actor,dict(decision=action,reason='独立明确审批合成参考',evidence_ids=[]),repository.knowledge_etag(current),action)
   stage='explicit_index_and_positive_trace'
   with connection() as c:
    cmd=Command(c,actor,'qa-index-only',item,uuid4().hex,{});scheduled=schedule_index(c,item,actor,cmd.id,dense);cmd.finish(scheduled,202)
   with connection(True) as c:prepared=prepare_index(c,UUID(scheduled['index_id']))
   indexed=compute_index(prepared,dense,dense,dense,lexical_dictionary_digest=dense.lexical_dictionary_digest)
   with connection(True) as c:published=publish_index(c,indexed);assert published['state']=='ready'
   with connection() as c:
    profile=c.execute('SELECT profile_id FROM retrieval.knowledge_indexes WHERE id=%s',(UUID(scheduled['index_id']),)).fetchone()['profile_id'];scope=freeze_scope(c,target,'authoring',profile,dense.identity,dense.lexical_dictionary_digest,[str(item)])
   with connection() as c:selection=read_candidates(c,scope,[1.]+[0.]*1023,' '.join('historical method'));assert selection
   with connection() as c:good=save_trace(c,scope,actor,'Independent historical method',selection,['explicit_qa_fixture'],None,1);good_row=c.execute('SELECT * FROM retrieval.knowledge_retrieval_traces WHERE id=%s',(UUID(good['trace_id']),)).fetchone()
   app=FastAPI();app.include_router(router)
   @app.exception_handler(DomainError)
   async def err(request,e):return JSONResponse(dict(code=e.code,details=clean(e.details)),status_code=e.status)
   def headers(key=None):return {'Origin':os.environ['APP_ORIGIN'],'X-CSRF-Token':csrf,'Idempotency-Key':key or uuid4().hex}
   query=dict(query='Independent historical method',purpose='authoring',limit=1,knowledge_ids=[str(item)])
   def cached_retrieval(trace,response):
    key=uuid4().hex
    with connection() as c:cmd=Command(c,actor,'knowledge-retrieve',target,key,query);cmd.finish(response)
    return key
   def cite_body(unit):return dict(unit_id=unit['unit_id'],quote=unit['text'],target_kind='project',target_object_id=str(target),applicability_reason='独立核对后仅作历史参考，不形成事实')
   with TestClient(app) as client:
    client.cookies.set('session',token)
    # Every retrieval below is a saved replay; any model/port constructor would be an error.
    with patch('packages.knowledge.rag_api.DenseHttp',side_effect=AssertionError('No model runtime permitted')):
     good_hit=good['hits'][0];first_key=uuid4().hex;first=client.post(f"/api/v1/knowledge-retrievals/{good['trace_id']}/references",json=cite_body(good_hit),headers=headers(first_key));assert first.status_code==201,first.json();ref=first.json();citation=ref['citation_id']
     hist=client.get('/api/v1/knowledge-unit-references/'+citation);tracehist=client.get('/api/v1/knowledge-retrievals/'+good['trace_id']);replay=client.post(f"/api/v1/knowledge-retrievals/{good['trace_id']}/references",json=cite_body(good_hit),headers=headers(first_key));retrieval_key=cached_retrieval(good['trace_id'],good);retrieve=client.post(f'/api/v1/bids/{target}/knowledge-retrievals',json=query,headers=headers(retrieval_key))
     check('untampered_first_cite_history_and_two_replays_remain_usable',hist.status_code==200 and tracehist.status_code==200 and replay.status_code==201 and replay.json()==ref and retrieve.status_code==200 and retrieve.json()==good and retrieve.headers.get('Idempotent-Replayed')=='true')
     with connection() as c:actual_citation=c.execute('SELECT * FROM retrieval.citations WHERE id=%s',(UUID(citation),)).fetchone();actual_ref=c.execute('SELECT * FROM retrieval.knowledge_unit_references WHERE citation_id=%s',(UUID(citation),)).fetchone()
     bad_traces=[]
     for variant in os.environ.get('QA_TRACE_VARIANTS','forged_text_self_consistent_digest,forged_frozen_text_stale_digest,unexpected_selected_field,duplicate_selected_unit').split(','):
      stage=variant;bad_scope=deepcopy(good_row['frozen_scope']);selected=deepcopy(good_row['selected_units'])
      if variant=='forged_text_self_consistent_digest':selected[0]['text']='FORGED_UNAPPROVED_QA_TEXT_'+uuid4().hex;selected[0]['text_digest']=text_digest(selected[0]['text'])
      elif variant=='forged_frozen_text_stale_digest':
       selected[0]['text']='FORGED_RAW_UTF8_QA_'+uuid4().hex
       for unit in bad_scope['units']:
        if unit['unit_id']==selected[0]['unit_id']:unit['text']=selected[0]['text']
      elif variant=='unexpected_selected_field':selected[0]['unexpected_independent_field']=True
      else:selected.append(deepcopy(selected[0]))
      trace=uuid4();bad_traces.append(str(trace));fake_citation=uuid4();cite_payload=cite_body(selected[0]);fake_response=deepcopy(ref);fake_response['citation_id']=str(fake_citation);fake_response['trace_id']=str(trace)
      with connection() as c:
       c.execute('INSERT INTO retrieval.knowledge_retrieval_traces(id,bid_id,actor_id,purpose,query_digest,profile_id,policy_digest,frozen_scope,scope_digest,selected_units,route) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(trace,target,actor,good_row['purpose'],good_row['query_digest'],good_row['profile_id'],good_row['policy_digest'],Jsonb(bad_scope),sha(scope_identity(bad_scope)),Jsonb(selected),Jsonb(['explicit_malformed_persisted_fixture'])))
       c.execute('INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',(fake_citation,target,actual_citation['source_id'],actual_citation['source_revision'],actual_citation['purpose'],actual_citation['evidence_id'],target))
       c.execute('INSERT INTO retrieval.knowledge_unit_references(citation_id,trace_id,bid_id,unit_id,knowledge_version_id,approved_quote,unit_span_start,unit_span_end,reference_manifest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(fake_citation,trace,target,actual_ref['unit_id'],actual_ref['knowledge_version_id'],actual_ref['approved_quote'],actual_ref['unit_span_start'],actual_ref['unit_span_end'],Jsonb(fake_response)))
       ck=uuid4().hex;cmd=Command(c,actor,'knowledge-unit-reference',trace,ck,cite_payload);cmd.finish(fake_response,201)
       before=c.execute('SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s',(target,)).fetchone()['n']
      result_response=trace_result(trace,bad_scope,selected,['explicit_fixture']);rk=cached_retrieval(trace,result_response)
      responses=[('first_cite',client.post(f'/api/v1/knowledge-retrievals/{trace}/references',json=cite_payload,headers=headers())),('trace_history',client.get(f'/api/v1/knowledge-retrievals/{trace}')),('reference_history',client.get(f'/api/v1/knowledge-unit-references/{fake_citation}')),('citation_replay',client.post(f'/api/v1/knowledge-retrievals/{trace}/references',json=cite_payload,headers=headers(ck))),('retrieval_replay',client.post(f'/api/v1/bids/{target}/knowledge-retrievals',json=query,headers=headers(rk)))]
      for path,response in responses:check(variant+'_'+path+'_rejects_persisted_invalid_supply',response.status_code==409 and response.json().get('code')=='KNOWLEDGE_TRACE_INVALID',dict(status=response.status_code,code=response.json().get('code')))
      with connection() as c:after=c.execute('SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s',(target,)).fetchone()['n'];facts=c.execute('SELECT count(*) AS n FROM fact_items WHERE bid_id=%s',(target,)).fetchone()['n']
      check(variant+'_failed_use_appends_no_citation_or_fact',before==after and facts==0)
     try:
      with connection() as c:c.execute("UPDATE retrieval.knowledge_retrieval_traces SET selected_units='[]' WHERE id=%s",(UUID(bad_traces[0]),))
      immutable=False
     except Exception as e:immutable=getattr(e,'sqlstate',None) in ('55000','42501')
     check('malformed_trace_fixture_is_still_immutable_without_trigger_bypass',immutable)
     result.update(passed=all(x['passed'] for x in result['checks']),target_bid_id=str(target),origin_bid_id=str(origin),knowledge_id=str(item),good_trace_id=good['trace_id'],bad_trace_ids=bad_traces,api_login=role['current_user'])
 except Exception as error:result['fatal']=dict(stage=stage,type=type(error).__name__,message='Independent trace integrity probe incomplete; no credentials or private profile logged')
 verify();result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest())
 (out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
