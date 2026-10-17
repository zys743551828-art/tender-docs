"""Manual snapshot backup and isolated restore drill; never an in-place restore."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex

# Local operator invokes pinned PG tools through Docker without a shell.
import subprocess  # nosec B404
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ("design", "retrieval")
MAX_OBJECT_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
ARCHIVE_ROOT = ROOT / ".local/backups"


class BackupError(Exception):
    """Only fixed, credential-free messages cross the CLI boundary."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def private_write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def configuration(path: Path) -> dict[str, str]:
    if path.stat().st_mode & 0o077:
        raise BackupError("PROFILE_PERMISSIONS: expected private mode 600")
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            parts = shlex.split(value, comments=True)
            if len(parts) > 1:
                raise BackupError("PROFILE_INVALID")
            result[key.strip()] = parts[0] if parts else ""
    return result


def locator(values: dict[str, str]) -> dict[str, str]:
    return {
        key.removeprefix("OBJECT_STORE_").lower(): values.get(key, "")
        for key in (
            "OBJECT_STORE_BACKEND",
            "OBJECT_STORE_ENDPOINT",
            "OBJECT_STORE_REGION",
            "OBJECT_STORE_BUCKET",
            "OBJECT_STORE_PREFIX",
        )
    }


def pg_tool(container: str, tool: str, dsn: str, args: list[str], **kwargs):
    if tool not in ("pg_dump", "pg_restore"):
        raise BackupError("POSTGRES_TOOL_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
        raise BackupError("CONTAINER_INVALID")
    fields = conninfo_to_dict(dsn)
    child = os.environ.copy()
    child.update(
        PGPASSWORD=str(fields.get("password", "")),
        PGUSER=str(fields["user"]),
        PGDATABASE=str(fields["dbname"]),
    )
    command = [
        "docker",
        "exec",
        "-i",
        "-e",
        "PGPASSWORD",
        "-e",
        "PGUSER",
        "-e",
        "PGDATABASE",
        "-e",
        "PGHOST=127.0.0.1",
        "-e",
        "PGPORT=5432",
        container,
        tool,
        *args,
    ]
    # Validated local tool/container and structured arguments; password only in environment.
    result = subprocess.run(  # nosec B603
        command, env=child, stderr=subprocess.PIPE, timeout=180, check=False, **kwargs
    )
    if result.returncode:
        raise BackupError("POSTGRES_TOOL_FAILED: details suppressed to protect private data")
    return result


def restricted(connection) -> str:
    user, superuser, bypass, owns = connection.execute(
        "SELECT current_user, rolsuper, rolbypassrls, EXISTS(SELECT 1 FROM pg_class "
        "WHERE relnamespace IN ('design'::regnamespace,'retrieval'::regnamespace) "
        "AND relowner=r.oid) FROM pg_roles r WHERE rolname=current_user"
    ).fetchone()
    if superuser or bypass or owns:
        raise BackupError("SOURCE_LOGIN_MUST_BE_NONOWNER")
    return user


def inventory(connection) -> dict:
    tables = connection.execute(
        "SELECT schemaname,tablename FROM pg_tables WHERE schemaname=ANY(%s) ORDER BY 1,2",
        (list(SCHEMAS),),
    ).fetchall()
    result = {}
    for schema, table in tables:
        hasher, count = hashlib.sha256(), 0
        # Stable physical-independent row representation, captured in the exported snapshot.
        with connection.cursor(name="backup_inventory_" + uuid4().hex) as cursor:
            cursor.execute(
                sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t ORDER BY 1").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            for (row,) in cursor:
                encoded = row.encode()
                hasher.update(len(encoded).to_bytes(8, "big"))
                hasher.update(encoded)
                count += 1
        result[f"{schema}.{table}"] = {"rows": count, "sha256": hasher.hexdigest()}
    return result


def read_object(port, bucket: str, key: str, size: int, expected: str) -> bytes:
    if size < 0 or size > MAX_OBJECT_BYTES:
        raise BackupError("OBJECT_LIMIT_EXCEEDED")
    response = port.get_object(Bucket=bucket, Key=key)
    try:
        payload = response["Body"].read(size + 1)
    finally:
        response["Body"].close()
    if len(payload) != size or digest(payload) != expected:
        raise BackupError("OBJECT_CHECKSUM_MISMATCH")
    return payload


def private_archive(path: Path, *, existing: bool) -> None:
    root = ARCHIVE_ROOT.resolve()
    if not path.resolve().is_relative_to(root) or path.resolve() == root or path.is_symlink():
        raise BackupError("ARCHIVE_MUST_BE_UNDER_PRIVATE_LOCAL_BACKUPS")
    if existing and (not path.is_dir() or path.stat().st_mode & 0o077):
        raise BackupError("ARCHIVE_PERMISSIONS")
    for parent in (root, *path.resolve().parents):
        if parent == root.parent:
            break
        if parent.exists() and parent.stat().st_mode & 0o077:
            raise BackupError("ARCHIVE_PARENT_PERMISSIONS")


def create(values: dict, output: Path, container: str, after_snapshot=None) -> dict:
    from packages.domain import storage

    private_archive(output, existing=False)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / "objects").mkdir(mode=0o700)
    port = storage.client()
    with psycopg.connect(values["API_DATABASE_DSN"]) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        user = restricted(connection)
        database, version, snapshot = connection.execute(
            "SELECT current_database(), current_setting('server_version'), pg_export_snapshot()"
        ).fetchone() or (None, None, None)
        if after_snapshot is not None:
            after_snapshot()
        tables = inventory(connection)
        blobs = connection.execute(
            "SELECT id::text,digest::text,object_key,byte_size,media_type "
            "FROM design.blobs ORDER BY id"
        ).fetchall()
        if sum(row[3] for row in blobs) > MAX_ARCHIVE_BYTES:
            raise BackupError("ARCHIVE_OBJECT_BUDGET_EXCEEDED")
        with (output / "database.dump").open("xb") as dump:
            os.chmod(dump.name, 0o600)
            pg_tool(
                container,
                "pg_dump",
                values["API_DATABASE_DSN"],
                [
                    "--format=custom",
                    "--snapshot=" + snapshot,
                    "--lock-wait-timeout=10s",
                ],
                stdout=dump,
            )
        objects = []
        for blob_id, checksum, key, size, media in blobs:
            payload = read_object(port, storage.bucket(), key, size, checksum)
            filename = f"objects/{blob_id}.bin"
            private_write(output / filename, payload)
            objects.append(
                {
                    "id": blob_id,
                    "sha256": checksum,
                    "key": key,
                    "bytes": size,
                    "media_type": media,
                    "file": filename,
                }
            )
    manifest = {
        "format": "tender-snapshot-backup-1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "host": conninfo_to_dict(values["API_DATABASE_DSN"]).get("host", ""),
            "port": conninfo_to_dict(values["API_DATABASE_DSN"]).get("port", "5432"),
            "database": database,
            "login": user,
            "server_version": version,
            "snapshot": snapshot,
            "isolation": "repeatable_read_read_only",
        },
        "object_store": locator(values),
        "tables": tables,
        "objects": objects,
        "database_sha256": digest((output / "database.dump").read_bytes()),
        "limits": {"max_object_bytes": MAX_OBJECT_BYTES},
    }
    private_write(
        output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    )
    private_write(
        output / "manifest.sha256", digest((output / "manifest.json").read_bytes()).encode()
    )
    return manifest


def load_archive(archive: Path) -> dict:
    private_archive(archive, existing=True)
    for filename in ("manifest.json", "manifest.sha256", "database.dump", "objects"):
        path = archive / filename
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise BackupError("ARCHIVE_FILE_PERMISSIONS")
    raw = (archive / "manifest.json").read_bytes()
    if digest(raw) != (archive / "manifest.sha256").read_text():
        raise BackupError("MANIFEST_CHECKSUM_MISMATCH")
    manifest = json.loads(raw)
    if manifest.get("format") != "tender-snapshot-backup-1":
        raise BackupError("MANIFEST_FORMAT")
    if digest((archive / "database.dump").read_bytes()) != manifest["database_sha256"]:
        raise BackupError("DATABASE_CHECKSUM_MISMATCH")
    seen = set()
    for item in manifest["objects"]:
        expected = f"objects/{item['id']}.bin"
        if not re.fullmatch(r"objects/[0-9a-f-]{36}\.bin", expected) or item["file"] != expected:
            raise BackupError("OBJECT_PATH_INVALID")
        path = archive / expected
        if path.is_symlink() or expected in seen:
            raise BackupError("OBJECT_PATH_INVALID")
        seen.add(expected)
        if path.stat().st_mode & 0o077 or not 0 <= item["bytes"] <= MAX_OBJECT_BYTES:
            raise BackupError("ARCHIVE_OBJECT_LIMIT_OR_PERMISSIONS")
        if path.stat().st_size != item["bytes"]:
            raise BackupError("ARCHIVE_OBJECT_CHECKSUM_MISMATCH")
        data = path.read_bytes()
        if len(data) != item["bytes"] or digest(data) != item["sha256"]:
            raise BackupError("ARCHIVE_OBJECT_CHECKSUM_MISMATCH")
    return manifest


def restore_verify(values: dict, archive: Path, container: str) -> dict:
    from packages.domain import storage

    manifest = load_archive(archive)
    if values.get("OBJECT_STORE_BACKEND") != "oss":
        raise BackupError("RESTORE_REQUIRES_PREFIX_ISOLATED_OSS")
    # Restores never accept a caller-selected existing DB or object namespace.
    suffix = uuid4().hex
    database = "tender_restore_" + suffix
    restored_prefix = values.get("OBJECT_STORE_PREFIX", "") + "restore-drills/" + suffix + "/"
    source_owner = values["OWNER_DATABASE_DSN"]
    owner_dsn = make_conninfo(source_owner, dbname=database)
    api_dsn = make_conninfo(
        values["API_DATABASE_DSN"], dbname=database, options="-c default_transaction_read_only=on"
    )
    with psycopg.connect(source_owner, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database))
        )
    with psycopg.connect(owner_dsn, autocommit=True) as bootstrap:
        bootstrap.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    with (archive / "database.dump").open("rb") as dump:
        pg_tool(
            container,
            "pg_restore",
            owner_dsn,
            ["--dbname=" + database, "--no-owner", "--exit-on-error", "--single-transaction"],
            stdin=dump,
            stdout=subprocess.DEVNULL,
        )
    with psycopg.connect(owner_dsn, autocommit=True) as bootstrap:
        # Only the newly created offline drill DB is hardened. Source privileges are untouched.
        roles = sql.SQL(",").join(
            sql.Identifier(r)
            for r in (
                "tender_api",
                "tender_worker",
                "tender_score_read",
                "tender_research_read",
                "tender_research_worker",
            )
        )
        bootstrap.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA design,retrieval FROM {}").format(roles)
        )
        bootstrap.execute(
            sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA design,retrieval FROM PUBLIC,{}").format(
                roles
            )
        )
        bootstrap.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA design,retrieval,public FROM PUBLIC,{}").format(roles)
        )
        bootstrap.execute("GRANT SELECT ON ALL TABLES IN SCHEMA design,retrieval TO tender_api")
        bootstrap.execute(
            sql.SQL("ALTER DATABASE {} SET default_transaction_read_only=on").format(
                sql.Identifier(database)
            )
        )
    with psycopg.connect(api_dsn) as restored:
        restored.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        login = restricted(restored)
        if inventory(restored) != manifest["tables"]:
            raise BackupError("RESTORED_DATABASE_IDENTITY_MISMATCH")
        # Compare DB references as well as local files: not merely a local archive checksum.
        actual = restored.execute(
            "SELECT id::text,digest::text,object_key,byte_size,media_type FROM design.blobs ORDER BY id"
        ).fetchall()
        expected = [
            (o["id"], o["sha256"], o["key"], o["bytes"], o["media_type"])
            for o in manifest["objects"]
        ]
        if actual != expected:
            raise BackupError("RESTORED_BLOB_REFERENCES_MISMATCH")
        read_only_row = restored.execute("SHOW transaction_read_only").fetchone()
        if not read_only_row:
            raise BackupError("RESTORE_READ_ONLY_CHECK_FAILED")
        read_only = read_only_row[0]
    # Read-only is enforced by table/function privileges as well as a default setting.
    write_probe_blocked = False
    try:
        with psycopg.connect(
            make_conninfo(api_dsn, options="-c default_transaction_read_only=off")
        ) as probe:
            probe.execute(
                "INSERT INTO design.members(id,display_name) VALUES(%s,%s)",
                (uuid4(), "Synthetic restore write prohibition check"),
            )
            raise BackupError("RESTORE_WRITE_PRIVILEGE_UNEXPECTED")
    except psycopg.errors.InsufficientPrivilege:
        write_probe_blocked = True
    original_prefix = os.environ.get("OBJECT_STORE_PREFIX")
    try:
        os.environ["OBJECT_STORE_PREFIX"] = restored_prefix
        port = storage.client()
        for item in manifest["objects"]:
            port.put_object(
                Bucket=storage.bucket(),
                Key=item["key"],
                Body=(archive / item["file"]).read_bytes(),
                ContentType=item["media_type"],
            )
            read_object(port, storage.bucket(), item["key"], item["bytes"], item["sha256"])
    finally:
        if original_prefix is None:
            os.environ.pop("OBJECT_STORE_PREFIX", None)
        else:
            os.environ["OBJECT_STORE_PREFIX"] = original_prefix
    receipt = {
        "status": "passed_isolated_development_restore",
        "database": database,
        "nonowner_login": login,
        "transaction_read_only": read_only,
        "api_write_probe_denied_with_read_only_setting_disabled": write_probe_blocked,
        "restored_object_prefix": restored_prefix,
        "objects_verified": len(manifest["objects"]),
        "tables_verified": len(manifest["tables"]),
        "archive_manifest_sha256": digest((archive / "manifest.json").read_bytes()),
        "no_consumer_started": True,
        "production_rpo_rto_qualified": False,
        "retained_for_read_only_inspection": True,
    }
    private_write(archive / ("restore-" + suffix + ".json"), json.dumps(receipt, indent=2).encode())
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify", "restore-verify"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--container", default="tender-workbench-external-postgres-1")
    args = parser.parse_args()
    try:
        values = configuration(args.env_file)
        os.environ.update(values)
        os.environ["WORKBENCH_ENV_FILE"] = str(args.env_file)
        if args.action == "create":
            manifest = create(values, args.archive, args.container)
            result = {
                "status": "created",
                "tables": len(manifest["tables"]),
                "objects": len(manifest["objects"]),
            }
        elif args.action == "verify":
            manifest = load_archive(args.archive)
            result = {"status": "archive_bytes_verified", "objects": len(manifest["objects"])}
        else:
            result = restore_verify(values, args.archive, args.container)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Never render driver errors: they can include private SQL/data, DSNs or signed URLs.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": str(exc) if isinstance(exc, BackupError) else type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
