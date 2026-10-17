"""Independent new-probe catalog backup/restore; no existing DB mutation or object requests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb


def main():
    root=Path(os.environ["QA_BACKUP_ROOT"])
    out=root/"execution";out.mkdir(exist_ok=False)
    runtime=Path(tempfile.mkdtemp(prefix="tender-qa-generic-"))
    runtime.chmod(0o700)
    shutil.copytree(root/"frozen-inputs",runtime,dirs_exist_ok=True)
    sys.path.insert(0,str(runtime))
    from deploy.backups import generic,run
    assert Path(generic.__file__).is_relative_to(runtime)
    inputs=json.loads((root/"input-manifest.json").read_text())

    def verify():
        assert all(hashlib.sha256((runtime/r["path"]).read_bytes()).hexdigest()==r["sha256"] for r in inputs)

    verify()
    profile=Path("/workspace/tender-workbench/.local/backend-checks.env")
    values=run.configuration(profile)
    values["OBJECT_STORE_BACKEND"]="disabled-for-independent-db-component"
    source_name="tender_qa_generic_source_"+uuid4().hex
    source_owner=make_conninfo(values["OWNER_DATABASE_DSN"],dbname=source_name)
    source_api=make_conninfo(values["API_DATABASE_DSN"],dbname=source_name)
    owner_connection=values["OWNER_DATABASE_DSN"]
    container="tender-workbench-dev-postgres-1"
    result=dict(passed=False,checks=[],source_probe_database=source_name,owner_scope="CREATE and initialize this new random probe and generic's new restore only",model_requests=0,object_requests=0,consumers_started=0,existing_database_mutations=0)

    def check(name,value,observation=None):result["checks"].append(dict(name=name,passed=bool(value),observation=observation))

    def denied(fn,code):
        try:fn()
        except run.BackupError as error:return str(error)==code
        return False

    stage="new_probe_initialize"
    try:
        with psycopg.connect(owner_connection,autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source_name)))
        with psycopg.connect(source_owner,autocommit=True) as c:
            for name in ["core.sql","domain.sql","retrieval.sql","worker-functions.sql","roles.sql"]:
                c.execute((runtime/"packages/contracts/sql"/name).read_text())
            # Only initialization owner of the brand-new QA database creates DDL.
            c.execute('CREATE SCHEMA "future.metrics 中文"')
            c.execute('CREATE TYPE "future.metrics 中文"."enum.quote" AS ENUM (\'first\',\'第二项\')')
            c.execute('CREATE TABLE "future.metrics 中文"."row\""dot"(id integer PRIMARY KEY,payload jsonb,embedding design.vector(4),stage "future.metrics 中文"."enum.quote")')
            c.execute('CREATE TABLE "future.metrics 中文".parts(id integer, note text) PARTITION BY RANGE(id)')
            c.execute('CREATE TABLE "future.metrics 中文".parts_a PARTITION OF "future.metrics 中文".parts FOR VALUES FROM(0) TO(100)')
            c.execute('CREATE MATERIALIZED VIEW "future.metrics 中文".cached AS SELECT 7 AS constant_value')
            c.execute('CREATE FUNCTION "future.metrics 中文".blocked_writer() RETURNS integer LANGUAGE SQL AS \'SELECT 7\'')
            c.execute('GRANT USAGE ON SCHEMA "future.metrics 中文" TO tender_api')
            c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "future.metrics 中文" TO tender_api')
        values["OWNER_DATABASE_DSN"],values["API_DATABASE_DSN"]=source_owner,source_api
        with psycopg.connect(source_api) as c:
            role=c.execute("SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
            c.execute('INSERT INTO "future.metrics 中文"."row\""dot" VALUES(1,%s,\'[1,0,0,0]\',\'first\')',(Jsonb({"value":"原始值","nested":[None,True,{"a":"quote \" slash \\"}]}),))
            c.execute('INSERT INTO "future.metrics 中文".parts VALUES(1,\'快照前分区行\')')
        check("source_export_login_is_actual_restricted_nonowner",not any(role[1:]))
        archive_root=runtime/".local/backups";archive_root.mkdir(mode=0o700,parents=True)
        (runtime/".local").chmod(0o700)
        archive=archive_root/("independent-"+uuid4().hex)

        def after_snapshot():
            # A distinct restricted LOGIN transaction commits UPDATE and INSERT.
            with psycopg.connect(source_api) as c:
                c.execute('UPDATE "future.metrics 中文"."row\""dot" SET payload=%s WHERE id=1',(Jsonb({"value":"快照后的改值"}),))
                c.execute('INSERT INTO "future.metrics 中文"."row\""dot" VALUES(2,%s,\'[0,1,0,0]\',\'第二项\')',(Jsonb({"value":"后插行"}),))
                c.execute('INSERT INTO "future.metrics 中文".parts VALUES(2,\'快照后分区行\')')
        stage="nonowner_snapshot"
        snapshot=generic.snapshot(values,archive,container,include_objects=False,after_snapshot=after_snapshot)
        new_tables=[r for r in snapshot["tables"] if r["schema"]=="future.metrics 中文"]
        check("future_schema_quote_partition_materialized_and_vector_catalog_discovered",len(new_tables)==4 and {r["kind"] for r in new_tables}=={"r","p","m"} and any(e["name"]=="vector" and e["schema"]=="design" for e in snapshot["catalog"]["extensions"]))
        check("snapshot_inventory_excludes_late_insert_and_update",all(r["rows"]==1 for r in new_tables))
        check("database_component_never_claims_full_object_backup",snapshot["objects_included"] is False and denied(lambda:generic.validate(archive,require_objects=True),"DATABASE_COMPONENT_HAS_NO_OBJECT_BYTES"))
        stage="new_random_restore"
        receipt=generic.restore(values,archive,container,include_objects=False)
        restored=receipt["database"]
        check("restore_creates_new_database_and_verifies_entire_dynamic_catalog",restored!=source_name and receipt["status"]=="passed_database_component_only" and receipt["tables_verified"]==len(snapshot["tables"]) and "future.metrics 中文" in receipt["schemas_verified"] and receipt["full_application_restore_qualified"] is False and receipt["objects_verified"]==0)
        target_api=make_conninfo(source_api,dbname=restored)
        with psycopg.connect(target_api) as c:
            row=c.execute('SELECT id,payload,embedding,stage FROM "future.metrics 中文"."row\""dot" ORDER BY id').fetchall()
            parts=c.execute('SELECT id,note FROM "future.metrics 中文".parts ORDER BY id').fetchall()
            cached=c.execute('SELECT constant_value FROM "future.metrics 中文".cached').fetchone()[0]
            readonly=c.execute("SHOW transaction_read_only").fetchone()[0]
        check("restored_exact_pre_snapshot_json_enum_vector_and_partition_rows",len(row)==1 and row[0][0]==1 and row[0][1]["value"]=="原始值" and row[0][2]=="[1,0,0,0]" and row[0][3]=="first" and parts==[(1,"快照前分区行")] and cached==7)
        check("restored_default_is_readonly",readonly=="on")
        for name,statement in [("insert",'INSERT INTO "future.metrics 中文"."row\""dot"(id) VALUES(3)'),("update",'UPDATE "future.metrics 中文"."row\""dot" SET id=9'),("delete",'DELETE FROM "future.metrics 中文"."row\""dot"'),("truncate",'TRUNCATE "future.metrics 中文"."row\""dot"'),("schema_create",'CREATE TABLE "future.metrics 中文".not_allowed(id integer)'),("user_function",'SELECT "future.metrics 中文".blocked_writer()')]:
            try:
                with psycopg.connect(make_conninfo(target_api,options="-c default_transaction_read_only=off")) as c:c.execute(statement)
            except psycopg.errors.InsufficientPrivilege:blocked=True
            else:blocked=False
            check("restored_"+name+"_denied_even_if_readonly_default_disabled",blocked)
        with psycopg.connect(source_api) as c:
            now_rows=c.execute('SELECT id,payload FROM "future.metrics 中文"."row\""dot" ORDER BY id').fetchall()
        check("source_remains_updated_without_restore_mutation",len(now_rows)==2 and now_rows[0][1]["value"]=="快照后的改值")
        # Corrupt only a copied private archive, never the original evidence.
        corrupt=archive_root/("corrupt-"+uuid4().hex);shutil.copytree(archive,corrupt)
        with (corrupt/"database.dump").open("ab") as f:f.write(b"independent-corruption")
        check("actual_dump_tamper_rejected_before_new_restore",denied(lambda:generic.restore(values,corrupt,container,include_objects=False),"DATABASE_CHECKSUM_MISMATCH"))
        link=archive_root/"own-symlink";link.symlink_to(archive,target_is_directory=True)
        check("archive_symlink_rejected",denied(lambda:generic.validate(link),"ARCHIVE_MUST_BE_UNDER_PRIVATE_LOCAL_BACKUPS"));link.unlink()
        class CatalogFixture:
            def __init__(self,kind,rls):self.calls=0;self.kind,self.rls=kind,rls
            def execute(self,*args):
                self.calls+=1
                rows=[("future.unanticipated",)] if self.calls==1 else [("future.unanticipated","literal\".table",self.kind,self.rls)] if self.calls==2 else [("vector","0.8.0","design")]
                return type("Rows",(),{"fetchall":lambda self:rows})()
        for kind,rls,code in [("f",False,"FOREIGN_DATA_REQUIRES_EXPLICIT_BACKUP_POLICY"),("r",True,"ROW_SECURITY_REQUIRES_EXPLICIT_BACKUP_POLICY")]:
            fake=CatalogFixture(kind,rls)
            check("catalog_"+kind+"_"+str(rls)+"_rejects_before_data_read",denied(lambda:generic.catalog(fake),code) and fake.calls==2)
        verify();result["passed"]=all(c["passed"] for c in result["checks"])
        result.update(restore_receipt=receipt,actual_relation_count=len(snapshot["tables"]),new_future_relation_count=len(new_tables),private_archive=str(archive),frozen_runtime=str(runtime),source_export_login=role[0])
    except Exception as error:
        result["fatal"]=dict(stage=stage,type=type(error).__name__,safe_code=str(error) if isinstance(error,run.BackupError) else None,message="No credentials, DSN or private dump data logged")
    result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),input_manifest_sha256=hashlib.sha256((root/"input-manifest.json").read_bytes()).hexdigest())
    (out/"executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(dict(passed=result["passed"],checks=len(result["checks"]),failed=[c["name"] for c in result["checks"] if not c["passed"]],fatal=result.get("fatal")))
    return 0 if result["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
