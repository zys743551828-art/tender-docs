"""Read-only deployed DDL/function/role identity; never reads data or login secrets."""

import hashlib
import json
import os
from pathlib import Path

from packages.domain.runtime import connection


def main():
    out = Path(os.environ["QA_RAG_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    with connection() as c:
        columns = c.execute(
            "SELECT table_schema,table_name,column_name,data_type,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema IN ('design','retrieval') ORDER BY table_schema,table_name,ordinal_position"
        ).fetchall()
        constraints = c.execute(
            "SELECT n.nspname AS schema,t.relname AS relation,co.conname AS name,pg_get_constraintdef(co.oid) AS definition FROM pg_constraint co JOIN pg_class t ON t.oid=co.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname IN ('design','retrieval') ORDER BY 1,2,3"
        ).fetchall()
        functions = c.execute(
            "SELECT n.nspname AS schema,p.proname AS name,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='retrieval' OR n.nspname='design' AND p.proname IN ('lock_worker_bid','acquire_step','finish_step','reserve_research_call') ORDER BY 1,2,3"
        ).fetchall()
        identity = c.execute("SELECT current_user,current_database(),version()").fetchone()
        role = c.execute(
            "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        owners = c.execute(
            "SELECT n.nspname AS schema,c.relname AS name,c.relowner::regrole::text AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('design','retrieval') AND c.relkind='r' ORDER BY 1,2"
        ).fetchall()
    record = dict(
        identity=identity,
        role=role,
        table_owners=owners,
        columns=columns,
        constraints=constraints,
        functions=functions,
        scope="post-execution read-only database identity; test runs separately freeze exact Python/declared candidate bytes",
    )
    (out / "actual-database-schema.json").write_text(
        json.dumps(record, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    frozen = out / "input-sql"
    frozen.mkdir()
    inputs = []
    for path in sorted(Path("packages/contracts/sql").glob("*.sql")):
        (frozen / path.name).write_bytes(path.read_bytes())
        inputs.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (out / "source-sql-manifest.json").write_text(json.dumps(inputs, indent=2) + "\n")
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    print(
        dict(
            readonly=True,
            table_count=len(owners),
            function_count=len(functions),
            superuser=role["rolsuper"],
            actual_schema_sha256=hashlib.sha256(
                (out / "actual-database-schema.json").read_bytes()
            ).hexdigest(),
        )
    )


if __name__ == "__main__":
    main()
