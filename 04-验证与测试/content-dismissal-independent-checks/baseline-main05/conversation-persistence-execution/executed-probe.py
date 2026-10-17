"""Independent work-conversation persistence ASGI checks; no AI generation."""
import hashlib,json,os,secrets
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from apps.api.assistant_routes import router
from packages.domain.runtime import connection,now,secret_hash,DomainError,enqueue,sha

def main():
 root=Path(os.environ['QA_CONTENT_ROOT']);out=root/'conversation-persistence-execution';out.mkdir(exist_ok=False);entries=json.loads((root/'runtime-manifest.json').read_text());assert all(hashlib.sha256((root/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in entries);result=dict(passed=False,checks=[],scope='Actual work-conversation01 ASGI and restrictedPG. Persistence only: no generated response, action/tool execution or formal scoring.',paid_calls=0,model_calls=0,consumers_started=0)
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 app=FastAPI();app.include_router(router)
 @app.exception_handler(DomainError)
 async def err(request,e):return JSONResponse(dict(code=e.code),status_code=e.status)
 actors=[uuid4(),uuid4()];tokens=[secrets.token_hex(32) for _ in actors];csrf=[secrets.token_hex(24) for _ in actors];bid,other=[uuid4(),uuid4()]
 with connection() as c:
  for i,actor in enumerate(actors):c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立持久主会话')",(actor,));c.execute('INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)',(uuid4(),actor,secret_hash(tokens[i]),hashlib.sha256(csrf[i].encode()).hexdigest(),now()+timedelta(hours=1)))
  for p in [bid,other]:c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立主会话项目','{\"auto_review\":false}',%s)",(p,actors[0]));c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),p))
 def h(i=0,tag=None,key=None):return {'Origin':os.environ['APP_ORIGIN'],'X-CSRF-Token':csrf[i],'Idempotency-Key':key or uuid4().hex,**({'If-Match':tag} if tag else {})}
 stage='concurrent_main'
 try:
  with TestClient(app) as a,TestClient(app) as b:
   a.cookies.set('session',tokens[0]);b.cookies.set('session',tokens[1]);clients=[a,b]
   with ThreadPoolExecutor(max_workers=2) as pool:opened=list(pool.map(lambda i:clients[i].post(f'/api/v1/bids/{bid}/conversations/main',json={},headers=h(i)),[0,1]))
   assert all(x.status_code==200 for x in opened);conversation=opened[0].json()['id'];base=f'/api/v1/bids/{bid}/conversations/{conversation}'
   with connection() as c:creations=c.execute("SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='conversation.created'",(bid,)).fetchone()['n']
   check('two_members_share_one_actual_main_conversation_and_creation',opened[0].json()['id']==opened[1].json()['id'] and creations==1)
   stage='message_identity'
   body=dict(client_message_id=str(uuid4()),text='独立人类文本，不允许视为已执行AI命令',source_version_ids=[]);current=a.get(base);tag=current.headers['etag'];key=uuid4().hex;accepted=a.post(base+'/messages',json=body,headers=h(0,tag,key));assert accepted.status_code==201;message=accepted.json();replay=a.post(base+'/messages',json=body,headers=h(0,tag,key));fresh=a.get(base)
   check('lost_ack_replays_message_without_incrementing_revision',replay.status_code==201 and replay.json()==message and replay.headers.get('Idempotent-Replayed')=='true' and fresh.json()['revision']==current.json()['revision']+1)
   conflict=a.post(base+'/messages',json=body|dict(text='同消息标识另一个内容'),headers=h(0,fresh.headers['etag']));cross_actor=b.post(base+'/messages',json=body,headers=h(1,fresh.headers['etag']));stale=a.post(base+'/messages',json=body|dict(client_message_id=str(uuid4())),headers=h(0,tag))
   check('message_identity_cannot_change_content_or_actor_and_stale_write_rejected',conflict.status_code==409 and conflict.json()['code']=='MESSAGE_ID_CONFLICT' and cross_actor.status_code==409 and stale.status_code==412)
   check('persisted_user_message_never_claims_AI_completion_or_score_input',message['role']=='user' and message['assistant_operation_id'] is None and message['formal_scoring_input'] is False)
   spoof=a.post(base+'/messages',json=body|dict(client_message_id=str(uuid4()),role='assistant'),headers=h(0,fresh.headers['etag']));check('client_cannot_impersonate_generated_assistant_role',spoof.status_code==422)
   cross=a.get(f'/api/v1/bids/{other}/conversations/{conversation}');check('conversation_path_cannot_cross_project',cross.status_code==404)
   stage='archive_restore'
   closed=a.post(base+'/state',json=dict(state='closed'),headers=h(0,a.get(base).headers['etag']));hidden=a.post(base+'/state',json=dict(state='hidden'),headers=h(0,closed.headers['etag']));assert hidden.status_code==200
   opened_again=a.post(f'/api/v1/bids/{bid}/conversations/main',json={},headers=h());blocked=a.post(base+'/messages',json=body|dict(client_message_id=str(uuid4())),headers=h(0,hidden.headers['etag']));invalid=a.post(base+'/state',json=dict(state='active'),headers=h(0,hidden.headers['etag']))
   check('opening_main_does_not_unhide_or_resume_closed_conversation',opened_again.json()['state']=='hidden' and blocked.status_code==409 and blocked.json()['code']=='CONVERSATION_CLOSED' and invalid.status_code==409)
   restored=a.post(base+'/state',json=dict(state='closed'),headers=h(0,a.get(base).headers['etag']));resumed=a.post(base+'/state',json=dict(state='active'),headers=h(0,restored.headers['etag']));assert resumed.status_code==200;history=b.get(base+'/messages').json()
   with connection() as c:
    sessions=c.execute('SELECT count(*) AS n FROM sessions WHERE member_id=ANY(%s::uuid[]) AND revoked_at IS NULL',(actors,)).fetchone()['n'];models=c.execute('SELECT count(*) AS n FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s',(bid,)).fetchone()['n'];facts=c.execute('SELECT count(*) AS n FROM fact_items WHERE bid_id=%s',(bid,)).fetchone()['n']
   check('restore_keeps_original_history_and_sessions_without_model_or_fact_side_effect',history['items']==[message] and sessions==2 and models==0 and facts==0)
   # Add two more actual messages and compare server pages without a local transcript.
   for i in range(2):
    r=a.post(base+'/messages',json=dict(client_message_id=str(uuid4()),text='独立后续文本'+str(i),source_version_ids=[]),headers=h(0,a.get(base).headers['etag']));assert r.status_code==201
   page1=a.get(base+'/messages',params={'limit':2}).json();page2=b.get(base+'/messages',params={'limit':2,'before':page1['before']}).json();ids=[x['id'] for x in page1['items']+page2['items']]
   check('another_member_discovers_persisted_paged_history_without_duplicate',len(ids)==len(set(ids))==3 and page1['has_more'] and not page2['has_more'])
   result.update(passed=all(x['passed'] for x in result['checks']),bid_id=str(bid),conversation_id=conversation)
 except Exception as e:result['fatal']=dict(stage=stage,type=type(e).__name__,code=getattr(e,'code',None),message='Independent conversation persistence probe incomplete')
 result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest());(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
