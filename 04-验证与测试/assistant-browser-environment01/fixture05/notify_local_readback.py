"""Read exact synthetic project's completed local runs, no scheduler or dispatch."""
import os,sys,json,shlex,hashlib
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
base=Path('/workspace/tender-docs/04-验证与测试/assistant-browser-environment01')
seed=json.loads((base/'fixture01/synthetic-source.json').read_text());bid=seed['project_id']
profile=Path('/workspace/tender-workbench/.local/assistant_ui01-runtime.env')
values={}
for line in profile.read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);parts=shlex.split(v,comments=True); values[k.strip()]=parts[0] if parts else ''
with psycopg.connect(values['API_DATABASE_DSN'],row_factory=dict_row) as c:
 c.execute('SET TRANSACTION READ ONLY');c.execute('SET LOCAL search_path=design,public')
 rows=c.execute("SELECT r.id,r.state,r.frozen_manifest->>'task_kind' AS kind FROM task_runs r WHERE r.bid_id=%s ORDER BY r.id",(bid,)).fetchall()
 print(json.dumps({'project_id':bid,'runs':[{k:str(v) if v is not None else None for k,v in r.items()} for r in rows]},ensure_ascii=False))
