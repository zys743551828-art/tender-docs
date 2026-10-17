"""Fresh bootstrap safety boundaries do not need cloud credentials or mutations."""

from pathlib import Path

import pytest

from deploy import bootstrap_fresh as fresh


def test_all_reviewed_extensions_are_validated_before_any_database_connection(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must reject extension bytes before connecting or creating")

    monkeypatch.setattr(fresh.psycopg, "connect", forbidden)
    good = fresh.reviewed_sql(fresh.ROOT / "packages/knowledge/sql/rag.sql")
    with pytest.raises(fresh.BootstrapError, match="DIGEST_MISMATCH"):
        fresh.bootstrap(
            base_profile=Path("does-not-need-to-exist"),
            name="probe",
            extensions=[
                (fresh.ROOT / good.path, good.sha256),
                (fresh.ROOT / "deploy/sql/model-governance.sql", "0" * 64),
            ],
        )


def test_ordered_plan_freezes_bytes_and_rejects_duplicate_and_private_sql():
    one = fresh.reviewed_sql(fresh.ROOT / "packages/knowledge/sql/rag.sql")
    two = fresh.reviewed_sql(fresh.ROOT / "deploy/sql/model-governance.sql")
    plan = fresh.sql_plan(
        [(fresh.ROOT / one.path, one.sha256), (fresh.ROOT / two.path, two.sha256)]
    )
    assert [p.path for p in plan[-2:]] == [one.path, two.path]
    assert plan[-2].content == one.content and plan[-1].content == two.content
    with pytest.raises(fresh.BootstrapError, match="DUPLICATE"):
        fresh.sql_plan([(fresh.ROOT / one.path, one.sha256)] * 2)
    with pytest.raises(fresh.BootstrapError, match="PUBLIC_REPOSITORY"):
        fresh.reviewed_sql(fresh.ROOT / ".local/private.sql", "0" * 64)


def test_business_environment_excludes_owner_and_both_purpose_roles():
    values = {
        key: "synthetic"
        for key in (
            "OWNER_DATABASE_DSN",
            "POSTGRES_PASSWORD",
            "MODEL_TRAIN_DATABASE_DSN",
            "MODEL_EVALUATE_DATABASE_DSN",
            "WORKBENCH_ENV_FILE",
            "API_DATABASE_DSN",
            "WORKER_DATABASE_DSN",
        )
    }
    assert set(fresh.business_configuration(values)) == {"API_DATABASE_DSN", "WORKER_DATABASE_DSN"}


def test_profile_must_be_private_regular_file(tmp_path):
    path = tmp_path / "unsafe.env"
    path.write_text("OWNER_DATABASE_DSN=synthetic\n")
    path.chmod(0o644)
    with pytest.raises(fresh.BootstrapError, match="PRIVATE_600"):
        fresh.private_configuration(path)
    path.chmod(0o600)
    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(fresh.BootstrapError, match="PRIVATE_600"):
        fresh.private_configuration(link)


def test_immutable_execution_plan_does_not_reread_changed_sql(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setattr(fresh, "ROOT", tmp_path)
    monkeypatch.setattr(fresh, "BASE_SQL", ())
    path = tmp_path / "approved.sql"
    path.write_bytes(b"SELECT 1;")
    plan = fresh.sql_plan([(path, hashlib.sha256(path.read_bytes()).hexdigest())])
    path.write_bytes(b"SELECT 2;")
    assert plan[0].content == b"SELECT 1;"


@pytest.mark.parametrize("missing", [True, False])
def test_missing_capability_or_existing_database_never_creates_or_grants(
    tmp_path, monkeypatch, missing
):
    from uuid import uuid4

    profile = tmp_path / "base.env"
    profile.write_text(
        "OWNER_DATABASE_DSN=postgresql://synthetic:synthetic@127.0.0.1:1/postgres\nOBJECT_STORE_PREFIX=synthetic/\n"
    )
    profile.chmod(0o600)
    statements = []

    class Admin:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params=()):
            statements.append(str(query))
            if "rolcanlogin" in str(query):
                rows = [] if missing else [(group, False, False, False) for group in fresh.GROUPS]
            else:
                rows = [(1,)]
            return type(
                "Result",
                (),
                {"fetchall": lambda self: rows, "fetchone": lambda self: rows[0] if rows else None},
            )()

    monkeypatch.setattr(fresh.psycopg, "connect", lambda *a, **k: Admin())
    with pytest.raises(
        fresh.BootstrapError, match="CAPABILITY_GROUP" if missing else "EXISTING_DATABASE"
    ):
        fresh.bootstrap(base_profile=profile, name="guard_" + uuid4().hex[:8])
    assert all(
        not any(word in statement for word in ("CREATE ", "GRANT ", "ALTER "))
        for statement in statements
    )


def test_public_origin_configuration_changes_only_origin():
    values = {
        "APP_ORIGIN": "http://127.0.0.1:5175",
        "OBJECT_STORE_PREFIX": "synthetic/",
        "OWNER_DATABASE_DSN": "synthetic-private",
    }
    result = fresh.with_app_origin(values, "http://127.0.0.1:5176")
    assert result["APP_ORIGIN"] == "http://127.0.0.1:5176"
    assert {k for k in values if values[k] != result[k]} == {"APP_ORIGIN"}
    assert values["APP_ORIGIN"] == "http://127.0.0.1:5175"


@pytest.mark.parametrize(
    "origin", ["https://example.com", "http://private@127.0.0.1:5176", "http://127.0.0.1:5176/path"]
)
def test_check_origin_rejects_remote_or_credentialed_or_path_origin(origin):
    with pytest.raises(fresh.BootstrapError, match="LOOPBACK_HTTP_PORT"):
        fresh.with_app_origin({}, origin)
