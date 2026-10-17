import json,os,hashlib
from pathlib import Path
from packages.domain.runtime import connection,clean
root=Path(os.environ['QA_ASSISTANT_ROOT'])
with connection() as c:
    functions=c.execute("SELECT p.proname,p.prosecdef,p.proconfig,pg_get_function_identity_arguments(p.oid) AS signature,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='score_execution' ORDER BY p.proname,p.oid").fetchall()
    tables=c.execute("SELECT t.relname,t.relacl::text AS privileges FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='score_execution' AND t.relkind='r' ORDER BY t.relname").fetchall()
    role=c.execute("SELECT current_user,r.rolsuper,r.rolbypassrls FROM pg_roles r WHERE rolname=current_user").fetchone();assert not role['rolsuper'] and not role['rolbypassrls']
    assert any(x['proname']=='reserve_call' for x in functions) and any(x['proname']=='publish_result' for x in functions)
    data=clean(dict(actual_api_role=role,functions=functions,tables=tables))
(root/'actual-score-catalog.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(functions=len(functions),tables=len(tables),api_nonowner=True)))
