import os,json
from pathlib import Path
from packages.domain.runtime import connection,clean
r=Path(os.environ['QA_ASSISTANT_ROOT'])
with connection() as c:
 functions=c.execute("SELECT p.proname,p.prosecdef,p.proconfig,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='technical_scale' ORDER BY p.proname,p.oid").fetchall()
 checks=c.execute("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=to_regclass('score_execution.inputs') ORDER BY conname").fetchall();assert any('technical_maximum IS NULL' in x['definition'] for x in checks)
 containers=c.execute("SELECT r.operation_id,r.outcome FROM word_render_runs r JOIN document_versions d ON d.id=r.source_document_version_id JOIN members m ON m.id=d.created_by WHERE m.display_name='独立助手合成成员' AND d.original_name='独立技术满分招标.docx' ORDER BY r.created_at").fetchall();assert len(containers)==4 and all(x['outcome']['isolation']['own_job_cleanup_confirmed'] for x in containers)
 own=c.execute("SELECT v.id,v.purpose,v.technical_maximum,v.created_at FROM technical_scale.versions v JOIN bids b ON b.id=v.bid_id JOIN members m ON m.id=b.created_by WHERE m.display_name='独立助手合成成员' ORDER BY v.created_at").fetchall()
 data=clean(dict(technical_scale_functions=functions,unchanged_score_input_checks=checks,own_four_Word_results_cleanup=containers,own_scale_versions=own))
(r/'actual-scale-catalog-and-cleanup.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(scale_functions=len(functions),own_Word_runs=len(containers),all_cleanup_confirmed=True,score_maximum_null_check=True)))
