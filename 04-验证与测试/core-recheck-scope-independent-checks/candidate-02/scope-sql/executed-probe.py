"""Independent body-vs-other-document range/diagnostic cases, synthetic SQL only."""
from pathlib import Path
from uuid import uuid4
import hashlib,importlib.util,json,os
from psycopg.types.json import Jsonb
from packages.domain.runtime import connection,sha,assert_role
from packages.domain.core_recheck_scope import evaluate


def main():
 root=Path(os.environ['QA_SCOPE_ROOT']);out=root/os.environ.get('QA_SCOPE_EXECUTION','scope-sql');out.mkdir(exist_ok=False);records=json.loads((root/'runtime-manifest.json').read_text());assert all(hashlib.sha256((root/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in records);assert_role();assert_role(True)
 spec=importlib.util.spec_from_file_location('qa_scope_sql_seed_only',root/'execution-inputs/tests/test_core_recheck_scope_integration.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
 result=dict(passed=False,checks=[],scope='Restricted actual PG/API fixture creation and ordinary Worker reads; explicit SQL parse/evidence fixtures, no parser/model/object/Kafka execution',model_requests=0,object_requests=0,consumers_started=0)
 def check(n,v,o):result['checks'].append(dict(name=n,passed=bool(v),observation=o))
 with connection() as c:
  actor,bid,material,ruleset,rule,command,operation=[uuid4() for _ in range(7)]
  c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立条款必要范围')",(actor,));c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立范围反例','{}',%s)",(bid,actor));c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,sha(str(material))));c.execute("INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",(ruleset,bid,material,sha(str(ruleset))))
  body=helper.add_document(c,bid,actor,[('usable','Reference is to separate tender document page 100; our duration is 90 days.')]+[('usable','Current body page '+str(i)+': no duration override.') for i in range(2,11)])
  external=helper.add_document(c,bid,actor,[('usable','Separate tender page '+str(i)) for i in range(1,101)])
  c.execute("INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'disqualification','本项目总工期必须90天','[]','[]',%s,'[]')",(rule,bid,ruleset,[external['evidence'][-1]]))
  c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-core-scope',%s,%s,%s)",(command,actor,bid,str(command),sha(str(command))));c.execute("INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'review',%s,'succeeded','review/1',now())",(operation,bid,command,sha(str(operation))))
  unrelated=helper.add_document(c,bid,actor,[('partial','Unrelated standalone marketing attachment')],coverage='partial')
  plain=helper.add_document(c,bid,actor,[('usable','Current body duration is 90 days.'),('usable','Other complete text.')])
  unread=helper.add_document(c,bid,actor,[('usable','Current body duration is 90 days.'),('usable','Other text.'),('partial','')],coverage='partial')
 with connection(True) as c:
  manifest=dict(body_version_id=str(body['version']),parse_run_ids=[str(body['parse']),str(external['parse'])])
  proof=evaluate(c,manifest=manifest,rule_id=rule,evidence_ids=[body['evidence'][0]],operation_id=operation)
  check('fully_read_body10_does_not_require_tender_page100_as_body_page100',proof['eligible'] and proof['required_pages']==list(range(1,11)),proof)
  m=dict(body_version_id=str(plain['version']),parse_run_ids=[str(plain['parse']),str(unrelated['parse'])])
  proof=evaluate(c,manifest=m,rule_id=rule,evidence_ids=[plain['evidence'][0]],operation_id=operation)
  check('complete_body_can_qualify_with_independent_unrelated_partial_attachment',proof['eligible'],proof)
  proof=evaluate(c,manifest=dict(body_version_id=str(unread['version']),parse_run_ids=[str(unread['parse'])]),rule_id=rule,evidence_ids=[unread['evidence'][0]],operation_id=operation)
  check('unreferenced_unknown_body_page_remains_required',not proof['eligible'] and proof['unread_pages']==[3],proof)
 with connection() as c:
  diagnostic=uuid4();c.execute("INSERT INTO validation_diagnostics(id,bid_id,operation_id,step_key,code,field_path,evidence_ids,action,message) VALUES(%s,%s,%s,'core-judgment','unit_mismatch',%s,%s,'needs_review','独立合成工期单位未核验')",(diagnostic,bid,operation,'rules.'+str(rule),[plain['evidence'][0]]))
 with connection(True) as c:
  proof=evaluate(c,manifest=m,rule_id=rule,evidence_ids=[plain['evidence'][0]],operation_id=operation)
  check('related_core_diagnostic_blocks_closure_despite_readable_complete_body',not proof['eligible'] and str(diagnostic) in proof['diagnostic_ids'],proof)
 result.update(passed=all(x['passed'] for x in result['checks']),bid_id=str(bid),fixture=dict(rule_id=str(rule),body=body,external=external,plain=plain,unrelated=unrelated,unread=unread,operation_id=str(operation)),probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest())
 (out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(result['checks']),failed=[x['name'] for x in result['checks'] if not x['passed']]))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
