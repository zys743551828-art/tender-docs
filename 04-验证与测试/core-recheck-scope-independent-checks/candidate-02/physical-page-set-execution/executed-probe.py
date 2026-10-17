"""Independent exact physical-page-set negative fixtures, actual restricted PG."""
import hashlib,json,os
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from packages.domain.runtime import connection,sha
from packages.domain.core_recheck_scope import evaluate

def main():
 root=Path(os.environ['QA_SCOPE_ROOT']);out=root/'physical-page-set-execution';out.mkdir(exist_ok=False);fixture=json.loads((root/'scope-sql/result.json').read_text());bid=fixture['bid_id'];version=fixture['fixture']['plain']['version'];rule=fixture['fixture']['rule_id'];items=[]
 with connection() as c:
  for label,count,pages in [('missing_and_outside',2,[1,3]),('extra_page',2,[1,2,3]),('zero_declared_pages',0,[1])]:
   parse,evidence=uuid4(),uuid4();c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,'complete','[]')",(parse,bid,version,sha(str(parse)),sha({}),count))
   for page in pages:c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,595,842,0,'usable','[]')",(bid,parse,page))
   text='Duration90 days';c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(evidence,bid,parse,text,sha(text)))
   items.append((label,parse,evidence))
 checks=[]
 with connection(True) as c:
  for label,parse,evidence in items:
   proof=evaluate(c,manifest=dict(body_version_id=version,parse_run_ids=[str(parse)]),rule_id=rule,evidence_ids=[evidence])
   checks.append(dict(name=label+'_never_qualifies_as_complete_body',passed=not proof['eligible'] and proof['reason']=='BODY_PHYSICAL_PAGE_SET_INVALID',observation=proof))
  proof=evaluate(c,manifest=dict(body_version_id=version,parse_run_ids=[str(items[0][1]),fixture['fixture']['plain']['parse']]),rule_id=rule,evidence_ids=[items[0][2]])
  checks.append(dict(name='two_frozen_body_parses_are_not_guessed',passed=not proof['eligible'] and proof['reason']=='BODY_PARSE_SCOPE_UNPROVEN',observation=proof))
 result=dict(passed=all(x['passed'] for x in checks),checks=checks,scope='Actual nonowner PostgreSQL; explicit synthetic complete-labeled malformed parse sets; no parser/model/object/Kafka call',probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest());(out/'executed-probe.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(dict(passed=result['passed'],checks=len(checks)))
 return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
