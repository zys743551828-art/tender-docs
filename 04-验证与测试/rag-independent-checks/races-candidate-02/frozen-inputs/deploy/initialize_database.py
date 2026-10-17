"""Initialize the isolated empty development database with reviewed SQL."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def file_configuration(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                parsed = shlex.split(value, comments=True)
                if len(parsed) > 1:
                    raise ValueError(f"Quote configuration values containing spaces: {key}")
                values[key.strip()] = parsed[0] if parsed else ""
    return values


def local_configuration() -> dict[str, str]:
    env_file = Path(os.environ.get("WORKBENCH_ENV_FILE", str(ROOT / ".env")))
    values = file_configuration(env_file)
    values.update(os.environ)
    return values


def main() -> None:
    values = local_configuration()
    with psycopg.connect(values["OWNER_DATABASE_DSN"], autocommit=True) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT FROM pg_namespace WHERE nspname='design')"
        ).fetchone()
        if row and row[0]:
            raise SystemExit("Refusing initialization: design schema already exists.")
        for name in (
            "core.sql",
            "domain.sql",
            "retrieval.sql",
            "worker-functions.sql",
            "roles.sql",
        ):
            connection.execute((ROOT / "packages/contracts/sql" / name).read_text())
            print(f"Initialized {name}")
        connection.execute((ROOT / "deploy/sql/vector-runtime-grants.sql").read_text())
        print("Initialized restricted vector runtime capabilities")
        with connection.transaction():
            for role in ("api", "worker", "research_worker", "score_read"):
                dsn = urlsplit(values[f"{role.upper()}_DATABASE_DSN"])
                if not dsn.username or not dsn.password:
                    raise ValueError("Missing application LOGIN configuration")
                login = unquote(dsn.username)
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} INHERIT").format(
                        sql.Identifier(login), sql.Literal(unquote(dsn.password))
                    )
                )
                connection.execute(
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier(f"tender_{role}"), sql.Identifier(login)
                    )
                )
                connection.execute(
                    sql.SQL("ALTER ROLE {} SET search_path TO design, public").format(
                        sql.Identifier(login)
                    )
                )
                connection.execute(
                    sql.SQL("ALTER ROLE {} SET statement_timeout TO '30s'").format(
                        sql.Identifier(login)
                    )
                )
    print("Empty database initialized; independent nonowner LOGIN roles created.")


if __name__ == "__main__":
    main()
