import json,os,hashlib,sys
from pathlib import Path
from packages.domain.runtime import connection,clean
from packages.word_intake.renderer import configured_profile
from apps.api.main import app
from apps.api.export_openapi import implemented_openapi
root=Path(os.environ['QA_ASSISTANT_ROOT']);runtime=Path('/workspace/tender-workbench/docs/word-mainflow-development-checks/runtime-preview-02')
actual=implemented_openapi(app);assert actual==json.loads((root/'actual-02.openapi.json').read_text())
os.environ['WORD_RENDER_PROFILE_PATH']='/workspace/tender-workbench/.local/word-bridge-runtime-preview02-profile.json'
profile=configured_profile();assert profile['profile_digest']=='61284d08db2703f4d753a3d098e0e54d8d84926ec5fa65c7ee3d3cf1f14a406b'
with connection() as c:
 default=c.execute("SELECT pg_get_expr(d.adbin,d.adrelid) AS value FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_attrdef d ON d.adrelid=t.oid AND d.adnum=a.attnum WHERE n.nspname='design' AND t.relname='word_render_runs' AND a.attname='created_at'").fetchone();assert default['value']=='clock_timestamp()'
 functions=c.execute("SELECT n.nspname,p.proname,p.prosecdef,p.proconfig,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='design' AND p.proname LIKE '%%word%%' ORDER BY p.proname,p.oid").fetchall()
 own=c.execute("SELECT r.operation_id,r.bid_id,r.source_document_version_id,r.profile_digest,r.outcome,r.pdf_document_version_id,r.created_at,r.finished_at,o.state FROM word_render_runs r JOIN operations o ON o.id=r.operation_id JOIN document_versions v ON v.id=r.source_document_version_id JOIN members m ON m.id=v.created_by WHERE m.display_name='独立助手合成成员' AND v.original_name='独立施工方案.docx' ORDER BY r.created_at").fetchall()
 calls=c.execute("SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=ANY(%s::uuid[])",([str(x['bid_id']) for x in own],)).fetchone()['n'];assert calls==0
for name,m in list(sys.modules.items()):
 p=getattr(m,'__file__',None)
 if p and name.startswith(('apps.','packages.')):assert Path(p).resolve().is_relative_to(runtime)
result=dict(actual_matches_frozen=True,profile_digest=profile['profile_digest'],created_at_actual_default=default['value'],word_schema_functions=clean(functions),own_observed_runs=clean(own),own_model_calls=calls,scope='Catalog reads and only independently created original runs; outcome metadata, no credentials/transport tickets')
(root/'actual-catalog-and-own-runs.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(default=default['value'],own_runs=len(own),model_calls=calls,actual_matches=True,profile_matches=True)))
