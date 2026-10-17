"""One-shot resources on a newly created dedicated Docker PostgreSQL cluster only."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from deploy.application_recovery import binding
from deploy.application_recovery import security as s


def validate_inputs(baseline):
    if s.file_sha(baseline) != "76dc6abed9ae344dbf755a3c0d237e68f3a420b042fac6193debe2e16116f38f":
        raise s.RecoveryError("UNREVIEWED_BASELINE_IDENTITY")
    doc = json.loads(Path(baseline).read_text())
    if doc.get("format") != "tender-application-recovery-protocol/v2":
        raise s.RecoveryError("REVIEWED_PROTOCOL_02_REQUIRED")
    backend = doc["backend"]
    if s.file_sha(backend["manifest"]) != backend["sha256"]:
        raise s.RecoveryError("BACKEND_MANIFEST_CHANGED")
    inputs = json.loads(Path(backend["manifest"]).read_text())
    if len(inputs) != backend["file_count"]:
        raise s.RecoveryError("BACKEND_FILE_COUNT_CHANGED")
    for item in inputs:
        if s.file_sha(Path(backend["source_root"]) / item["path"]) != item["sha256"]:
            raise s.RecoveryError("BACKEND_SOURCE_CHANGED")
    frozen = []
    for item in doc["ordered_sql"]:
        raw = Path(item["frozen_input"]).read_bytes()
        if s.sha(raw) != item["sha256"]:
            raise s.RecoveryError("REVIEWED_SQL_CHANGED")
        frozen.append(raw.decode())
    return doc, frozen


def plan(directory, baseline, source_port, target_port):
    s.budget()
    s.PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = s.private_path(directory, exists=False)
    directory.mkdir(mode=0o700, exist_ok=False)
    doc, _ = validate_inputs(baseline)
    if source_port == target_port:
        raise s.RecoveryError("SOURCE_TARGET_PORT_COLLISION")
    for port in (source_port, target_port):
        if not 1024 < port < 65536:
            raise s.RecoveryError("LOOPBACK_PORT_REQUIRED")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    result = {
        "format": "application-recovery-plan/1",
        "nonce": uuid4().hex,
        "baseline": str(Path(baseline).resolve()),
        "baseline_sha256": s.file_sha(baseline),
        "source_port": source_port,
        "target_port": target_port,
        "backend": doc["backend"],
        "state": "work_paused",
        "disk_budget": {
            "minimum_initial_free_bytes": 1600 * 1024 * 1024,
            "minimum_before_each_cluster_bytes": 900 * 1024 * 1024,
            "two_container_copy_estimate_bytes": 900 * 1024 * 1024,
            "two_small_pg_volumes_estimate_bytes": 320 * 1024 * 1024,
            "archive_estimate_bytes": 32 * 1024 * 1024,
            "reserve_estimate_bytes": 256 * 1024 * 1024,
            "download_allowed": False,
        },
    }
    s.save(directory / "plan.json", result)
    return result


def assert_resources(cluster):
    UUID(cluster["resource_id"])
    description = json.loads(s.docker(["inspect", cluster["container"]]).stdout)[0]
    if (
        description["Id"] != cluster["container_id"]
        or description["Config"]["Labels"].get("tender.application_recovery")
        != cluster["resource_id"]
        or description["Config"]["Image"] != s.PG_IMAGE
        or description["HostConfig"]["PortBindings"]
        != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(cluster["port"])}]}
    ):
        raise s.RecoveryError("DOCKER_RESOURCE_IDENTITY_MISMATCH")
    volumes = json.loads(s.docker(["volume", "inspect", cluster["volume"]]).stdout)
    if volumes[0]["Labels"].get("tender.application_recovery") != cluster["resource_id"]:
        raise s.RecoveryError("DOCKER_VOLUME_IDENTITY_MISMATCH")
    mounts = [m for m in description["Mounts"] if m["Destination"] == "/var/lib/postgresql/data"]
    if len(mounts) != 1 or mounts[0].get("Name") != cluster["volume"]:
        raise s.RecoveryError("DOCKER_DATA_MOUNT_MISMATCH")


def create(directory, plan_doc, side, *, owner_label=None, backup_group=None):
    if side not in {"source", "target"} or (directory / (side + ".json")).exists():
        raise s.RecoveryError("REFUSING_EXISTING_CLUSTER_RECEIPT")
    binding.require()
    s.budget(900 * 1024 * 1024)
    token = uuid4().hex
    cluster = {
        "side": side,
        "resource_id": str(UUID(token)),
        "nonce": plan_doc["nonce"],
        "container": "twb-ar-" + token,
        "volume": "twb-ar-data-" + token,
        "port": plan_doc[side + "_port"],
        "database": "tender_recovery_" + token,
        "owner_label": owner_label or "twb_archive_owner_" + uuid4().hex,
        "backup_group": backup_group or "twb_backup_read_" + uuid4().hex,
        "accounts": {
            k: s.fresh_account("twb_ar_" + k) for k in ("owner", "ddl", "api", "worker", "reader")
        },
    }
    for kind, name in (("container", cluster["container"]), ("volume", cluster["volume"])):
        args = ["inspect", name] if kind == "container" else ["volume", "inspect", name]
        if s.docker(args, check=False).returncode == 0:
            raise s.RecoveryError("REFUSING_EXISTING_DOCKER_RESOURCE")
    s.docker(["image", "inspect", s.PG_IMAGE])  # no pull/build is permitted
    password_file = directory / (side + "-postgres-password")
    descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(cluster["accounts"]["owner"]["password"])
    s.save(directory / (side + "-creating.json"), cluster)
    label = "tender.application_recovery=" + cluster["resource_id"]
    s.docker(["volume", "create", "--label", label, cluster["volume"]])
    s.docker(
        [
            "run",
            "--detach",
            "--pull=never",
            "--name",
            cluster["container"],
            "--label",
            label,
            "--memory=384m",
            "--cpus=1",
            "--publish",
            "127.0.0.1:" + str(cluster["port"]) + ":5432",
            "--mount",
            "type=volume,src=" + cluster["volume"] + ",dst=/var/lib/postgresql/data",
            "--mount",
            "type=bind,src=" + str(password_file) + ",dst=/run/pg-password,readonly",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/pg-password",
            "-e",
            "POSTGRES_USER=" + cluster["accounts"]["owner"]["user"],
            "-e",
            "POSTGRES_DB=postgres",
            s.PG_IMAGE,
            "-c",
            "shared_buffers=32MB",
            "-c",
            "max_connections=20",
            "-c",
            "max_wal_size=128MB",
        ]
    )
    cluster["container_id"] = json.loads(s.docker(["inspect", cluster["container"]]).stdout)[0][
        "Id"
    ]
    for _ in range(40):
        try:
            with psycopg.connect(
                s.connection_dsn(cluster, database="postgres"), autocommit=True
            ) as c:
                if c.execute("SELECT current_database(),current_user").fetchone() != (
                    "postgres",
                    cluster["accounts"]["owner"]["user"],
                ):
                    raise s.RecoveryError("ADMIN_CONNECTION_IDENTITY_MISMATCH")
                cluster["system_identifier"] = c.execute(
                    "SELECT system_identifier::text FROM pg_control_system()"
                ).fetchone()
                if not cluster["system_identifier"]:
                    raise s.RecoveryError("MISSING_CLUSTER_IDENTITY")
                cluster["system_identifier"] = cluster["system_identifier"][0]
                break
        except psycopg.OperationalError:
            time.sleep(0.25)
    else:
        raise s.RecoveryError("NEW_CLUSTER_NOT_READY")
    assert_resources(cluster)
    with psycopg.connect(s.connection_dsn(cluster, database="postgres"), autocommit=True) as c:
        s.verify(c, cluster, database="postgres")
        if c.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (cluster["database"],)
        ).fetchone():
            raise s.RecoveryError("REFUSING_EXISTING_DATABASE")
        ddl = cluster["accounts"]["ddl"]
        c.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} SUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(s.identifier(ddl["user"]), sql.Literal(ddl["password"]))
        )
        c.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                s.identifier(cluster["database"]), s.identifier(ddl["user"])
            )
        )
        for name in ("postgres", "template0", "template1", cluster["database"]):
            c.execute(
                sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM PUBLIC").format(s.identifier(name))
            )
        for name in (*s.GROUPS, cluster["owner_label"], cluster["backup_group"]):
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(s.identifier(name))
            )
    with psycopg.connect(s.connection_dsn(cluster), autocommit=True) as c:
        s.verify(c, cluster)
        cluster["infrastructure_objects"] = infrastructure_inventory(c, cluster)
    s.save(directory / (side + ".json"), cluster)
    return cluster


def initialize(c, cluster, sql_bytes):
    s.verify(c, cluster, "ddl")
    assert_ddl_role(c, cluster)
    if c.execute("SELECT to_regnamespace('design')").fetchone()[0] is not None:
        raise s.RecoveryError("REFUSING_INITIALIZED_SCHEMA")
    for raw in sql_bytes:
        c.execute(raw)
    c.execute("CREATE SCHEMA recovery_control")
    c.execute("CREATE TABLE recovery_control.identity(value text PRIMARY KEY)")
    c.execute("CREATE SEQUENCE recovery_control.permission_probe")
    c.execute("INSERT INTO recovery_control.identity VALUES(%s)", (cluster["nonce"],))
    c.execute("REVOKE ALL ON SCHEMA recovery_control FROM PUBLIC")
    reassign(c, cluster)


def accounts(c, cluster, *, source=False):
    s.verify(c, cluster)
    for role in ("api", "worker", "reader"):
        account = cluster["accounts"][role]
        c.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(s.identifier(account["user"]), sql.Literal(account["password"]))
        )
        group = cluster["backup_group"] if role == "reader" else "tender_" + role
        c.execute(
            sql.SQL("GRANT {} TO {}").format(s.identifier(group), s.identifier(account["user"]))
        )
        c.execute(
            sql.SQL("ALTER ROLE {} SET search_path=design,public").format(
                s.identifier(account["user"])
            )
        )
        c.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout='30s'").format(
                s.identifier(account["user"])
            )
        )
        if role == "reader":
            c.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(
                    s.identifier(account["user"])
                )
            )
        if role != "worker":
            c.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    s.identifier(cluster["database"]), s.identifier(account["user"])
                )
            )
    c.execute(
        sql.SQL("GRANT USAGE ON SCHEMA recovery_control TO tender_api,{}").format(
            s.identifier(cluster["backup_group"])
        )
    )
    c.execute(
        sql.SQL("GRANT SELECT ON recovery_control.identity TO tender_api,{}").format(
            s.identifier(cluster["backup_group"])
        )
    )
    if source:
        schemas = [
            row[0]
            for row in c.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema' ORDER BY 1"
            )
        ]
        for name in schemas:
            c.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(name), s.identifier(cluster["backup_group"])
                )
            )
        for schema, name, kind in c.execute(
            "SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN('r','p','m','v','S') ORDER BY 1,2"
        ).fetchall():
            c.execute(
                sql.SQL("GRANT SELECT ON {} {}.{} TO {}").format(
                    sql.SQL("SEQUENCE" if kind == "S" else "TABLE"),
                    sql.Identifier(schema),
                    sql.Identifier(name),
                    s.identifier(cluster["backup_group"]),
                )
            )


def infrastructure_inventory(c, cluster):
    user = cluster["accounts"]["owner"]["user"]
    rows = c.execute(
        "SELECT 'database',datname,r.rolname FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE datname IN('postgres','template0','template1') UNION ALL SELECT 'tablespace',spcname,r.rolname FROM pg_tablespace t JOIN pg_roles r ON r.oid=t.spcowner WHERE spcname IN('pg_default','pg_global') UNION ALL SELECT 'extension',extname,r.rolname FROM pg_extension e JOIN pg_roles r ON r.oid=e.extowner WHERE extname='plpgsql' ORDER BY 1,2"
    ).fetchall()
    expected = {("database", n) for n in ("postgres", "template0", "template1")} | {
        ("tablespace", "pg_default"),
        ("tablespace", "pg_global"),
        ("extension", "plpgsql"),
    }
    if {(r[0], r[1]) for r in rows} != expected or any(r[2] != user for r in rows):
        raise s.RecoveryError("INFRASTRUCTURE_DEFAULT_OWNERSHIP_CHANGED")
    return [list(row) for row in rows]


def assert_ddl_role(c, cluster):
    user = cluster["accounts"]["ddl"]["user"]
    if user == cluster["accounts"]["owner"]["user"]:
        raise s.RecoveryError("INITIAL_BOOTSTRAP_CANNOT_BE_DDL_OWNER")
    row = c.execute(
        "SELECT oid,rolsuper,rolcanlogin,EXISTS(SELECT FROM pg_shdepend d WHERE d.refclassid='pg_authid'::regclass AND d.refobjid=r.oid AND d.deptype='p') FROM pg_roles r WHERE rolname=%s",
        (user,),
    ).fetchone()
    if not row or row[0] < 16384 or not row[1] or not row[2] or row[3]:
        raise s.RecoveryError("DDL_ROLE_NOT_FRESH_NONPINNED_TEMPORARY_SUPERUSER")


def reassign(c, cluster):
    """Only the fresh nonpinned business DDL role is reassigned; initdb is never touched."""
    s.verify(c, cluster, "ddl")
    assert_ddl_role(c, cluster)
    if infrastructure_inventory(c, cluster) != cluster["infrastructure_objects"]:
        raise s.RecoveryError("INITIAL_INFRASTRUCTURE_BASELINE_CHANGED")
    user = cluster["accounts"]["ddl"]["user"]
    rows = c.execute(
        "SELECT 'database',datname FROM pg_database WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname=%s) UNION ALL SELECT 'tablespace',spcname FROM pg_tablespace WHERE spcowner=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY 1,2",
        (user, user),
    ).fetchall()
    if set(rows) != {("database", cluster["database"])}:
        raise s.RecoveryError("REASSIGN_UNKNOWN_SHARED_OBJECT")
    cluster["reassigned_shared_objects"] = [list(row) for row in rows]
    c.execute(
        sql.SQL("REASSIGN OWNED BY {} TO {}").format(
            s.identifier(user), s.identifier(cluster["owner_label"])
        )
    )
    after = c.execute(
        "SELECT 'database',datname FROM pg_database WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname=%s) UNION ALL SELECT 'tablespace',spcname FROM pg_tablespace WHERE spcowner=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY 1,2",
        (cluster["owner_label"], cluster["owner_label"]),
    ).fetchall()
    if (
        set(after) != set(rows)
        or infrastructure_inventory(c, cluster) != cluster["infrastructure_objects"]
    ):
        raise s.RecoveryError("REASSIGN_OWNERSHIP_NOT_VERIFIED")


def finish_ddl(c, cluster):
    """Infrastructure disables and demotes only this newly created temporary DDL role."""
    s.verify(c, cluster)
    assert_ddl_role(c, cluster)
    user = cluster["accounts"]["ddl"]["user"]
    if user == cluster["accounts"]["owner"]["user"]:
        raise s.RecoveryError("INITIAL_BOOTSTRAP_CANNOT_BE_DEMOTED")
    if c.execute(
        "SELECT EXISTS(SELECT FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=(SELECT oid FROM pg_roles WHERE rolname=%s) AND dbid IN(0,(SELECT oid FROM pg_database WHERE datname=current_database())))",
        (user,),
    ).fetchone() != (False,):
        raise s.RecoveryError("TEMPORARY_DDL_ROLE_HAS_REMAINING_OWNERSHIP_OR_ACL")
    c.execute(
        sql.SQL(
            "ALTER ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL"
        ).format(s.identifier(user))
    )
    if c.execute(
        "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
        (user,),
    ).fetchone() != (False, False, False, False, False, False):
        raise s.RecoveryError("TEMPORARY_DDL_ROLE_NOT_DISABLED")
    if infrastructure_inventory(c, cluster) != cluster["infrastructure_objects"]:
        raise s.RecoveryError("INFRASTRUCTURE_OWNERSHIP_CHANGED_AFTER_DEMOTION")
    cluster["accounts"]["ddl"].pop("password", None)
    cluster["temporary_ddl_disabled_and_nonowner"] = True
