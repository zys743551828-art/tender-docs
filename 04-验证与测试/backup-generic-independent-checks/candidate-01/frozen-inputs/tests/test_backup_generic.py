"""Future schema catalog/quoting boundaries plus opt-in local database-only recovery drill."""

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from deploy.backups import generic, run


class PrivateConfiguration(dict):
    def __repr__(self):
        return "<private backup configuration redacted>"


class CatalogConnection:
    def __init__(self, relations):
        self.results = iter(
            [["design", "future.metrics", "public"], relations, [("plpgsql", "1.0", "pg_catalog")]]
        )

    def execute(self, query, params=()):
        rows = next(self.results)
        if rows and isinstance(rows[0], str):
            rows = [(value,) for value in rows]
        return type("Result", (), {"fetchall": lambda self: rows})()


def test_unknown_user_schema_is_discovered_not_silently_omitted():
    value = generic.catalog(CatalogConnection([("future.metrics", "table.with.dot", "r", False)]))
    assert value["relations"] == [
        {"schema": "future.metrics", "name": "table.with.dot", "kind": "r"}
    ]
    assert "future.metrics" in value["schemas"]


@pytest.mark.parametrize(
    "kind,rls,message", [("f", False, "FOREIGN_DATA"), ("r", True, "ROW_SECURITY")]
)
def test_catalog_rejects_implicit_remote_or_filtered_backup_before_selecting_rows(
    kind, rls, message
):
    with pytest.raises(run.BackupError, match=message):
        generic.catalog(CatalogConnection([("future_metrics", "rows", kind, rls)]))


def test_database_component_cannot_be_misrepresented_as_full_backup(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(run, "ARCHIVE_ROOT", root)
    archive = root / "fixture"
    archive.mkdir(mode=0o700)
    (archive / "objects").mkdir(mode=0o700)
    run.private_write(archive / "database.dump", b"synthetic-dump")
    manifest = {
        "format": generic.FORMAT,
        "objects_included": False,
        "objects": [],
        "database_sha256": generic.hash_file(archive / "database.dump"),
    }
    raw = json.dumps(manifest).encode()
    run.private_write(archive / "manifest.json", raw)
    run.private_write(archive / "manifest.sha256", run.digest(raw).encode())
    with pytest.raises(run.BackupError, match="DATABASE_COMPONENT_HAS_NO_OBJECT_BYTES"):
        generic.validate(archive, require_objects=True)


def test_real_future_schema_snapshot_restore_read_only_without_object_requests():
    profile = os.environ.get("BACKUP_GENERIC_INTEGRATION_ENV_FILE")
    if not profile:
        pytest.skip("explicit local PG profile required; no remote object calls")
    values = PrivateConfiguration(run.configuration(Path(profile)))
    source_name = "tender_catalog_probe_" + uuid4().hex
    source_owner = make_conninfo(values["OWNER_DATABASE_DSN"], dbname=source_name)
    source_api = make_conninfo(values["API_DATABASE_DSN"], dbname=source_name)
    with psycopg.connect(values["OWNER_DATABASE_DSN"], autocommit=True) as bootstrap:
        bootstrap.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source_name))
        )
    with psycopg.connect(source_owner, autocommit=True) as bootstrap:
        for name in (
            "core.sql",
            "domain.sql",
            "retrieval.sql",
            "worker-functions.sql",
            "roles.sql",
        ):
            bootstrap.execute((run.ROOT / "packages/contracts/sql" / name).read_text())
        bootstrap.execute("CREATE SCHEMA future_metrics")
        bootstrap.execute(
            "CREATE TABLE future_metrics.probe_rows(id uuid PRIMARY KEY,payload jsonb NOT NULL,embedding design.vector(2))"
        )
        bootstrap.execute("GRANT USAGE ON SCHEMA future_metrics TO tender_api")
        bootstrap.execute("GRANT SELECT,INSERT,UPDATE ON future_metrics.probe_rows TO tender_api")
        bootstrap.execute('CREATE SCHEMA "future.metrics"')
        bootstrap.execute(
            'CREATE TABLE "future.metrics"."quote""rows"(id uuid PRIMARY KEY,note text)'
        )
        bootstrap.execute('GRANT USAGE ON SCHEMA "future.metrics" TO tender_api')
        bootstrap.execute(
            'GRANT SELECT,INSERT,UPDATE ON "future.metrics"."quote""rows" TO tender_api'
        )
    values["OWNER_DATABASE_DSN"] = source_owner
    values["API_DATABASE_DSN"] = source_api
    first, late = uuid4(), uuid4()
    with psycopg.connect(source_api) as c:
        c.execute(
            "INSERT INTO future_metrics.probe_rows(id,payload,embedding) VALUES(%s,%s,'[1,0]')",
            (first, Jsonb({"synthetic": "新schema快照行"})),
        )
    with psycopg.connect(source_api) as c:
        c.execute(
            'INSERT INTO "future.metrics"."quote""rows" VALUES(%s,%s)',
            (uuid4(), "合成带点schema与引号表名"),
        )
    root = run.ROOT / ".local/backups"
    root.mkdir(mode=0o700, exist_ok=True)
    archive = root / ("future-schema-" + uuid4().hex)

    def concurrent_insert():
        with psycopg.connect(source_api) as c:
            c.execute(
                "INSERT INTO future_metrics.probe_rows(id,payload,embedding) VALUES(%s,%s,'[1,0]')",
                (late, Jsonb({"synthetic": "导出快照后提交，不进入档案"})),
            )

    container = os.environ.get("BACKUP_GENERIC_PG_CONTAINER", "tender-workbench-dev-postgres-1")
    manifest = generic.snapshot(
        values, archive, container, include_objects=False, after_snapshot=concurrent_insert
    )
    table = next(t for t in manifest["tables"] if t["schema"] == "future_metrics")
    assert table["rows"] == 1 and not manifest["objects_included"]
    receipt = generic.restore(values, archive, container, include_objects=False)
    assert receipt["status"] == "passed_database_component_only"
    assert "future_metrics" in receipt["schemas_verified"] and receipt["tables_verified"] == len(
        manifest["tables"]
    )
    target_api = make_conninfo(source_api, dbname=receipt["database"])
    with psycopg.connect(source_api) as source:
        assert source.execute("SELECT count(*) FROM future_metrics.probe_rows").fetchone()[0] == 2
    with psycopg.connect(target_api) as restored:
        assert restored.execute("SELECT id FROM future_metrics.probe_rows").fetchone()[0] == first
        assert restored.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert (
            restored.execute("SELECT embedding FROM future_metrics.probe_rows").fetchone()[0]
            == "[1,0]"
        )
        assert (
            restored.execute('SELECT note FROM "future.metrics"."quote""rows"').fetchone()[0]
            == "合成带点schema与引号表名"
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(
            make_conninfo(target_api, options="-c default_transaction_read_only=off")
        ) as restored:
            restored.execute(
                "INSERT INTO future_metrics.probe_rows(id,payload,embedding) VALUES(%s,%s,'[1,0]')",
                (uuid4(), Jsonb({"synthetic": "write must fail"})),
            )
    from psycopg.conninfo import conninfo_to_dict

    with psycopg.connect(source_owner, autocommit=True) as bootstrap:
        generic._harden_new_database(
            bootstrap,
            generic.catalog(bootstrap),
            source_name,
            str(conninfo_to_dict(source_api)["user"]),
        )
    proof = {
        **receipt,
        "source_probe_database": source_name,
        "concurrent_source_commit_excluded": True,
        "quoted_future_schema_and_relation_verified": True,
        "vector_type_row_restored_readable_without_user_function_execute": True,
        "owned_source_probe_also_hardened_read_only": True,
        "future_schema_real_insert_denied_with_read_only_default_disabled": True,
        "remote_object_requests": 0,
        "retained_for_independent_read_only_inspection": True,
        "private_archive_relative_path": str(archive.relative_to(run.ROOT)),
    }
    run.private_write(archive / "future-schema-proof.json", json.dumps(proof, indent=2).encode())
