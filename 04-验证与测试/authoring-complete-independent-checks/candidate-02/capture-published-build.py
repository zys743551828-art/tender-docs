import os,json,hashlib
from pathlib import Path
from packages.domain.runtime import connection,clean
root=Path(os.environ['QA_ASSISTANT_ROOT']);value=json.loads((root/'own-artifacts/positive/readback.json').read_text())
with connection() as c:
    rows=c.execute("SELECT DISTINCT o.build_id,b.operation_id,i.draft_snapshot_id,i.manifest FROM build_outputs o JOIN builds b ON b.id=o.build_id JOIN build_inputs i ON i.id=b.input_id JOIN document_versions v ON v.id=o.document_version_id JOIN blobs z ON z.id=v.blob_id WHERE o.format='pdf' AND z.digest=%s",(value['pdf_sha256'],)).fetchall();assert len(rows)==1
    row=rows[0];count=c.execute('SELECT count(*) n FROM effect_receipts WHERE operation_id=%s',(row['operation_id'],)).fetchone()['n'];assert count==1
    actor=c.execute('SELECT created_by FROM bids WHERE id=(SELECT bid_id FROM builds WHERE id=%s)',(row['build_id'],)).fetchone()['created_by'];assert c.execute('SELECT display_name FROM members WHERE id=%s',(actor,)).fetchone()['display_name']=='独立助手合成成员'
    role=c.execute('SELECT current_user,r.rolsuper,r.rolbypassrls FROM pg_roles r WHERE rolname=current_user').fetchone();assert not role['rolsuper'] and not role['rolbypassrls']
    columns=c.execute("SELECT t.relname,a.attname,format_type(a.atttypid,a.atttypmod) type FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='design' AND t.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY t.relname,a.attnum",(['builds','build_inputs','build_outputs','authoring_structure_versions','authoring_structure_nodes','authoring_plan_versions'],)).fetchall()
(root/'actual-published-build-and-schema.json').write_text(json.dumps(clean(dict(actual_api_nonowner=role,published_positive_build=row,exact_effect_receipts=count,columns=columns)),ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(positive_effect_receipts=count,schema_columns=len(columns),physical_pages=len(value['physical_pages']),body_digits=value['body_digits'])))
