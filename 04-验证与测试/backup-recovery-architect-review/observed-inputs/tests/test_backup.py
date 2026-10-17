"""Archive corruption boundaries and opt-in real concurrent snapshot/restore drill."""

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from deploy.backups import run


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(run, "ARCHIVE_ROOT", root)
    target = root / "test"
    target.mkdir(mode=0o700)
    (target / "objects").mkdir(mode=0o700)
    blob_id = str(uuid4())
    data = b"synthetic archive bytes"
    manifest = {
        "format": "tender-snapshot-backup-1",
        "database_sha256": run.digest(b"synthetic-dump"),
        "objects": [
            {
                "id": blob_id,
                "file": f"objects/{blob_id}.bin",
                "bytes": len(data),
                "sha256": run.digest(data),
            }
        ],
    }
    run.private_write(target / f"objects/{blob_id}.bin", data)
    run.private_write(target / "database.dump", b"synthetic-dump")
    run.private_write(target / "manifest.json", json.dumps(manifest).encode())
    run.private_write(
        target / "manifest.sha256", run.digest((target / "manifest.json").read_bytes()).encode()
    )
    return target, manifest


def test_tampered_database_is_rejected_before_restore(archive):
    target, _ = archive
    (target / "database.dump").write_bytes(b"modified bytes")
    with pytest.raises(run.BackupError, match="DATABASE_CHECKSUM_MISMATCH"):
        run.load_archive(target)


def test_tampered_object_is_rejected_before_restore(archive):
    target, manifest = archive
    (target / manifest["objects"][0]["file"]).write_bytes(b"unexpected")
    with pytest.raises(run.BackupError, match="ARCHIVE_OBJECT_CHECKSUM_MISMATCH"):
        run.load_archive(target)


def test_archive_symlink_escape_rejected(archive, tmp_path):
    target, manifest = archive
    original = target / manifest["objects"][0]["file"]
    outside = tmp_path / "outside"
    outside.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(outside)
    with pytest.raises(run.BackupError, match="OBJECT_PATH_INVALID"):
        run.load_archive(target)


def test_public_archive_permissions_rejected(archive):
    target, _ = archive
    target.chmod(0o755)
    with pytest.raises(run.BackupError, match="ARCHIVE_PERMISSIONS"):
        run.load_archive(target)


def test_snapshot_restore_with_concurrent_committed_source_write():
    profile = os.environ.get("BACKUP_INTEGRATION_ENV_FILE")
    if not profile:
        pytest.skip("explicit isolated real backup/restore profile required")
    values = run.configuration(Path(profile))
    os.environ.update(values)
    os.environ["WORKBENCH_ENV_FILE"] = profile
    root = run.ROOT / ".local/backups"
    root.mkdir(mode=0o700, exist_ok=True)
    target = root / ("snapshot-concurrency-" + uuid4().hex)
    marker = uuid4()
    with psycopg.connect(values["API_DATABASE_DSN"]) as before:
        original = before.execute("SELECT count(*) FROM design.members").fetchone()[0]

    def concurrent_commit():
        # A separate real DB session commits while the exporting transaction remains open.
        with psycopg.connect(values["API_DATABASE_DSN"]) as writer:
            writer.execute(
                "INSERT INTO design.members(id,display_name) VALUES(%s,%s)",
                (marker, "Synthetic backup concurrent snapshot probe"),
            )

    manifest = run.create(values, target, "tender-workbench-external-postgres-1", concurrent_commit)
    assert manifest["tables"]["design.members"]["rows"] == original
    receipt = run.restore_verify(values, target, "tender-workbench-external-postgres-1")
    with psycopg.connect(values["API_DATABASE_DSN"]) as source:
        assert (
            source.execute("SELECT count(*) FROM design.members WHERE id=%s", (marker,)).fetchone()[
                0
            ]
            == 1
        )
    restored_dsn = make_conninfo(values["API_DATABASE_DSN"], dbname=receipt["database"])
    with psycopg.connect(restored_dsn) as restored:
        assert restored.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert (
            restored.execute(
                "SELECT count(*) FROM design.members WHERE id=%s", (marker,)
            ).fetchone()[0]
            == 0
        )
        assert restored.execute("SELECT count(*) FROM design.members").fetchone()[0] == original
    proof = {
        **receipt,
        "source_concurrent_commit_visible": True,
        "post_snapshot_commit_excluded_from_manifest_and_restored_dump": True,
        "source_services_paused": False,
    }
    run.private_write(target / "concurrency-proof.json", json.dumps(proof, indent=2).encode())
