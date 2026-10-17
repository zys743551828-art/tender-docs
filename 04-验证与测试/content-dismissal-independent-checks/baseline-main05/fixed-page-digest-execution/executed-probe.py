"""Independent old-decision applicability: actual nonowner fenced content publisher."""
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import hashlib,json,os
from psycopg.types.json import Jsonb
from apps.worker import engine,content_handlers
from packages.domain.content_checks import prepare,validate_output
from packages.domain.finding_lifecycle import lifecycle,origin,decision
from packages.domain.review_data import read_bundle,source_closure
from packages.domain.rule_sets import rules
from packages.domain.runtime import assert_role,connection,enqueue_plan,sha,clean,audit
from packages.rag.indexing import ensure_parse_sources


def main():
 root=Path(os.environ['QA_CONTENT_ROOT']);out=root/os.environ.get('QA_CONTENT_EXECUTION','execution');out.mkdir(exist_ok=False);entries=json.loads((root/'runtime-manifest.json').read_text())
 def verify():assert all(hashlib.sha256((root/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in entries)
 verify();assert_role();assert_role(True);assert Path(os.environ['WORKBENCH_ENV_FILE']).name=='delivery_protocols01-checks.env'
 assets=Path('/workspace/tender-docs/04-验证与测试/content-dismissal-independent-checks/fixture-inputs');inputs=json.loads((assets/'manifest.json').read_text());assert all(hashlib.sha256((assets/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in inputs)
 result=dict(passed=False,checks=[],scope='Own synthetic real-PDF SHA and explicit parsed-page/model-output SQL fixtures; actual engineering validate_output and restricted Worker fenced content publish. A document/1 test envelope routes only publication through explicit patch to actual content publisher; not real model parsing/Kafka/API acceptance.',paid_calls=0,model_calls=0,object_requests=0,consumers_started=0)
 def check(n,v,o=None):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 actor,bid=[uuid4() for _ in range(2)]
 def add_document(c,name,texts,kind):
  version,parse=uuid4(),uuid4();raw=(assets/name).read_bytes();digest=sha(raw);c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf') ON CONFLICT(digest) DO NOTHING",(uuid4(),digest,'qa-synthetic/'+digest,len(raw)));blob=c.execute('SELECT id FROM blobs WHERE digest=%s',(digest,)).fetchone()['id'];c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now()-interval '1 hour',%s,%s)",(version,bid,blob,name,kind,actor));c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,'complete','[]')",(parse,bid,version,sha('explicit-independent-page-fixture'),sha({}),len(texts)));ev=[]
  for page,text in enumerate(texts,1):
   identity=uuid4();c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,595,842,0,'usable','[]')",(bid,parse,page));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,%s,0,'text',%s,%s)",(identity,bid,parse,page,text,sha(text.encode())));ev.append(identity)
  _,source=ensure_parse_sources(c,bid,parse,publish_readable=True);closure=source_closure(c,[dict(id=str(source))]);return dict(version=version,parse=parse,evidence=ev,blob=blob,digest=digest,texts=texts,ref=dict(id=str(source),kind='body' if kind=='bid_body' else 'tender',digest=digest,lineage_manifest_digest=sha(closure)))
 def assertion(doc,side,value):return dict(evidence_id=str(doc['evidence'][side]),quote=doc['texts'][side],subject='工程A',scope='标段一',attribute='工期',value=str(value),unit='天',relation='equal')
 def round_fixture(doc,ordinal,tender,parent_rule=None):
  with connection() as c:
   material,ruleset,rule,review,inputid,cmd=[uuid4() for _ in range(6)];c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,'{}',%s)",(material,bid,ordinal,sha(str(material))))
   for role,d in [('body',doc),('tender',tender)]:c.execute('INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,0)',(material,bid,d['version'],role))
   c.execute("INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",(ruleset,bid,material,sha(str(ruleset))));c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'mandatory','本工程工期必须一致','[]','[]',%s,'[]')",(rule,bid,ruleset,tender['evidence']))
   refs=[doc['ref'],tender['ref']];manifest=clean(dict(bid_id=bid,body_version_id=doc['version'],rule_set_id=ruleset,source_refs=refs,as_of=c.execute('SELECT clock_timestamp() AS at').fetchone()['at'],parse_run_ids=[doc['parse'],tender['parse']],content_checks=dict(consistency=True,scope_residue=False)));evidence=clean(dict(documents=read_bundle(c,bid,material),rules=rules(c,ruleset),confirmed_facts=[],source_closure=source_closure(c,refs)))
   c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-dismissal-round',%s,%s,%s)",(cmd,actor,bid,str(cmd),sha(str(cmd))));op=enqueue_plan(c,bid,cmd,'qa-content-applicability',dict(id=review),stages=[dict(key='consistency-check',depends_on=[])],handler_version='document/1',topic='tender.task.document.v1');c.execute('INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)',(inputid,bid,material,doc['version'],sha(str(inputid)),sha('explicit-offline-check'),Jsonb(manifest),doc['blob'],sha(manifest),manifest['as_of'],sha('context')));c.execute('INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)',(review,bid,inputid,op['id']));msg=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(op['id'],)).fetchone()['payload']
  return dict(row=dict(manifest=manifest,review_id=str(review)),evidence=evidence,message=msg,review=review,rule=rule,operation=op['id'],doc=doc)
 def publish_round(fixture,values):
  row,evidence,msg=fixture['row'],fixture['evidence'],fixture['message'];prepared=prepare('consistency',row,evidence,evidence['documents']);candidate=dict(rule_id=str(fixture['rule']),severity='critical',statement='MODEL_UNTRUSTED_EXTRA9999',left=assertion(fixture['doc'],0,values[0]),right=assertion(fixture['doc'],1,values[1]));validated=validate_output('consistency',dict(read_evidence_ids=list(prepared['blocks']),issues=[candidate],gaps=[]),prepared,evidence);assert validated['check_state']=='complete' and len(validated['issues'])==1
  result['last_publish_phase']='acquire';lease=engine.acquire(msg);assert lease
  result['last_publish_phase']='finish'
  with patch.object(engine,'publish',content_handlers.publish):
   assert engine.finish(msg,lease,dict(row=row,evidence=evidence,outcome=validated));assert engine.finish(msg,lease,dict(row=row,evidence=evidence,outcome=validated)) is False
  with connection(True) as c:
   finding=c.execute('SELECT * FROM findings WHERE review_id=%s',(fixture['review'],)).fetchone();event=c.execute("SELECT payload FROM audit_entries WHERE event_type='content_check.finding_recorded' AND payload->>'finding_id'=%s",(str(finding['id']),)).fetchone()['payload'];actual=clean(dict(finding=finding,lifecycle=lifecycle(c,finding),root_id=origin(c,finding)['id'],decision_id=decision(c,finding)[0]['id'] if decision(c,finding)[0] else None,issue_key=event['issue_key'],assertions=event['assertions']));count=c.execute('SELECT count(*) AS n FROM model_calls WHERE operation_id=%s',(fixture['operation'],)).fetchone()['n'];assert count==0
  return actual
 stage='setup'
 try:
  with connection() as c:
   c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立content决定范围')",(actor,));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立新事实不得借旧dismiss','{\"auto_review\":false}',%s)",(bid,actor));old_doc=add_document(c,'old90-120.pdf',['工程A标段一工期90天','工程A标段一工期120天'],'bid_body');tender=add_document(c,'tender.pdf',['工程A标段一工期必须一致且等于90天'],'source_original')
  stage='first_valid_conflict';first_fixture=round_fixture(old_doc,1,tender);first=publish_round(first_fixture,(90,120));check('old_real_grounded_90_120_starts_open_with_two_evidence_sides',first['lifecycle']=='open' and set(first['finding']['evidence_ids'])==set(map(str,old_doc['evidence'])) and '9999' not in first['finding']['statement'])
  stage='human_old_evidence_only_dismiss'
  with connection() as c:
   c.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(bid,));decided=uuid4();c.execute("INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'finding',%s,1,'dismiss','仅旧90/120两侧证据的有理由决定，不处理未来稿件和数值',%s,%s)",(decided,bid,first['finding']['id'],old_doc['evidence'],actor));c.execute("INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'finding',%s,2,%s)",(bid,first['finding']['id'],decided));audit(c,actor,bid,'finding.decided',dict(finding_id=first['finding']['id'],decision_id=str(decided),decision='dismiss',fixture='Explicit old-evidence human artifact; not HTTP approval acceptance'))
  with connection(True) as c:old_row=c.execute('SELECT * FROM findings WHERE id=%s',(first['finding']['id'],)).fetchone();check('reasoned_old_decision_is_actual_and_limited_to_old_evidence',lifecycle(c,old_row)=='dismissed' and decision(c,old_row)[0]['id']==decided)
  stage='identical_body_control';same=publish_round(round_fixture(old_doc,2,tender),(90,120));check('identical_frozen_body_and_values_may_keep_old_decision',same['lifecycle']=='dismissed' and same['root_id']==first['finding']['id'] and same['decision_id']==str(decided))
  stage='new_body_changed_values'
  with connection() as c:new_doc=add_document(c,'new95-125.pdf',['工程A标段一工期95天','工程A标段一工期125天'],'bid_body')
  changed=publish_round(round_fixture(new_doc,3,tender),(95,125));check('new_body_changed_sha_and_two_disjoint_grounded_evidence_sides',new_doc['digest']!=old_doc['digest'] and set(new_doc['evidence']).isdisjoint(old_doc['evidence']) and set(changed['finding']['evidence_ids'])==set(map(str,new_doc['evidence'])) and changed['assertions']['left']['value']=='95' and changed['assertions']['right']['value']=='125')
  check('same_issue_group_does_not_authorize_old_human_decision_for_new_values',changed['issue_key']==first['issue_key'] and changed['lifecycle']=='open' and changed['root_id']!=first['finding']['id'] and changed['decision_id'] is None,changed)
  with connection(True) as c:old_row=c.execute('SELECT * FROM findings WHERE id=%s',(first['finding']['id'],)).fetchone();check('new_issue_does_not_rewrite_old_dismissed_history',lifecycle(c,old_row)=='dismissed' and decision(c,old_row)[0]['id']==decided)
  result.update(passed=all(x['passed'] for x in result['checks']),bid_id=str(bid),first=first,same_body=same,changed_body=changed,old_body_digest=old_doc['digest'],new_body_digest=new_doc['digest'],old_human_decision_id=str(decided))
 except Exception as error:result['fatal']=dict(stage=stage,type=type(error).__name__,code=getattr(error,'code',None),message='Independent publication probe incomplete; no private configuration exposed')
 verify();result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest(),fixture_manifest_sha256=hashlib.sha256((assets/'manifest.json').read_bytes()).hexdigest());(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']],fatal=result.get('fatal')))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
