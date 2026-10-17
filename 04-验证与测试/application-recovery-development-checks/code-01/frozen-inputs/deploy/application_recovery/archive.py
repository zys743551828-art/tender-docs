"""Complete same-reader snapshot and exact object replay; old generic evidence is untouched."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from contextlib import contextmanager
from uuid import UUID

import psycopg

from deploy.application_recovery import cluster as clusters
from deploy.application_recovery import security as s
from deploy.backups import generic
from deploy.backups import run as safe


@contextmanager
def object_environment(values):
    previous = dict(os.environ)
    try:
        allowed = s.environment()
        os.environ.clear()
        os.environ.update(allowed)
        os.environ.update({k: v for k, v in values.items() if k in s.OBJECT_ENV})
        os.environ["WORKBENCH_ENV_FILE"] = "/nonexistent/application-recovery-no-default-env"
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def object_port(values):
    if values.get("OBJECT_STORE_BACKEND") != "oss" or not values.get("OBJECT_STORE_PREFIX"):
        raise s.RecoveryError("PREFIX_ISOLATED_NATIVE_OSS_REQUIRED")
    if not values.get("OBJECT_STORE_ENDPOINT", "").startswith("https://"):
        raise s.RecoveryError("HTTPS_OSS_REQUIRED")
    from packages.domain.providers.oss import OssClient

    with object_environment(values):
        return OssClient.from_env()


def detail(c):
    """Semantic definitions and ACLs are independent of new catalog OIDs/physical order."""
    queries = {
        "acl_role_dependencies": "SELECT DISTINCT r.rolname FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid WHERE d.dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND d.refclassid='pg_authid'::regclass AND d.deptype IN('a','o','r') ORDER BY 1",
        "schemas": "SELECT n.nspname,r.rolname,n.nspacl::text FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' ORDER BY 1",
        "relations": "SELECT n.nspname,c.relname,c.relkind,r.rolname,c.relacl::text,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' ORDER BY 1,2",
        "columns": "SELECT n.nspname,c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,a.attacl::text,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,a.attnum",
        "functions": "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),r.rolname,p.proacl::text,p.prosecdef,p.proconfig,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND p.prokind IN('f','p') ORDER BY 1,2,3",
        "constraints": "SELECT n.nspname,c.conname,coalesce(t.relname,''),coalesce(y.typname,''),pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace LEFT JOIN pg_class t ON t.oid=c.conrelid LEFT JOIN pg_type y ON y.oid=c.contypid WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' ORDER BY 1,2,3,4",
        "indexes": "SELECT n.nspname,c.relname,pg_get_indexdef(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='i' AND n.nspname !~ '^pg_' ORDER BY 1,2",
        "triggers": "SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname !~ '^pg_' ORDER BY 1,2,3",
        "types": "SELECT n.nspname,t.typname,t.typtype,r.rolname,t.typacl::text FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_roles r ON r.oid=t.typowner WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' ORDER BY 1,2",
        "extensions": "SELECT e.extname,e.extversion,n.nspname,r.rolname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace JOIN pg_roles r ON r.oid=e.extowner ORDER BY 1",
        "default_acl": "SELECT r.rolname,coalesce(n.nspname,''),d.defaclobjtype,d.defaclacl::text FROM pg_default_acl d JOIN pg_roles r ON r.oid=d.defaclrole LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace ORDER BY 1,2,3",
        "sequences": "SELECT schemaname,sequencename,sequenceowner,data_type,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname !~ '^pg_' ORDER BY 1,2",
        "blob_foreign_keys": "SELECT n.nspname,t.relname,c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE c.contype='f' AND c.confrelid='design.blobs'::regclass ORDER BY 1,2,3",
    }
    return json.loads(
        json.dumps(
            {key: c.execute(query).fetchall() for key, query in queries.items()}, default=str
        )
    )


def reader_guard(c, cluster):
    s.verify(c, cluster, "reader", restricted=True)
    groups = {
        r[0]
        for r in c.execute(
            "SELECT rolname FROM pg_roles WHERE oid<>(SELECT oid FROM pg_roles WHERE rolname=current_user) AND pg_has_role(current_user,oid,'MEMBER')"
        )
    }
    if groups != {cluster["backup_group"]}:
        raise s.RecoveryError("BACKUP_MEMBERSHIP_NOT_EXCLUSIVE")
    invalid = c.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN('r','p','m','v') AND (NOT has_table_privilege(current_user,c.oid,'SELECT') OR has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'))"
    ).fetchone()[0]
    if invalid:
        raise s.RecoveryError("BACKUP_READ_OR_WRITE_BOUNDARY_INVALID")
    if c.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND c.relkind='S' AND (NOT has_sequence_privilege(current_user,c.oid,'SELECT') OR has_sequence_privilege(current_user,c.oid,'USAGE,UPDATE'))"
    ).fetchone()[0]:
        raise s.RecoveryError("BACKUP_SEQUENCE_BOUNDARY_INVALID")
    executable = c.execute(
        "SELECT n.nspname,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND NOT EXISTS(SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') AND has_function_privilege(current_user,p.oid,'EXECUTE')"
    ).fetchall()
    if executable:
        raise s.RecoveryError("BACKUP_HAS_USER_FUNCTION_EXECUTE")
    if c.execute(
        "SELECT EXISTS(SELECT FROM pg_largeobject_metadata),EXISTS(SELECT FROM pg_foreign_table),EXISTS(SELECT FROM pg_subscription)"
    ).fetchone() != (False, False, False):
        raise s.RecoveryError("UNCONTROLLED_EXTERNAL_OR_LARGE_OBJECTS")
    if c.execute(
        "SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema' AND has_schema_privilege(current_user,oid,'CREATE')"
    ).fetchone()[0]:
        raise s.RecoveryError("BACKUP_HAS_SCHEMA_CREATE")


def snapshot(cluster, values, directory, *, after_snapshot=None, expected_definitions=None):
    clusters.assert_resources(cluster)
    directory.mkdir(mode=0o700, exist_ok=False)
    (directory / "objects").mkdir(mode=0o700)
    port = object_port(values)
    with psycopg.connect(s.connection_dsn(cluster, "reader")) as c:
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        reader_guard(c, cluster)
        exported = c.execute("SELECT pg_export_snapshot()").fetchone()
        if not exported:
            raise s.RecoveryError("SNAPSHOT_EXPORT_FAILED")
        snapshot_id = exported[0]
        catalog = generic.catalog(c)
        definitions = detail(c)
        if expected_definitions is None or definitions != expected_definitions:
            raise s.RecoveryError("SOURCE_INITIALIZATION_DEFINITIONS_CHANGED")
        tables = generic.inventory(c, catalog)
        queues = {
            "operations": c.execute(
                "SELECT state,count(*) FROM design.operations GROUP BY state ORDER BY state"
            ).fetchall(),
            "outbox": c.execute(
                "SELECT published_at IS NOT NULL,count(*) FROM design.outbox GROUP BY 1 ORDER BY 1"
            ).fetchall(),
            "model_calls": c.execute(
                "SELECT state,count(*) FROM design.model_calls GROUP BY state ORDER BY state"
            ).fetchall(),
            "uploads": c.execute(
                "SELECT state,count(*) FROM design.uploads GROUP BY state ORDER BY state"
            ).fetchall(),
            "staging_bytes_included": False,
            "staging_and_provider_upload_resume": "unqualified_no_remote_reinit_or_cleanup",
        }
        references = c.execute(
            "SELECT id::text,digest::text,object_key,byte_size,media_type FROM design.blobs ORDER BY id"
        ).fetchall()
        if sum(r[3] for r in references) > safe.MAX_ARCHIVE_BYTES:
            raise s.RecoveryError("OBJECT_ARCHIVE_BUDGET")
        if after_snapshot:
            after_snapshot()
        dump_path = directory / "database.dump"
        with dump_path.open("xb") as output:
            os.chmod(dump_path, 0o600)
            s.pg_tool(
                cluster,
                "pg_dump",
                ["--format=custom", "--snapshot=" + snapshot_id, "--lock-wait-timeout=10s"],
                stdout=output,
            )
        objects = []
        for identity, checksum, key, size, media in references:
            raw = safe.read_object(port, values["OBJECT_STORE_BUCKET"], key, size, checksum)
            filename = "objects/" + identity + ".bin"
            safe.private_write(directory / filename, raw)
            objects.append(
                {
                    "id": identity,
                    "sha256": checksum,
                    "key": key,
                    "bytes": size,
                    "media_type": media,
                    "file": filename,
                }
            )
    manifest = {
        "format": "application-recovery-archive/1",
        "source_database": cluster["database"],
        "source_system_identifier": cluster["system_identifier"],
        "source_login": cluster["accounts"]["reader"]["user"],
        "snapshot": snapshot_id,
        "isolation": "repeatable_read_read_only",
        "catalog": catalog,
        "definitions": definitions,
        "tables": tables,
        "objects": objects,
        "objects_included": True,
        "database_sha256": s.file_sha(dump_path),
        "object_store": safe.locator(values),
        "owner_label": cluster["owner_label"],
        "backup_group": cluster["backup_group"],
        "operational_state": "work_paused",
        "old_queue_resume_qualified": False,
        "queue_and_staging_inventory": queues,
    }
    s.save(directory / "manifest.json", manifest)
    safe.private_write(
        directory / "manifest.sha256", s.file_sha(directory / "manifest.json").encode()
    )
    return manifest


def validate(directory):
    manifest = s.load(directory / "manifest.json")
    if (
        s.file_sha(directory / "manifest.json")
        != s.private_path(directory / "manifest.sha256").read_text()
    ):
        raise s.RecoveryError("ARCHIVE_MANIFEST_CHANGED")
    if (
        manifest.get("format") != "application-recovery-archive/1"
        or manifest.get("objects_included") is not True
    ):
        raise s.RecoveryError("FULL_APPLICATION_ARCHIVE_REQUIRED")
    if s.file_sha(s.private_path(directory / "database.dump")) != manifest["database_sha256"]:
        raise s.RecoveryError("DATABASE_DUMP_CHANGED")
    if sum(o["bytes"] for o in manifest["objects"]) > safe.MAX_ARCHIVE_BYTES:
        raise s.RecoveryError("OBJECT_ARCHIVE_BUDGET")
    allowed = {*s.GROUPS, manifest["owner_label"], manifest["backup_group"], "pg_database_owner"}
    if any(row[0] not in allowed for row in manifest["definitions"]["acl_role_dependencies"]):
        raise s.RecoveryError("UNCONTROLLED_OWNER_OR_ACL_ROLE")
    for label in (manifest["owner_label"], manifest["backup_group"]):
        s.identifier(label)
    for _schema, owner, _ in manifest["definitions"]["schemas"]:
        if owner not in allowed:
            raise s.RecoveryError("UNCONTROLLED_SCHEMA_OWNER")
    for row in manifest["definitions"]["relations"]:
        if row[3] not in allowed:
            raise s.RecoveryError("UNCONTROLLED_RELATION_OWNER")
    for obj in manifest["objects"]:
        identity = str(UUID(obj["id"]))
        if (
            obj.get("file") != "objects/" + identity + ".bin"
            or not 0 <= obj["bytes"] <= safe.MAX_OBJECT_BYTES
        ):
            raise s.RecoveryError("OBJECT_FILE_IDENTITY_INVALID")
        path = s.private_path(directory / obj["file"])
        if path.stat().st_size != obj["bytes"] or s.file_sha(path) != obj["sha256"]:
            raise s.RecoveryError("OBJECT_FILE_CHANGED")
    return manifest


def restore(cluster, source_manifest, directory, values):
    manifest = validate(directory)
    if (
        source_manifest != manifest
        or cluster["system_identifier"] == manifest["source_system_identifier"]
    ):
        raise s.RecoveryError("RESTORE_SOURCE_TARGET_NOT_DISTINCT")
    clusters.assert_resources(cluster)
    if (cluster["owner_label"], cluster["backup_group"]) != (
        manifest["owner_label"],
        manifest["backup_group"],
    ):
        raise s.RecoveryError("RESTORE_ROLE_LABELS_MISMATCH")
    if (
        safe.locator(values) == manifest["object_store"]
        or values["OBJECT_STORE_PREFIX"] == manifest["object_store"]["prefix"]
    ):
        raise s.RecoveryError("RESTORE_PREFIX_MUST_BE_DISTINCT")
    with psycopg.connect(s.connection_dsn(cluster), autocommit=True) as c:
        s.verify(c, cluster)
        if c.execute("SELECT to_regnamespace('design')").fetchone() != (None,):
            raise s.RecoveryError("REFUSING_EXISTING_RESTORE_SCHEMA")
    with (directory / "database.dump").open("rb") as source:
        s.pg_tool(
            cluster,
            "pg_restore",
            ["--dbname=" + cluster["database"], "--exit-on-error", "--single-transaction"],
            stdin=source,
            stdout=subprocess.DEVNULL,
            role="owner",
        )
    with psycopg.connect(s.connection_dsn(cluster), autocommit=True) as c:
        s.verify(c, cluster)
        clusters.reassign(c, cluster)
        clusters.accounts(c, cluster)
    with psycopg.connect(s.connection_dsn(cluster, "reader")) as c:
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        reader_guard(c, cluster)
        if (
            generic.catalog(c) != manifest["catalog"]
            or generic.inventory(c) != manifest["tables"]
            or detail(c) != manifest["definitions"]
        ):
            raise s.RecoveryError("RESTORED_CATALOG_DATA_ACL_OR_DEFINITION_MISMATCH")
    port = object_port(values)
    for obj in manifest["objects"]:
        port.put_object(
            Bucket=values["OBJECT_STORE_BUCKET"],
            Key=obj["key"],
            Body=(directory / obj["file"]).read_bytes(),
            ContentType=obj["media_type"],
        )
        safe.read_object(
            port, values["OBJECT_STORE_BUCKET"], obj["key"], obj["bytes"], obj["sha256"]
        )
    receipt = {
        "catalog_data_and_acl_verified": True,
        "tables": len(manifest["tables"]),
        "objects": len(manifest["objects"]),
        "object_bytes": sum(o["bytes"] for o in manifest["objects"]),
        "target_database": cluster["database"],
        "state": "work_paused",
        "old_queue_resume_qualified": False,
        "production_rpo_rto_qualified": False,
    }
    s.save(directory.parent / "restore-verified.json", receipt)
    return receipt
