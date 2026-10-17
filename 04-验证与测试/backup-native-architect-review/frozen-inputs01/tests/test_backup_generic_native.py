"""Opt-in real native OSS: exclusively new synthetic keys and freshly initialized databases."""

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from deploy.backups import generic, run


class PrivateConfiguration(dict):
    def __repr__(self):
        return "<private native backup profile redacted>"


def test_real_native_oss_catalog_backup_and_fresh_read_only_restore(monkeypatch):
    profile = os.environ.get("BACKUP_GENERIC_NATIVE_ENV_FILE")
    if not profile:
        pytest.skip(
            "explicit isolated native OSS profile required; creates only synthetic own keys"
        )
    values = PrivateConfiguration(run.configuration(Path(profile)))
    assert values.get("OBJECT_STORE_BACKEND") == "oss"
    assert values.get("OBJECT_STORE_PREFIX")
    suffix = uuid4().hex
    source_name = "tender_native_probe_" + suffix
    values["OWNER_DATABASE_DSN"] = make_conninfo(values["OWNER_DATABASE_DSN"], dbname=source_name)
    values["API_DATABASE_DSN"] = make_conninfo(values["API_DATABASE_DSN"], dbname=source_name)
    values["OBJECT_STORE_PREFIX"] += "generic-native-drills/" + suffix + "/"
    # The admin DSN is used only to create our fresh DB, never an existing business DB.
    admin = PrivateConfiguration(run.configuration(Path(profile)))
    with psycopg.connect(admin["OWNER_DATABASE_DSN"], autocommit=True) as bootstrap:
        bootstrap.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source_name))
        )
    initialized = []
    with psycopg.connect(values["OWNER_DATABASE_DSN"], autocommit=True) as bootstrap:
        for name in (
            "core.sql",
            "domain.sql",
            "retrieval.sql",
            "worker-functions.sql",
            "roles.sql",
        ):
            relative = "packages/contracts/sql/" + name
            bootstrap.execute((run.ROOT / relative).read_text())
            initialized.append(relative)
        for relative in (
            "deploy/sql/vector-runtime-grants.sql",
            "deploy/sql/research-source-qualification.sql",
        ):
            bootstrap.execute((run.ROOT / relative).read_text())
            initialized.append(relative)
        bootstrap.execute('CREATE SCHEMA "future.native"')
        bootstrap.execute(
            'CREATE TABLE "future.native"."quote""rows"(id uuid PRIMARY KEY,note text,embedding design.vector(2))'
        )
        bootstrap.execute('GRANT USAGE ON SCHEMA "future.native" TO tender_api')
        bootstrap.execute('GRANT SELECT,INSERT ON "future.native"."quote""rows" TO tender_api')
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WORKBENCH_ENV_FILE", str(Path(profile)))
    from packages.domain import storage

    factory = storage.client
    calls = []
    permitted_keys = set()

    class ExactObjects:
        def __init__(self):
            self.port = factory()
            self.prefix = os.environ["OBJECT_STORE_PREFIX"]

        def put_object(self, **kwargs):
            assert kwargs["Key"] in permitted_keys
            calls.append(("PUT", self.prefix, kwargs["Key"]))
            return self.port.put_object(**kwargs)

        def get_object(self, **kwargs):
            assert kwargs["Key"] in permitted_keys
            calls.append(("GET", self.prefix, kwargs["Key"]))
            return self.port.get_object(**kwargs)

    monkeypatch.setattr(storage, "client", ExactObjects)
    actor, bid = uuid4(), uuid4()
    with psycopg.connect(values["API_DATABASE_DSN"]) as c:
        assert generic.restricted(c) == conninfo_to_dict(values["API_DATABASE_DSN"])["user"]
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,'合成原生OSS备份机制成员')", (actor,)
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成独立备份工程','{}',%s)",
            (bid, actor),
        )
        c.execute(
            'INSERT INTO "future.native"."quote""rows" VALUES(%s,%s,\'[1,0]\')',
            (uuid4(), "真实新schema合成行"),
        )
    payloads = (
        b"",
        "合成官方原件：仅验证档案字节，不是正式成绩。".encode(),
        bytes(range(256)) * 2049,
    )
    registered = []

    def register(raw, late=False):
        blob, document = uuid4(), uuid4()
        key = "synthetic/" + str(blob)
        permitted_keys.add(key)
        storage.put(key, raw, "application/octet-stream")
        with psycopg.connect(values["API_DATABASE_DSN"]) as c:
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/octet-stream')",
                (blob, run.digest(raw), key, len(raw)),
            )
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成备份原件.bin',clock_timestamp(),'source_original',%s)",
                (document, bid, blob, actor),
            )
            if late:
                c.execute(
                    'INSERT INTO "future.native"."quote""rows" VALUES(%s,%s,\'[0,1]\')',
                    (uuid4(), "快照导出后并发提交"),
                )
        registered.append(
            {
                "id": str(blob),
                "document": str(document),
                "key": key,
                "sha256": run.digest(raw),
                "bytes": len(raw),
            }
        )

    for raw in payloads:
        register(raw)
    root = run.ROOT / ".local/backups"
    root.mkdir(mode=0o700, exist_ok=True)
    archive = root / ("generic-native-" + suffix)
    container = os.environ.get(
        "BACKUP_GENERIC_NATIVE_CONTAINER", "tender-workbench-external-postgres-1"
    )
    manifest = generic.snapshot(
        values,
        archive,
        container,
        include_objects=True,
        after_snapshot=lambda: register(b"Synthetic committed after exported snapshot", late=True),
    )
    assert len(manifest["objects"]) == 3 and len(registered) == 4
    assert {o["id"] for o in manifest["objects"]} == {o["id"] for o in registered[:3]}
    future = next(t for t in manifest["tables"] if t["schema"] == "future.native")
    assert future["rows"] == 1
    assert run.locator(values) == manifest["object_store"]
    assert generic.validate(archive, require_objects=True) == manifest
    receipt = generic.restore(values, archive, container, include_objects=True)
    assert (
        receipt["status"] == "passed_full_development_restore" and receipt["objects_verified"] == 3
    )
    assert receipt["object_bytes_included"] and not receipt["full_application_restore_qualified"]
    assert receipt["database"] != source_name
    target_api = make_conninfo(values["API_DATABASE_DSN"], dbname=receipt["database"])
    with psycopg.connect(target_api) as c:
        assert c.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert c.execute("SELECT count(*) FROM blobs").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM document_versions").fetchone()[0] == 3
        assert c.execute('SELECT note,embedding FROM "future.native"."quote""rows"').fetchone() == (
            "真实新schema合成行",
            "[1,0]",
        )
        assert (
            c.execute("SELECT count(*) FROM blobs WHERE id=%s", (registered[-1]["id"],)).fetchone()[
                0
            ]
            == 0
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(
            make_conninfo(target_api, options="-c default_transaction_read_only=off")
        ) as c:
            c.execute(
                'INSERT INTO "future.native"."quote""rows"(id,note) VALUES(%s,\'must fail\')',
                (uuid4(),),
            )
    with psycopg.connect(values["API_DATABASE_DSN"]) as c:
        assert c.execute("SELECT count(*) FROM blobs").fetchone()[0] == 4
        assert c.execute('SELECT count(*) FROM "future.native"."quote""rows"').fetchone()[0] == 2
    monkeypatch.setenv("OBJECT_STORE_PREFIX", receipt["restored_object_prefix"])
    port = storage.client()
    for obj, raw in zip(registered[:3], payloads, strict=True):
        assert (
            run.read_object(port, storage.bucket(), obj["key"], obj["bytes"], obj["sha256"]) == raw
        )
    monkeypatch.setenv("OBJECT_STORE_PREFIX", values["OBJECT_STORE_PREFIX"])
    with psycopg.connect(values["OWNER_DATABASE_DSN"], autocommit=True) as bootstrap:
        generic._harden_new_database(
            bootstrap,
            generic.catalog(bootstrap),
            source_name,
            str(conninfo_to_dict(values["API_DATABASE_DSN"])["user"]),
        )
    assert len(calls) == 16 and {c[0] for c in calls} == {"PUT", "GET"}
    assert {c[1] for c in calls} == {
        values["OBJECT_STORE_PREFIX"],
        receipt["restored_object_prefix"],
    }
    assert not any(c[0] == "GET" and c[2] == registered[-1]["key"] for c in calls)
    proof = {
        **receipt,
        "source_probe_database": source_name,
        "archive_relative_path": str(archive.relative_to(run.ROOT)),
        "source_object_prefix": values["OBJECT_STORE_PREFIX"],
        "source_committed_objects": 4,
        "snapshot_objects": 3,
        "objects_total_bytes": sum(map(len, payloads)),
        "concurrent_source_commit_excluded_from_dump_and_object_manifest": True,
        "exact_native_requests": {
            "PUT": sum(c[0] == "PUT" for c in calls),
            "GET": sum(c[0] == "GET" for c in calls),
            "LIST": 0,
            "DELETE": 0,
        },
        "all_registered_source_and_restore_objects_retained": True,
        "quoted_new_schema_vector_and_actual_write_denial_verified": True,
        "source_probe_hardened_read_only": True,
        "initialized_sql": {p: run.digest((run.ROOT / p).read_bytes()) for p in initialized},
        "model_calls": 0,
        "consumer_started": False,
        "production_rpo_rto_qualified": False,
    }
    run.private_write(
        archive / "native-proof.json", json.dumps(proof, ensure_ascii=False, indent=2).encode()
    )
