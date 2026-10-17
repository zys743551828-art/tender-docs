"""Private artifacts, explicit identity and credential-free process boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess  # nosec B404
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = ROOT / ".local/backups/application"
PG_IMAGE = (
    "pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d"
)
GROUPS = (
    "tender_api",
    "tender_worker",
    "tender_score_read",
    "tender_research_read",
    "tender_research_worker",
    "tender_model_train",
    "tender_model_evaluate",
    "tender_model_score",
)
SYSTEM_ENV = {
    "PATH",
    "LANG",
    "LC_ALL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CODEX_PROXY_CERT",
    "DOCKER_CONFIG",
    "HOME",
}
OBJECT_ENV = {
    "OBJECT_STORE_BACKEND",
    "OBJECT_STORE_ENDPOINT",
    "OBJECT_STORE_BUCKET",
    "OBJECT_STORE_REGION",
    "OBJECT_STORE_PREFIX",
    "OBJECT_STORE_ACCESS_KEY",
    "OBJECT_STORE_SECRET_KEY",
    "OBJECT_STORE_BUCKET_URL",
}


class RecoveryError(Exception):
    """Only fixed codes, never provider errors or private SQL, cross the command boundary."""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_path(path, *, exists=True):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise RecoveryError("PRIVATE_PATH_TRAVERSAL")
    if not path.is_relative_to(PRIVATE_ROOT) or path == PRIVATE_ROOT:
        raise RecoveryError("PRIVATE_APPLICATION_ROOT_REQUIRED")
    for ancestor in (path, *path.parents):
        if ancestor == PRIVATE_ROOT.parent:
            break
        if ancestor.is_symlink() or (ancestor.exists() and ancestor.stat().st_mode & 0o077):
            raise RecoveryError("PRIVATE_PATH_MODE_OR_SYMLINK")
    if exists and not path.is_file():
        raise RecoveryError("PRIVATE_REGULAR_FILE_REQUIRED")
    return path


def save(path, value):
    path = private_path(path, exists=False)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)


def load(path):
    path = private_path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise RecoveryError("PRIVATE_JSON_LIMIT")
    return json.loads(path.read_bytes())


def environment(values=None, *, objects=False):
    result = {k: v for k, v in os.environ.items() if k in SYSTEM_ENV}
    if objects:
        result.update({k: v for k, v in (values or {}).items() if k in OBJECT_ENV})
    return result


def owner_fields(dsn):
    try:
        uri = urlsplit(dsn)
        data = {k: str(v) for k, v in conninfo_to_dict(dsn).items() if v is not None}
        if uri.scheme not in {"postgres", "postgresql"} or "?" in dsn or "#" in dsn:
            raise ValueError()
        if set(data) - {"host", "port", "user", "password", "dbname"}:
            raise ValueError()
        if not all(data.get(k) for k in ("host", "user", "password", "dbname")):
            raise ValueError()
        if data["host"] != "127.0.0.1" or not data.get("port", "").isdigit():
            raise ValueError()
        if not 1 <= int(data["port"]) <= 65535:
            raise ValueError()
        return data
    except (ValueError, psycopg.ProgrammingError):
        raise RecoveryError("EXPLICIT_LOOPBACK_IDENTITY_REQUIRED") from None


def connection_dsn(cluster, role="owner", database=None):
    account = cluster["accounts"][role]
    return make_conninfo(
        host="127.0.0.1",
        port=str(cluster["port"]),
        dbname=database or cluster["database"],
        user=account["user"],
        password=account["password"],
        connect_timeout="5",
    )


def verify(c, cluster, role="owner", *, database=None, restricted=False):
    expected = (database or cluster["database"], cluster["accounts"][role]["user"])
    if c.execute("SELECT current_database(),current_user").fetchone() != expected:
        raise RecoveryError("CONNECTED_IDENTITY_MISMATCH")
    # system_identifier access is owner-only: restricted roles verify the cluster marker instead.
    if role == "owner":
        current = c.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
        if current != cluster["system_identifier"]:
            raise RecoveryError("PG_CLUSTER_IDENTITY_MISMATCH")
    else:
        if c.execute("SELECT value FROM recovery_control.identity").fetchone() != (
            cluster["nonce"],
        ):
            raise RecoveryError("DATABASE_RECOVERY_IDENTITY_MISMATCH")
    if restricted:
        flags = c.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not flags or any(flags):
            raise RecoveryError("RESTRICTED_LOGIN_FLAGS_INVALID")
        if c.execute(
            "SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND pg_has_role(current_user,c.relowner,'USAGE'))"
        ).fetchone()[0]:
            raise RecoveryError("LOGIN_INHERITS_OWNER")


def docker(args, *, check=True, timeout=30, input=None):
    result = subprocess.run(  # nosec B603
        ["/usr/local/bin/docker", "--host=unix:///var/run/docker.sock", *args],
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        input=input,
        check=False,
    )
    if check and result.returncode:
        raise RecoveryError("LOCAL_DOCKER_COMMAND_FAILED")
    return result


def pg_tool(cluster, tool, args, *, stdin=None, stdout=None, role="reader"):
    if tool not in {"pg_dump", "pg_restore"}:
        raise RecoveryError("PG_TOOL_DENIED")
    account = cluster["accounts"][role]
    child = environment()
    child.update(
        PGPASSWORD=account["password"], PGUSER=account["user"], PGDATABASE=cluster["database"]
    )
    result = subprocess.run(  # nosec B603
        [
            "/usr/local/bin/docker",
            "--host=unix:///var/run/docker.sock",
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
            cluster["container"],
            tool,
            *args,
        ],
        env=child,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RecoveryError("PG_TOOL_FAILED_PRIVATE_DETAILS_SUPPRESSED")


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", value):
        raise RecoveryError("GENERATED_IDENTIFIER_INVALID")
    return sql.Identifier(value)


def fresh_account(prefix):
    return {"user": prefix + "_" + secrets.token_hex(12), "password": secrets.token_urlsafe(32)}


def budget(minimum=1400 * 1024 * 1024):
    if shutil.disk_usage(ROOT).free < minimum:
        raise RecoveryError("INSUFFICIENT_FREE_DISK_NO_CLEANUP_ATTEMPTED")
