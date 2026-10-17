"""Word schema permission and shape probe. Synthetic SQL records, no file/object/renderer I/O."""
import os,json,hashlib
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
HERE=Path(__file__).resolve().parent
S=HERE/'observed-schema02.sql';assert hashlib.sha256(S.read_bytes()).hexdigest()=='380a58eef00224a6094652c076d0d3ff0cb268a30a9868544671f4d37eb1dbfe'
def h(v):return hashlib.sha256(v.encode()).hexdigest()
def connect(role):
 c=psycopg.connect(os.environ[role+'_DATABASE_DSN']);c.execute('SET LOCAL search_path=design,public')
 who=c.execute('SELECT current_database(),current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone();assert who[0]=='tender_checks_word_intake01' and not any(who[2:]);return c
actor,bid,batch,upload,verify_cmd,verify_op,docx,pdf,blobx,blobp=[uuid4() for _ in range(10)]
with connect('API') as c:
 c.execute("INSERT INTO members(id,display_name) VALUES(%s,'架构师合成Word SQL机制')",(actor,))
 c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'仅SQL合成无真实Word转换','{}',%s)",(bid,actor))
 c.execute("INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,'open',%s)",(batch,bid,actor))
 for blob,doc,media,data in [(blobx,docx,'application/vnd.openxmlformats-officedocument.wordprocessingml.document','synthetic-docx-no-object'),(blobp,pdf,'application/pdf','synthetic-pdf-no-object')]:
  c.execute('INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s)',(blob,h(data),'architect-word-sql/'+str(blob),len(data),media))
  c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成SQL机制',clock_timestamp(),'source_original',%s)",(doc,bid,blob,actor))
 c.execute("INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at,verified_version_id) VALUES(%s,%s,%s,'fixture.docx','bid_body',%s,24,%s,'verified',clock_timestamp()+interval '1 hour',%s)",(upload,bid,batch,h('synthetic-docx-no-object'),'architect-word-stage/'+str(upload),docx))
 c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'architect-fixture',%s,%s,%s)",(verify_cmd,actor,upload,str(uuid4()),h('verify')))
 c.execute("INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'architect-word-verify',%s,'queued','architect/1')",(verify_op,bid,verify_cmd,h('verify')))
with connect('WORKER') as c:
 c.execute("INSERT INTO word_intake_receipts(document_version_id,bid_id,upload_id,verify_operation_id,inspection_digest,inspection_report) VALUES(%s,%s,%s,%s,%s,%s)",(docx,bid,upload,verify_op,h('synthetic-inspection'),Jsonb({'schema':'word-intake-inspection/1'})))
profile={'identity':{k:h(k) for k in ('renderer_digest','fontset_digest','options_digest')}};pd=h('synthetic-frozen-profile')
chain={'source_docx_digest':h('synthetic-docx-no-object'),'pdf_digest':h('synthetic-pdf-no-object'),'profile_digest':pd,**profile['identity']}
outcome={'state':'succeeded','coverage':'complete','gaps':[],'chain':chain,'conversion_attempted':True}
ops=[uuid4() for _ in range(7)]
with connect('API') as c:
 for op in ops:
  command=uuid4();c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'architect-fixture',%s,%s,%s)",(command,actor,docx,str(uuid4()),h(str(op))))
  c.execute("INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'architect-word-render',%s,'queued','architect/1')",(op,bid,command,h(str(op))))
 def denied(statement,values):
  try:
   with c.transaction():c.execute(statement,values)
  except psycopg.Error as e:assert e.sqlstate=='42501';return e.sqlstate
  raise AssertionError('API fabricated a completion')
 forged=denied('INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile,pdf_document_version_id,outcome,finished_at) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp())',(ops[0],bid,docx,pd,Jsonb(profile),pdf,Jsonb(outcome)))
 c.execute('INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile) VALUES(%s,%s,%s,%s,%s)',(ops[1],bid,docx,pd,Jsonb(profile)))
observed={}
with connect('WORKER') as c:
 def reject(label,op,result,pdf_id=pdf):
  try:
   with c.transaction():c.execute('INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile,pdf_document_version_id,outcome,finished_at) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp())',(op,bid,docx,pd,Jsonb(profile),pdf_id,Jsonb(result)))
  except psycopg.Error as e:assert e.sqlstate=='23514';observed[label]=e.sqlstate;return
  raise AssertionError('invalid Word outcome accepted')
 reject('JSON_null_state',ops[2],{'state':None,'coverage':'complete','gaps':[],'chain':{},'conversion_attempted':False},None)
 reject('success_not_attempted',ops[3],{**outcome,'conversion_attempted':False})
 reject('complete_with_gaps',ops[4],{**outcome,'gaps':['partial_content']})
 reject('wrong_output_SHA',ops[5],{**outcome,'chain':{**chain,'pdf_digest':'0'*64}})
 partial={**outcome,'coverage':'partial','gaps':['synthetic_partial']}
 c.execute('UPDATE word_render_runs SET pdf_document_version_id=%s,outcome=%s,finished_at=clock_timestamp() WHERE operation_id=%s',(pdf,Jsonb(partial),ops[1]))
 try:
  with c.transaction():c.execute('UPDATE word_render_runs SET outcome=%s WHERE operation_id=%s',(Jsonb(outcome),ops[1]))
 except psycopg.Error as e:assert e.sqlstate=='55000';observed['terminal_immutable']=e.sqlstate
 else:raise AssertionError('terminal receipt mutable')
with connect('API') as c:
 assert c.execute('SELECT outcome->>\'coverage\' FROM word_render_runs WHERE operation_id=%s',(ops[1],)).fetchone()[0]=='partial'
 assert c.execute('SELECT count(*) FROM word_render_runs WHERE bid_id=%s',(bid,)).fetchone()[0]==1
 try:
  with c.transaction():c.execute('UPDATE word_render_runs SET outcome=%s WHERE operation_id=%s',(Jsonb(outcome),ops[1]))
 except psycopg.Error as e:assert e.sqlstate=='42501';observed['API_result_update_denied']=e.sqlstate
 else:raise AssertionError('API result update granted')
result={'SQL_sha256':hashlib.sha256(S.read_bytes()).hexdigest(),'fixture':'isolated synthetic SQL project; no real uploaded bytes/Word conversion/effect publication','API_forged_completion_INSERT':forged,'worker_negative_results':observed,'API_pending_insert_and_worker_partial_finish':True,'result_rows':1,'actual_nonowner_PG':True,'objects':0,'renderer_calls':0,'paid_calls':0,'DDL_executed':False}
(HERE/'schema02-permissions-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
