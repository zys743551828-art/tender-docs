"""Independent boundary cases for the new application-recovery path (no external writes)."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from deploy.application_recovery import application, archive, assets, cluster, security


@pytest.mark.parametrize(
    "tail", ["?dbname=existing", "?user=old", "?password=secret", "#fragment", "?sslmode=require"]
)
def test_connection_overrides_rejected(tail):
    with pytest.raises(security.RecoveryError, match="EXPLICIT_LOOPBACK"):
        security.owner_fields("postgresql://owner:password@127.0.0.1:55521/new" + tail)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@host:5432/db",
        "postgresql://u:p@127.0.0.1,localhost/db",
        "postgresql://u@127.0.0.1:5432/db",
        "postgresql://u:p@127.0.0.1:99999/db",
    ],
)
def test_nonexplicit_loopback_identity_rejected(dsn):
    with pytest.raises(security.RecoveryError):
        security.owner_fields(dsn)


def test_percent_encoded_identity_is_parsed_by_libpq():
    assert (
        security.owner_fields("postgresql://u%40name:p%40ss@127.0.0.1:55521/db")["user"] == "u@name"
    )


def test_process_environment_does_not_inherit_credentials(monkeypatch):
    for name in (
        "BACKUP_DATABASE_DSN",
        "API_DATABASE_DSN",
        "WORKER_DATABASE_DSN",
        "MODEL_TRAIN_DATABASE_DSN",
        "OWNER_DATABASE_DSN",
        "DEEPSEEK_API_KEY",
        "PGPASSWORD",
        "PYTHONPATH",
        "DOCKER_HOST",
    ):
        monkeypatch.setenv(name, "private-sentinel")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    assert security.environment().get("HTTPS_PROXY") == "http://proxy:8080"
    assert "private-sentinel" not in security.environment().values()


def test_object_environment_restores_outer_values(monkeypatch):
    monkeypatch.setenv("BACKUP_DATABASE_DSN", "private-sentinel")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    import os

    with archive.object_environment({"OBJECT_STORE_PREFIX": "own/", "OWNER_DATABASE_DSN": "never"}):
        assert os.environ["HTTPS_PROXY"] == "http://proxy:8080"
        assert os.environ["OBJECT_STORE_PREFIX"] == "own/"
        assert "OWNER_DATABASE_DSN" not in os.environ
        assert "BACKUP_DATABASE_DSN" not in os.environ
    assert os.environ["BACKUP_DATABASE_DSN"] == "private-sentinel"


def test_private_paths_reject_traversal_symlink_and_public_mode(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(security, "PRIVATE_ROOT", root)
    outside = tmp_path / "outside"
    outside.write_text("private")
    with pytest.raises(security.RecoveryError):
        security.private_path(root / ".." / "outside")
    (root / "link").symlink_to(outside)
    with pytest.raises(security.RecoveryError):
        security.private_path(root / "link")
    (root / "public").write_text("private")
    (root / "public").chmod(0o644)
    with pytest.raises(security.RecoveryError):
        security.private_path(root / "public")


def test_private_receipts_are_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "PRIVATE_ROOT", tmp_path)
    tmp_path.chmod(0o700)
    path = tmp_path / "receipt.json"
    security.save(path, {"stage": "first"})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        security.save(path, {"stage": "changed"})
    assert security.load(path) == {"stage": "first"}


def test_unreviewed_baseline_cannot_supply_arbitrary_sql(tmp_path):
    path = tmp_path / "fake.json"
    path.write_text(
        json.dumps({"format": "tender-application-recovery-protocol/v2", "ordered_sql": []})
    )
    with pytest.raises(security.RecoveryError, match="UNREVIEWED_BASELINE"):
        cluster.validate_inputs(path)


def test_exact_reviewed_308_and_15_inputs():
    baseline = Path(
        "/workspace/tender-docs/04-验证与测试/application-recovery-development-checks/protocol-02/baseline-inputs.json"
    )
    document, sql_bytes = cluster.validate_inputs(baseline)
    assert document["backend"]["file_count"] == 308
    assert len(sql_bytes) == 15
    assert "model_release.certifications" in "\n".join(sql_bytes)


def test_unknown_shared_object_prevents_reassign(monkeypatch):
    monkeypatch.setattr(security, "verify", lambda *args, **kwargs: None)
    monkeypatch.setattr(cluster, "assert_ddl_role", lambda *args: None)
    monkeypatch.setattr(cluster, "infrastructure_inventory", lambda *args: [])
    c = Mock()
    c.execute.return_value.fetchall.return_value = [("database", "unrecorded")]
    with pytest.raises(security.RecoveryError, match="UNKNOWN_SHARED"):
        cluster.reassign(
            c,
            {
                "database": "own_new",
                "owner_label": "own_archive",
                "accounts": {"owner": {"user": "own_bootstrap"}, "ddl": {"user": "own_ddl"}},
                "infrastructure_objects": [],
            },
        )
    assert c.execute.call_count == 1


def test_changed_runtime_asset_binding_is_rejected(monkeypatch):
    monkeypatch.setattr(assets, "collect", lambda **kwargs: {"digest": "new"})
    with pytest.raises(security.RecoveryError, match="ASSET_BINDING_CHANGED"):
        assets.verify({"digest": "old"})


def test_reader_effective_membership_not_just_name(monkeypatch):
    monkeypatch.setattr(security, "verify", lambda *args, **kwargs: None)
    c = Mock()
    c.execute.return_value.__iter__ = lambda _: iter([("backup",), ("tender_model_evaluate",)])
    with pytest.raises(security.RecoveryError, match="MEMBERSHIP_NOT_EXCLUSIVE"):
        archive.reader_guard(c, {"backup_group": "backup"})


def fixture():
    return {"ids": {k: str(uuid4()) for k in ("bid", "knowledge", "report")}}


def request(app, method, path, body=b"{}"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "method": method, "path": path, "headers": []}, receive, send))
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/operations"),
        ("GET", "/api/v1/reconcile"),
        ("GET", "/api/v1/knowledge"),
        ("PATCH", "/api/v1/bids/any/settings"),
    ],
)
def test_paused_harness_rejects_nonallowlisted_routes(method, path):
    app = application.PausedApplication(Mock(), fixture())
    assert request(app, method, path) == 423


def test_paused_harness_only_allows_frozen_body_and_new_uuid():
    app = application.PausedApplication(Mock(), fixture())
    assert request(app, "POST", "/api/v1/bids", b'{"title":"another work"}') == 423
    new_id = str(uuid4())
    app.bind_new_bid(new_id)
    assert (
        request(app, "PATCH", "/api/v1/bids/" + new_id + "/settings", b'{"auto_review":true}')
        == 423
    )
    with pytest.raises(security.RecoveryError):
        app.bind_new_bid(str(uuid4()))
