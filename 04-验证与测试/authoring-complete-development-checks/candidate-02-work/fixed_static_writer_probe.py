"""New static writer interop fix, existing approved Word03 port; no PAGE execution."""
import json
from pathlib import Path
import sys
from uuid import UUID
sys.path.insert(0,'/workspace/tender-workbench')
from packages.authoring.repository import snapshot_content
from packages.authoring.structured_docx import assemble,WRITER_DIGEST
from packages.domain.runtime import connection,sha
from packages.word_intake.renderer import configured_profile,render
HERE=Path(__file__).resolve().parent
record=json.loads(Path('/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/candidate-01/evidence/synthetic-artifact.json').read_text())
with connection(True) as c:
 snapshot=snapshot_content(c,UUID(record['bid_id']),UUID(record['snapshot_id']))
assert c.closed
source=assemble(snapshot)
(HERE/'fixed-static-writer.docx').write_bytes(source)
result=render(source,configured_profile())
if result['pdf']:(HERE/'fixed-static-writer.pdf').write_bytes(result['pdf'])
report={k:v for k,v in result.items() if k!='pdf'}
report.update(scope='Public synthetic A2 static DOCX from real restricted PG snapshot; connection closed before approved Word03 generic sandbox. New generated PAGE lane not executed. No OSS/model/Kafka.',writer_digest=WRITER_DIGEST,snapshot_id=record['snapshot_id'],old_frozen_source_digest=record['docx_digest'],new_source_digest=sha(source),connection_closed_before_conversion=True)
(HERE/'fixed-static-writer-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'state':result['state'],'coverage':result['coverage'],'gaps':result['gaps'],'pages':result['chain'].get('page_count'),'source_digest':sha(source),'cleanup':(result['isolation'] or {}).get('own_job_cleanup_confirmed')},ensure_ascii=False))
