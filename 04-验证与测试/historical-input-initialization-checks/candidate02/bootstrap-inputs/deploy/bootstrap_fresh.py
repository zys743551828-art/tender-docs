"""One-shot fresh database/account bootstrap; no entry point accepts an existing target DB."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from deploy.initialize_database import ROOT, file_configuration

BASE_SQL = (
    "packages/contracts/sql/core.sql",
    "packages/contracts/sql/domain.sql",
    "packages/contracts/sql/retrieval.sql",
    "packages/contracts/sql/worker-functions.sql",
    "packages/contracts/sql/roles.sql",
    "deploy/sql/vector-runtime-grants.sql",
    "deploy/sql/research-source-qualification.sql",
)
CAPABILITIES = ("api", "worker", "research_worker", "score_read")
PURPOSES = ("model_train", "model_evaluate")
GROUPS = (
    "tender_api",
    "tender_worker",
    "tender_score_read",
    "tender_research_read",
    "tender_research_worker",
)


class BootstrapError(Exception):
    pass


@dataclass(frozen=True)
class ReviewedSql:
    path: str
    sha256: str
    content: bytes


def reviewed_sql(path, expected=None):
    path = Path(path).resolve()
    if (
        not path.is_relative_to(ROOT)
        or path.suffix != ".sql"
        or any(p.startswith(".") for p in path.relative_to(ROOT).parts)
    ):
        raise BootstrapError("SQL_REQUIRES_PUBLIC_REPOSITORY_PATH")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected is not None and (not re.fullmatch(r"[a-f0-9]{64}", expected) or digest != expected):
        raise BootstrapError("REVIEWED_SQL_DIGEST_MISMATCH")
    content.decode("utf-8")
    return ReviewedSql(str(path.relative_to(ROOT)), digest, content)


def sql_plan(extensions):
    # Freeze every byte before any database/account mutation. Execution never rereads these files.
    result = [reviewed_sql(ROOT / name) for name in BASE_SQL]
    result.extend(reviewed_sql(path, expected) for path, expected in extensions)
    if len({item.path for item in result}) != len(result):
        raise BootstrapError("DUPLICATE_SQL_IN_ORDERED_PLAN")
    return tuple(result)


def write_private(path, values):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))


def private_configuration(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise BootstrapError("BASE_PROFILE_MUST_BE_PRIVATE_600_REGULAR_FILE")
    result = file_configuration(path)
    if not result.get("OWNER_DATABASE_DSN"):
        raise BootstrapError("INITIALIZATION_OWNER_REQUIRED")
    return result


def business_configuration(values):
    return {
        key: value
        for key, value in values.items()
        if key
        not in {
            "OWNER_DATABASE_DSN",
            "POSTGRES_PASSWORD",
            "MODEL_TRAIN_DATABASE_DSN",
            "MODEL_EVALUATE_DATABASE_DSN",
            "WORKBENCH_ENV_FILE",
        }
    }


def with_app_origin(values, origin):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.port
    ):
        raise BootstrapError("CHECK_ORIGIN_REQUIRES_LOOPBACK_HTTP_PORT")
    return {**values, "APP_ORIGIN": origin}


def owner_parameters(value) -> dict[str, str]:
    """Restricted single-host URI. Query identity/service overrides are never accepted."""
    try:
        uri = urlsplit(value)
        if uri.scheme not in {"postgres", "postgresql"} or "?" in value or "#" in value:
            raise ValueError("unsupported URI features")
        fields = {
            key: str(item) for key, item in conninfo_to_dict(value).items() if item is not None
        }
        if set(fields) - {"host", "port", "user", "password", "dbname"}:
            raise ValueError("unsupported connection options")
        if not all(fields.get(key) for key in ("host", "user", "password", "dbname")):
            raise ValueError("explicit connection identity required")
        if (
            "," in fields["host"]
            or "/" in fields["host"]
            or any(c.isspace() for c in fields["host"])
        ):
            raise ValueError("single network host required")
        port = fields.get("port", "5432")
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError("single port required")
        fields["port"] = port
        return fields
    except (ValueError, psycopg.ProgrammingError):
        raise BootstrapError("OWNER_DSN_REQUIRES_EXPLICIT_SINGLE_HOST_URI_WITHOUT_QUERY") from None


def identity_dsn(owner, *, database, user=None, password=None):
    # libpq explicit overrides, then a canonical query-free URI for existing check tooling.
    raw = conninfo_to_dict(
        make_conninfo(
            **{
                **owner,
                "dbname": database,
                "user": user if user is not None else owner["user"],
                "password": password if password is not None else owner["password"],
            }
        )
    )
    normalized = {key: str(item) for key, item in raw.items() if item is not None}
    host = normalized["host"]
    if ":" in host:
        host = "[" + host + "]"
    authority = (
        quote(normalized["user"], safe="")
        + ":"
        + quote(normalized["password"], safe="")
        + "@"
        + host
        + ":"
        + normalized["port"]
    )
    result = urlunsplit(
        ("postgresql", authority, "/" + quote(normalized["dbname"], safe=""), "", "")
    )
    if conninfo_to_dict(result) != normalized:
        raise BootstrapError("DERIVED_CONNECTION_IDENTITY_INVALID")
    return result


def verify_connection(c, *, database, user, restricted=False):
    row = c.execute("SELECT current_database(),current_user").fetchone()
    if row is None or tuple(row) != (database, user):
        raise BootstrapError("CONNECTED_DATABASE_OR_USER_MISMATCH")
    if restricted:
        flags = c.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if flags is None or any(flags):
            raise BootstrapError("CONNECTED_LOGIN_IS_NOT_RESTRICTED")
        owns = c.execute(
            "SELECT EXISTS(SELECT FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND relnamespace IN(to_regnamespace('design'),to_regnamespace('retrieval'),to_regnamespace('model_governance')))"
        ).fetchone()
        if owns is None or owns[0]:
            raise BootstrapError("CONNECTED_LOGIN_OWNS_BUSINESS_TABLES")


def bootstrap(
    *,
    base_profile,
    name,
    extensions=(),
    model_purposes=False,
    kafka_bootstrap=None,
    kafka_port=None,
    compose_project=None,
    app_origin=None,
):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", name):
        raise BootstrapError("INVALID_FRESH_ENVIRONMENT_NAME")
    plan = sql_plan(extensions)
    if model_purposes and "deploy/sql/model-governance.sql" not in {p.path for p in plan}:
        raise BootstrapError("MODEL_PURPOSES_REQUIRE_REVIEWED_GOVERNANCE_SQL")
    values = private_configuration(base_profile)
    if app_origin is not None:
        values = with_app_origin(values, app_origin)
    directory = ROOT / ".local"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise BootstrapError("PRIVATE_DIRECTORY_PERMISSIONS")
    paths = {
        "init": directory / (name + "-init.env"),
        "business": directory / (name + "-checks.env"),
    }
    if model_purposes:
        paths.update({purpose: directory / (name + "-" + purpose + ".env") for purpose in PURPOSES})
    if any(path.exists() or path.is_symlink() for path in paths.values()):
        raise BootstrapError("REFUSING_EXISTING_PROFILE")
    owner = owner_parameters(values["OWNER_DATABASE_DSN"])
    database = "tender_checks_" + name
    owner_dsn = identity_dsn(owner, database=database)
    groups = (
        *GROUPS,
        *(
            ("tender_model_train", "tender_model_evaluate", "tender_model_score")
            if model_purposes
            else ()
        ),
    )
    roles = (*CAPABILITIES, *(PURPOSES if model_purposes else ()))
    nonce = secrets.token_hex(4)
    accounts = {}
    for role in roles:
        login = f"twb_checks_{name}_{nonce}_{role}"
        if len(login) > 63:
            raise BootstrapError("LOGIN_IDENTIFIER_TOO_LONG")
        password = secrets.token_urlsafe(32)
        dsn = identity_dsn(owner, database=database, user=login, password=password)
        accounts[role] = (login, password, dsn)
        values[role.upper() + "_DATABASE_DSN"] = dsn
    values["OWNER_DATABASE_DSN"] = owner_dsn
    if not values.get("OBJECT_STORE_PREFIX"):
        raise BootstrapError("ISOLATED_OBJECT_PREFIX_REQUIRED")
    values["OBJECT_STORE_PREFIX"] = (
        values["OBJECT_STORE_PREFIX"].rstrip("/")
        + "/check-environments/"
        + secrets.token_hex(16)
        + "/"
    )
    values.pop("OSS_KEY_PREFIX", None)
    values.pop("WORKBENCH_ENV_FILE", None)
    if any(value is not None for value in (kafka_bootstrap, kafka_port, compose_project)):
        if (
            kafka_bootstrap != "127.0.0.1:19096"
            or kafka_port != 19096
            or not compose_project
            or not re.fullmatch(r"[a-z][a-z0-9-]{2,50}", compose_project)
        ):
            raise BootstrapError("FRESH_COMBINATION_REQUIRES_DEDICATED_KAFKA_19096")
        values.update(
            KAFKA_BOOTSTRAP_SERVERS=kafka_bootstrap,
            KAFKA_EXTERNAL_PORT=str(kafka_port),
            COMPOSE_PROJECT_NAME=compose_project,
            KAFKA_CLUSTER_ID=secrets.token_urlsafe(16),
        )
    else:
        # Without an explicitly isolated broker, no runnable shared-topic address is inherited.
        values["KAFKA_BOOTSTRAP_SERVERS"] = ""
        for key in ("KAFKA_EXTERNAL_PORT", "COMPOSE_PROJECT_NAME", "KAFKA_CLUSTER_ID"):
            values.pop(key, None)
    with psycopg.connect(identity_dsn(owner, database="postgres"), autocommit=True) as admin:
        verify_connection(admin, database="postgres", user=owner["user"])
        found = admin.execute(
            "SELECT rolname,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=ANY(%s)",
            (list(groups),),
        ).fetchall()
        if {row[0] for row in found} != set(groups) or any(any(row[1:]) for row in found):
            raise BootstrapError("REQUIRED_EXISTING_NOLOGIN_CAPABILITY_GROUP_MISSING_OR_UNSAFE")
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            raise BootstrapError("REFUSING_EXISTING_DATABASE")
        if admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", ([a[0] for a in accounts.values()],)
        ).fetchone():
            raise BootstrapError("REFUSING_EXISTING_LOGIN")
        # Preserve the only recovery credentials before fresh creation. Existing files never replaced.
        write_private(paths["init"], values)
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database))
        )
        admin.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
    with psycopg.connect(owner_dsn, autocommit=True) as fresh:
        verify_connection(fresh, database=database, user=owner["user"])
        for item in plan:
            fresh.execute(item.content.decode("utf-8"))
        with fresh.transaction():
            for role, (login, password, _) in accounts.items():
                fresh.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(login), sql.Literal(password))
                )
                fresh.execute(
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier("tender_" + role), sql.Identifier(login)
                    )
                )
                fresh.execute(
                    sql.SQL("ALTER ROLE {} SET search_path TO design, public").format(
                        sql.Identifier(login)
                    )
                )
                fresh.execute(
                    sql.SQL("ALTER ROLE {} SET statement_timeout TO '30s'").format(
                        sql.Identifier(login)
                    )
                )
                fresh.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(login)
                    )
                )
    for login, _, dsn in accounts.values():
        with psycopg.connect(dsn) as verifier:
            verify_connection(verifier, database=database, user=login, restricted=True)
    write_private(paths["business"], business_configuration(values))
    for purpose in PURPOSES if model_purposes else ():
        key = purpose.upper() + "_DATABASE_DSN"
        write_private(paths[purpose], {key: values[key]})
    return {
        "database": database,
        "profiles": {key: str(path.relative_to(ROOT)) for key, path in paths.items()},
        "ordered_sql": [{"path": p.path, "sha256": p.sha256} for p in plan],
        "logins": {role: account[0] for role, account in accounts.items()},
        "object_store_prefix": values["OBJECT_STORE_PREFIX"],
        "kafka_bootstrap": values.get("KAFKA_BOOTSTRAP_SERVERS"),
        "compose_project": values.get("COMPOSE_PROJECT_NAME"),
        "app_origin": values.get("APP_ORIGIN"),
        "all_created_login_connections_verified_before_business_profiles": True,
        "no_business_process_started": True,
    }
