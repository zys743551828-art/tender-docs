"""Independent actual HTTP projection/new-decision tests over immutable05 bad carry."""
from pathlib import Path
from uuid import uuid4,UUID
from datetime import timedelta
import hashlib,json,os,secrets
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from apps.api.review_routes import router as reads
from apps.api.human_routes import router as writes
from packages.domain.runtime import connection,DomainError,clean,now,secret_hash,sha
from packages.identity.repository import create_association

def main():
 root=Path(os.environ['QA_CONTENT_ROOT']);out=root/'legacy-read-and-decision-execution';out.mkdir(exist_ok=False);oldpath=Path('/workspace/tender-docs/04-验证与测试/content-dismissal-independent-checks/baseline-main05/fixed-page-digest-execution/result.json');old=json.loads(oldpath.read_text());entries=json.loads((root/'runtime-manifest.json').read_text());assert all(hashlib.sha256((root/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in entries);bid=UUID(old['bid_id']);oldroot=old['first']['finding']['id'];newid=old['changed_body']['finding']['id'];sameid=old['same_body']['finding']['id'];token,csrf=secrets.token_hex(32),secrets.token_hex(24)
 with connection() as c:
  actor=c.execute('SELECT created_by FROM bids WHERE id=%s',(bid,)).fetchone()['created_by'];raw_old=c.execute('SELECT * FROM findings WHERE id=%s',(newid,)).fetchone();decision_old=c.execute('SELECT * FROM decisions WHERE id=%s',(old['old_human_decision_id'],)).fetchone();assert raw_old['carried_from_id'] is not None
  if not c.execute('SELECT 1 FROM bid_profiles WHERE bid_id=%s',(bid,)).fetchone():
   ids=create_association(c,dict(title='独立05历史carry读取夹具'));c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,ids['round_id'],ids['bidder_id']))
  c.execute('INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)',(uuid4(),actor,secret_hash(token),hashlib.sha256(csrf.encode()).hexdigest(),now()+timedelta(hours=1)))
 app=FastAPI();app.include_router(reads);app.include_router(writes)
 @app.exception_handler(DomainError)
 async def err(request,e):return JSONResponse(dict(code=e.code),status_code=e.status)
 def h(tag,key=None):return {'Origin':os.environ['APP_ORIGIN'],'X-CSRF-Token':csrf,'Idempotency-Key':key or uuid4().hex,'If-Match':tag}
 result=dict(passed=False,checks=[],scope='Actual nonowner PG and actual GETfinding/POSTfindingdecision ASGI against the exact bad carried row created by frozen05. Own fixture profile added with no current review; metrics remains unknown, not a complete gate test.',paid_calls=0,model_calls=0,consumers_started=0)
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 stage='read_existing_bad_carry'
 try:
  with TestClient(app) as client:
   client.cookies.set('session',token);before=client.get('/api/v1/findings/'+newid);same=client.get('/api/v1/findings/'+sameid);assert before.status_code==200,before.json();assert same.status_code==200
   check('existing05_wrong_carry_projects_open_with_no_inapplicable_old_decision',before.json()['lifecycle']=='open' and before.json()['decision'] is None,before.json())
   check('identical_old_body_carry_still_may_project_the_old_dismiss',same.json()['lifecycle']=='dismissed' and same.json()['decision']['id']==old['old_human_decision_id'])
   stage='late_ancestor_dismiss'
   ancestor=client.get('/api/v1/findings/'+oldroot);late=client.post('/api/v1/findings/'+oldroot+'/decisions',json=dict(decision='dismiss',reason='迟到决定仍然只覆盖旧90/120证据',evidence_ids=old['first']['finding']['evidence_ids']),headers=h(ancestor.headers['etag']));assert late.status_code==200,late.json()
   current=client.get('/api/v1/findings/'+newid);check('late_ancestor_dismiss_cannot_silence_new95_125_conflict',current.json()['lifecycle']=='open' and current.json()['decision'] is None)
   stage='new_decision_actual_subject'
   key=uuid4().hex;body=dict(decision='defer',reason='只处理新95/125两侧证据，旧决定保留',evidence_ids=old['changed_body']['finding']['evidence_ids']);tag=current.headers['etag'];newdecision=client.post('/api/v1/findings/'+newid+'/decisions',json=body,headers=h(tag,key));assert newdecision.status_code==200,newdecision.json();replay=client.post('/api/v1/findings/'+newid+'/decisions',json=body,headers=h(tag,key))
   with connection() as c:
    persisted=c.execute('SELECT * FROM decisions WHERE id=%s',(newdecision.json()['id'],)).fetchone();oldhead=c.execute("SELECT decision_id FROM decision_heads WHERE bid_id=%s AND object_kind='finding' AND object_id=%s",(bid,oldroot)).fetchone()['decision_id']
   check('new_manual_decision_writes_new_finding_not_physical_old_root',str(persisted['object_id'])==newid and str(oldhead)==late.json()['id'] and client.get('/api/v1/findings/'+newid).json()['lifecycle']=='deferred')
   check('new_decision_response_loss_replays_same_decision_without_new_write',replay.status_code==200 and replay.json()==newdecision.json() and replay.headers.get('Idempotent-Replayed')=='true')
   stage='late_ancestor_after_own_decision'
   ancestor=client.get('/api/v1/findings/'+oldroot);later=client.post('/api/v1/findings/'+oldroot+'/decisions',json=dict(decision='dismiss',reason='第二个迟到旧稿决定不改变新稿延期核验',evidence_ids=old['first']['finding']['evidence_ids']),headers=h(ancestor.headers['etag']));assert later.status_code==200
   newer=client.get('/api/v1/findings/'+newid).json();check('own_new_decision_takes_priority_over_later_ancestor_dismiss',newer['lifecycle']=='deferred' and newer['decision']['id']==newdecision.json()['id'])
   with connection() as c:
    raw_after=c.execute('SELECT * FROM findings WHERE id=%s',(newid,)).fetchone();old_after=c.execute('SELECT * FROM decisions WHERE id=%s',(old['old_human_decision_id'],)).fetchone();modelcount=c.execute('SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s',(bid,)).fetchone()['n']
   check('read_repair_and_new_decisions_do_not_rewrite_immutable_finding_or_old_decision',sha(clean(raw_after))==sha(clean(raw_old)) and sha(clean(old_after))==sha(clean(decision_old)) and modelcount==0)
   result.update(passed=all(x['passed'] for x in result['checks']),old_bad_row_id=newid,raw_carried_from_id=str(raw_old['carried_from_id']),old_root_id=oldroot,new_decision_id=newdecision.json()['id'])
 except Exception as e:result['fatal']=dict(stage=stage,type=type(e).__name__,code=getattr(e,'code',None),message='Independent legacy read/decision test incomplete; no secrets logged')
 result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest(),baseline_result_sha256=hashlib.sha256(oldpath.read_bytes()).hexdigest());(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
