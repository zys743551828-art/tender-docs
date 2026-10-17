"""Catalog-driven backup successor; old two-schema evidence remains frozen and unmodified."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from deploy.backups import run as safe

FORMAT = "tender-catalog-backup/v2"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
ROLES = (
    "tender_api",
    "tender_worker",
    "tender_score_read",
    "tender_research_read",
    "tender_research_worker",
)


def hash_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def catalog(c):
    schemas = [
        row[0]
        for row in c.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname<>'information_schema' AND nspname !~ '^pg_' ORDER BY nspname"
        ).fetchall()
    ]
    relations = c.execute(
        "SELECT n.nspname,c.relname,c.relkind,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind IN('r','p','m','f') ORDER BY 1,2",
        (schemas,),
    ).fetchall()
    if any(row[2] == "f" for row in relations):
        raise safe.BackupError("FOREIGN_DATA_REQUIRES_EXPLICIT_BACKUP_POLICY")
    if any(row[3] for row in relations):
        raise safe.BackupError("ROW_SECURITY_REQUIRES_EXPLICIT_BACKUP_POLICY")
    extensions = c.execute(
        "SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1"
    ).fetchall()
    return {
        "schemas": schemas,
        "relations": [{"schema": s, "name": n, "kind": k} for s, n, k, _ in relations],
        "extensions": [{"name": n, "version": v, "schema": s} for n, v, s in extensions],
    }


def restricted(c):
    row = c.execute(
        "SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if not row or any(row[1:]):
        raise safe.BackupError("SOURCE_LOGIN_MUST_BE_RESTRICTED_NONOWNER")
    owns = c.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_' AND pg_has_role(current_user,c.relowner,'USAGE'))"
    ).fetchone()[0]
    if owns:
        raise safe.BackupError("SOURCE_LOGIN_MUST_BE_RESTRICTED_NONOWNER")
    return row[0]


def inventory(c, description=None):
    description = description or catalog(c)
    result = []
    for relation in description["relations"]:
        hasher, count = hashlib.sha256(), 0
        with c.cursor(name="catalog_backup_" + uuid4().hex) as cursor:
            cursor.execute(
                sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t ORDER BY 1").format(
                    sql.Identifier(relation["schema"]), sql.Identifier(relation["name"])
                )
            )
            for (row,) in cursor:
                encoded = row.encode()
                hasher.update(len(encoded).to_bytes(8, "big"))
                hasher.update(encoded)
                count += 1
        result.append({**relation, "rows": count, "sha256": hasher.hexdigest()})
    return result


def snapshot(values, output, container, *, include_objects=False, after_snapshot=None):
    if include_objects and values.get("OBJECT_STORE_BACKEND") != "oss":
        raise safe.BackupError("FULL_RESTORE_REQUIRES_PREFIX_ISOLATED_OSS")
    safe.private_archive(output, existing=False)
    output.mkdir(mode=0o700)
    (output / "objects").mkdir(mode=0o700)
    with psycopg.connect(values["API_DATABASE_DSN"]) as c:
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        login = restricted(c)
        exported = c.execute(
            "SELECT pg_export_snapshot(),current_database(),current_setting('server_version'),clock_timestamp()"
        ).fetchone()
        if not exported:
            raise safe.BackupError("SNAPSHOT_EXPORT_FAILED")
        snap, database, version, at = exported
        if after_snapshot:
            after_snapshot()
        description = catalog(c)
        tables = inventory(c, description)
        references = c.execute(
            "SELECT id::text,digest::text,object_key,byte_size,media_type FROM design.blobs ORDER BY id"
        ).fetchall()
        if sum(r[3] for r in references) > safe.MAX_ARCHIVE_BYTES:
            raise safe.BackupError("ARCHIVE_OBJECT_BUDGET_EXCEEDED")
        with (output / "database.dump").open("xb") as dump:
            os.chmod(dump.name, 0o600)
            safe.pg_tool(
                container,
                "pg_dump",
                values["API_DATABASE_DSN"],
                ["--format=custom", "--snapshot=" + snap, "--lock-wait-timeout=10s"],
                stdout=dump,
            )
        objects = [
            {"id": i, "sha256": h, "key": k, "bytes": b, "media_type": m}
            for i, h, k, b, m in references
        ]
        if include_objects:
            from packages.domain import storage

            port = storage.client()
            for item in objects:
                content = safe.read_object(
                    port, storage.bucket(), item["key"], item["bytes"], item["sha256"]
                )
                item["file"] = "objects/" + item["id"] + ".bin"
                safe.private_write(output / item["file"], content)
    manifest = {
        "format": FORMAT,
        "source": {
            "database": database,
            "login": login,
            "server_version": version,
            "snapshot": snap,
            "captured_at": at.isoformat(),
            "isolation": "repeatable_read_read_only",
        },
        "catalog": description,
        "tables": tables,
        "objects": objects,
        "objects_included": include_objects,
        "object_store": safe.locator(values),
        "database_sha256": hash_file(output / "database.dump"),
    }
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise safe.BackupError("MANIFEST_BUDGET_EXCEEDED")
    safe.private_write(output / "manifest.json", raw)
    safe.private_write(output / "manifest.sha256", safe.digest(raw).encode())
    return manifest


def validate(archive, *, require_objects=False):
    safe.private_archive(archive, existing=True)
    for name in ("manifest.json", "manifest.sha256", "database.dump", "objects"):
        path = archive / name
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise safe.BackupError("ARCHIVE_FILE_PERMISSIONS")
    if (archive / "manifest.json").stat().st_size > MAX_MANIFEST_BYTES:
        raise safe.BackupError("MANIFEST_BUDGET_EXCEEDED")
    raw = (archive / "manifest.json").read_bytes()
    if safe.digest(raw) != (archive / "manifest.sha256").read_text():
        raise safe.BackupError("MANIFEST_CHECKSUM_MISMATCH")
    manifest = json.loads(raw)
    if manifest.get("format") != FORMAT:
        raise safe.BackupError("MANIFEST_FORMAT")
    if hash_file(archive / "database.dump") != manifest["database_sha256"]:
        raise safe.BackupError("DATABASE_CHECKSUM_MISMATCH")
    if require_objects and manifest.get("objects_included") is not True:
        raise safe.BackupError("DATABASE_COMPONENT_HAS_NO_OBJECT_BYTES")
    if sum(item["bytes"] for item in manifest["objects"]) > safe.MAX_ARCHIVE_BYTES:
        raise safe.BackupError("ARCHIVE_OBJECT_BUDGET_EXCEEDED")
    if manifest["objects_included"]:
        for item in manifest["objects"]:
            if not 0 <= item["bytes"] <= safe.MAX_OBJECT_BYTES:
                raise safe.BackupError("OBJECT_LIMIT_EXCEEDED")
            identity = str(UUID(item["id"]))
            expected = "objects/" + identity + ".bin"
            if item.get("file") != expected:
                raise safe.BackupError("OBJECT_PATH_INVALID")
            path = archive / expected
            if (
                path.is_symlink()
                or path.stat().st_mode & 0o077
                or path.stat().st_size != item["bytes"]
                or hash_file(path) != item["sha256"]
            ):
                raise safe.BackupError("ARCHIVE_OBJECT_CHECKSUM_MISMATCH")
    return manifest


def _harden_new_database(c, description, database, api_login):
    # This function is only called with a freshly generated database created by this attempt.
    effective = c.execute(
        "SELECT rolname FROM pg_roles WHERE pg_has_role(%s,oid,'USAGE')", (api_login,)
    ).fetchall()
    role_names = sql.SQL(",").join(
        map(sql.Identifier, sorted(set(ROLES) | {row[0] for row in effective} | {api_login}))
    )
    for schema in description["schemas"]:
        namespace = sql.Identifier(schema)
        c.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM PUBLIC,{}").format(
                namespace, role_names
            )
        )
        c.execute(
            sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC,{}").format(
                namespace, role_names
            )
        )
        c.execute(
            sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM PUBLIC,{}").format(
                namespace, role_names
            )
        )
        c.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC,{}").format(namespace, role_names))
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO tender_api").format(namespace))
        c.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO tender_api").format(namespace)
        )
    c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    c.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(api_login)
        )
    )
    c.execute(
        sql.SQL("ALTER DATABASE {} SET default_transaction_read_only=on").format(
            sql.Identifier(database)
        )
    )


def restore(values, archive, container, *, include_objects=False):
    manifest = validate(archive, require_objects=include_objects)
    if include_objects and values.get("OBJECT_STORE_BACKEND") != "oss":
        raise safe.BackupError("FULL_RESTORE_REQUIRES_PREFIX_ISOLATED_OSS")
    suffix = uuid4().hex
    database = "tender_catalog_restore_" + suffix
    owner = values["OWNER_DATABASE_DSN"]
    owner_fields = conninfo_to_dict(owner)
    api_fields = conninfo_to_dict(values["API_DATABASE_DSN"])
    if any(
        str(owner_fields.get(key, default)) != str(api_fields.get(key, default))
        for key, default in (("host", ""), ("port", "5432"))
    ):
        raise safe.BackupError("RESTORE_OWNER_API_INSTANCE_MISMATCH")

    api_login = str(conninfo_to_dict(values["API_DATABASE_DSN"])["user"])
    target_owner = make_conninfo(owner, dbname=database)
    target_api = make_conninfo(
        values["API_DATABASE_DSN"], dbname=database, options="-c default_transaction_read_only=on"
    )
    with psycopg.connect(owner, autocommit=True) as bootstrap:
        bootstrap.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database))
        )
        bootstrap.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
    safe.private_write(
        archive / ("attempt-" + suffix + ".json"),
        json.dumps(
            {
                "database": database,
                "state": "new_offline_restore_database_created",
                "source_untouched": True,
            },
            indent=2,
        ).encode(),
    )
    with (archive / "database.dump").open("rb") as dump:
        safe.pg_tool(
            container,
            "pg_restore",
            target_owner,
            ["--dbname=" + database, "--no-owner", "--exit-on-error", "--single-transaction"],
            stdin=dump,
            stdout=subprocess.DEVNULL,
        )
    with psycopg.connect(target_owner, autocommit=True) as bootstrap:
        restored_catalog = catalog(bootstrap)
        if restored_catalog != manifest["catalog"]:
            raise safe.BackupError("RESTORED_CATALOG_MISMATCH")
        _harden_new_database(bootstrap, restored_catalog, database, api_login)
    with psycopg.connect(target_api) as verifier:
        verifier.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        login = restricted(verifier)
        if catalog(verifier) != manifest["catalog"] or inventory(verifier) != manifest["tables"]:
            raise safe.BackupError("RESTORED_DATABASE_IDENTITY_MISMATCH")
        actual = verifier.execute(
            "SELECT id::text,digest::text,object_key,byte_size,media_type FROM design.blobs ORDER BY id"
        ).fetchall()
        expected = [
            (o["id"], o["sha256"], o["key"], o["bytes"], o["media_type"])
            for o in manifest["objects"]
        ]
        if actual != expected:
            raise safe.BackupError("RESTORED_BLOB_REFERENCES_MISMATCH")
    # Verify absence of table-write and user-function privileges across every discovered schema.
    with psycopg.connect(
        make_conninfo(target_api, options="-c default_transaction_read_only=off")
    ) as verifier:
        writable_row = verifier.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND c.relkind IN('r','p','m') AND (has_table_privilege(current_user,c.oid,'INSERT') OR has_table_privilege(current_user,c.oid,'UPDATE') OR has_table_privilege(current_user,c.oid,'DELETE') OR has_table_privilege(current_user,c.oid,'TRUNCATE'))",
            (manifest["catalog"]["schemas"],),
        ).fetchone()
        executable_row = verifier.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY(%s) AND has_function_privilege(current_user,p.oid,'EXECUTE')",
            (manifest["catalog"]["schemas"],),
        ).fetchone()
        if not writable_row or not executable_row:
            raise safe.BackupError("RESTORED_PRIVILEGE_CHECK_FAILED")
        if writable_row[0] or executable_row[0]:
            raise safe.BackupError("RESTORED_READ_ONLY_PRIVILEGES_INVALID")
    prefix = None
    if include_objects:
        from packages.domain import storage

        prefix = values.get("OBJECT_STORE_PREFIX", "") + "restore-drills/" + suffix + "/"
        original = os.environ.get("OBJECT_STORE_PREFIX")
        try:
            os.environ["OBJECT_STORE_PREFIX"] = prefix
            port = storage.client()
            for item in manifest["objects"]:
                port.put_object(
                    Bucket=storage.bucket(),
                    Key=item["key"],
                    Body=(archive / item["file"]).read_bytes(),
                    ContentType=item["media_type"],
                )
                safe.read_object(port, storage.bucket(), item["key"], item["bytes"], item["sha256"])
        finally:
            if original is None:
                os.environ.pop("OBJECT_STORE_PREFIX", None)
            else:
                os.environ["OBJECT_STORE_PREFIX"] = original
    receipt = {
        "status": "passed_full_development_restore"
        if include_objects
        else "passed_database_component_only",
        "database": database,
        "nonowner_login": login,
        "schemas_verified": manifest["catalog"]["schemas"],
        "tables_verified": len(manifest["tables"]),
        "catalog_extensions_verified": manifest["catalog"]["extensions"],
        "all_discovered_schema_write_privileges_revoked": True,
        "all_discovered_schema_user_function_execution_revoked": True,
        "objects_verified": len(manifest["objects"]) if include_objects else 0,
        "object_references_verified": len(manifest["objects"]),
        "object_bytes_included": manifest["objects_included"],
        "restored_object_prefix": prefix,
        "full_application_restore_qualified": False,
        "production_rpo_rto_qualified": False,
        "no_consumer_started": True,
        "archive_manifest_sha256": safe.digest((archive / "manifest.json").read_bytes()),
    }
    safe.private_write(
        archive / ("restore-" + suffix + ".json"), json.dumps(receipt, indent=2).encode()
    )
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("database-snapshot", "create", "verify", "restore-database", "restore-verify"),
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--container", default="tender-workbench-external-postgres-1")
    args = parser.parse_args()
    try:
        values = safe.configuration(args.env_file)
        os.environ.update(values)
        os.environ["WORKBENCH_ENV_FILE"] = str(args.env_file)
        if args.action in {"database-snapshot", "create"}:
            manifest = snapshot(
                values, args.archive, args.container, include_objects=args.action == "create"
            )
            result = {
                "status": "archive_created"
                if args.action == "create"
                else "database_component_created",
                "tables": len(manifest["tables"]),
                "schemas": manifest["catalog"]["schemas"],
                "objects_included": manifest["objects_included"],
            }
        elif args.action == "verify":
            manifest = validate(args.archive)
            result = {
                "status": "local_archive_bytes_verified",
                "objects_included": manifest["objects_included"],
            }
        else:
            result = restore(
                values,
                args.archive,
                args.container,
                include_objects=args.action == "restore-verify",
            )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": str(error)
                    if isinstance(error, safe.BackupError)
                    else type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
