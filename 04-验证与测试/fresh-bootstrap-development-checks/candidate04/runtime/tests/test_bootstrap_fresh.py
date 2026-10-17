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
            "MODEL_SCORE_DATABASE_DSN",
            "ASSISTANT_BUDGET_ADMIN_DATABASE_DSN",
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
            if "current_database(),current_user" in str(query):
                rows = [("postgres", "synthetic")]
            elif "rolcanlogin" in str(query):
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


@pytest.mark.parametrize(
    "suffix",
    [
        "?dbname=tender_existing_release",
        "?user=existing_owner",
        "?password=other",
        "?host=other",
        "?service=other",
        "#dbname=existing",
    ],
)
def test_query_and_fragment_overrides_are_rejected_before_any_connect(
    tmp_path, monkeypatch, suffix
):
    def forbidden(*args, **kwargs):
        pytest.fail("unsafe URI must never reach libpq connect")

    monkeypatch.setattr(fresh.psycopg, "connect", forbidden)
    profile = tmp_path / "base.env"
    fresh.write_private(
        profile,
        {
            "OWNER_DATABASE_DSN": "postgresql://synthetic:synthetic@127.0.0.1:1/postgres" + suffix,
            "OBJECT_STORE_PREFIX": "synthetic/",
        },
    )
    with pytest.raises(fresh.BootstrapError, match="WITHOUT_QUERY"):
        fresh.bootstrap(base_profile=profile, name="query_guard")


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@host1,host2/db",
        "postgresql://u:p@host1:1,host2:2/db",
        "postgresql://u:p@127.0.0.1:0/db",
        "postgresql://u:p@127.0.0.1:65536/db",
    ],
)
def test_multiple_hosts_or_invalid_port_are_rejected(dsn):
    with pytest.raises(fresh.BootstrapError, match="SINGLE_HOST_URI"):
        fresh.owner_parameters(dsn)


def test_offline_libpq_counterexample_and_canonical_percent_identity():
    from psycopg.conninfo import conninfo_to_dict

    broken = "postgresql://synthetic:synthetic@127.0.0.1:1/tender_checks_synthetic?dbname=tender_existing_release&user=old_login&password=old_password"
    assert conninfo_to_dict(broken)["dbname"] == "tender_existing_release"
    assert conninfo_to_dict(broken)["user"] == "old_login"
    with pytest.raises(fresh.BootstrapError):
        fresh.owner_parameters(broken)
    owner = fresh.owner_parameters("postgresql://own%65r:p%40ss@127.0.0.1:55441/seed")
    assert owner["user"] == "owner" and owner["password"] == "p@ss"
    derived = fresh.identity_dsn(
        owner, database="tender_checks_synthetic", user="new login", password="new@pass"
    )
    actual = conninfo_to_dict(derived)
    assert (
        actual["dbname"] == "tender_checks_synthetic"
        and actual["user"] == "new login"
        and actual["password"] == "new@pass"
    )
    assert "?" not in derived and "#" not in derived


@pytest.mark.parametrize(
    "observed", [("existing_release", "owner"), ("expected", "wrong_user"), None]
)
def test_connected_identity_mismatch_prevents_any_following_ddl(observed):
    statements = []

    class Fake:
        def execute(self, statement):
            statements.append(statement)
            return type("Result", (), {"fetchone": lambda self: observed})()

    with pytest.raises(fresh.BootstrapError, match="MISMATCH"):
        fresh.verify_connection(Fake(), database="expected", user="owner")
    assert statements == ["SELECT current_database(),current_user"]


@pytest.mark.parametrize(
    "flags,owns",
    [((True, False, False, False, False), False), ((False, False, False, False, False), True)],
)
def test_created_login_must_be_restricted_and_nonowner_before_profile(flags, owns):
    class Fake:
        def execute(self, statement):
            row = (
                ("new_db", "new_login")
                if "current_database" in statement
                else (flags if "rolsuper" in statement else (owns,))
            )
            return type("Result", (), {"fetchone": lambda self: row})()

    with pytest.raises(fresh.BootstrapError, match="NOT_RESTRICTED|OWNS_BUSINESS"):
        fresh.verify_connection(Fake(), database="new_db", user="new_login", restricted=True)


@pytest.mark.parametrize("where", ["admin", "fresh"])
def test_bootstrap_blocks_misdirected_connections_before_target_ddl(tmp_path, monkeypatch, where):
    monkeypatch.setattr(fresh, "ROOT", tmp_path)
    monkeypatch.setattr(
        fresh,
        "sql_plan",
        lambda _: (
            fresh.ReviewedSql("approved.sql", "a" * 64, b"CREATE TABLE must_not_execute(id int)"),
        ),
    )
    profile = tmp_path / "base.env"
    fresh.write_private(
        profile,
        {
            "OWNER_DATABASE_DSN": "postgresql://synthetic:synthetic@127.0.0.1:1/seed",
            "OBJECT_STORE_PREFIX": "synthetic/",
        },
    )
    connections = []

    class Fake:
        def __init__(self, index):
            self.index = index
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement, params=()):
            self.statements.append(str(statement))
            if "current_database(),current_user" in str(statement):
                row = (
                    ("existing_release", "synthetic")
                    if self.index == (0 if where == "admin" else 1)
                    else ("postgres", "synthetic")
                )
                rows = [row]
            elif "rolcanlogin" in str(statement):
                rows = [(group, False, False, False) for group in fresh.GROUPS]
            else:
                rows = []
            return type(
                "Result",
                (),
                {"fetchone": lambda self: rows[0] if rows else None, "fetchall": lambda self: rows},
            )()

    def connect(*args, **kwargs):
        c = Fake(len(connections))
        connections.append(c)
        return c

    monkeypatch.setattr(fresh.psycopg, "connect", connect)
    with pytest.raises(fresh.BootstrapError, match="MISMATCH"):
        fresh.bootstrap(base_profile=profile, name="identity_probe")
    target = connections[0 if where == "admin" else 1]
    assert target.statements == ["SELECT current_database(),current_user"]
    assert not (tmp_path / ".local/identity_probe-checks.env").exists()


@pytest.mark.parametrize("paths", [(), ("deploy/sql/model-governance.sql",)])
def test_score_requires_both_reviewed_sql_before_profile_or_connection(monkeypatch, paths):
    monkeypatch.setattr(
        fresh,
        "sql_plan",
        lambda _: tuple(fresh.ReviewedSql(p, "a" * 64, b"SELECT 1") for p in paths),
    )

    def forbidden(*args, **kwargs):
        pytest.fail(
            "missing reviewed prerequisites must fail before connection or private profile read"
        )

    monkeypatch.setattr(fresh.psycopg, "connect", forbidden)
    monkeypatch.setattr(fresh, "private_configuration", forbidden)
    with pytest.raises(fresh.BootstrapError, match="REQUIRE"):
        fresh.bootstrap(base_profile=Path("absent"), name="score_guard", model_score_purpose=True)


@pytest.mark.parametrize("budget_admin", [False, True])
@pytest.mark.parametrize(
    "train_evaluate,score", [(False, False), (True, False), (False, True), (True, True)]
)
def test_fresh_purpose_logins_and_private_profiles_are_separate(
    tmp_path, monkeypatch, train_evaluate, score, budget_admin
):
    from contextlib import nullcontext

    from psycopg.conninfo import conninfo_to_dict

    monkeypatch.setattr(fresh, "ROOT", tmp_path)
    paths = (
        "deploy/sql/model-governance.sql",
        "deploy/sql/score-execution.sql",
        "packages/assistant/sql/intent-budget.sql",
    )
    monkeypatch.setattr(
        fresh,
        "sql_plan",
        lambda _: tuple(fresh.ReviewedSql(p, "a" * 64, b"SELECT 1") for p in paths),
    )
    profile = tmp_path / "base.env"
    fresh.write_private(
        profile,
        {
            "OWNER_DATABASE_DSN": "postgresql://synthetic:synthetic@127.0.0.1:1/seed",
            "OBJECT_STORE_PREFIX": "synthetic/",
        },
    )
    statements = []
    identities = []

    class Fake:
        def __init__(self, dsn):
            self.identity = conninfo_to_dict(dsn)
            identities.append(self.identity)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def transaction(self):
            return nullcontext()

        def execute(self, statement, params=()):
            rendered = statement if isinstance(statement, str) else statement.as_string(None)
            statements.append(rendered)
            if "current_database(),current_user" in rendered:
                rows = [(self.identity["dbname"], self.identity["user"])]
            elif "rolcanlogin" in rendered and "WHERE rolname=ANY" in rendered:
                rows = [(group, False, False, False, False, False, False) for group in params[0]]
            elif "WHERE rolname=%s" in rendered:
                rows = [(False,) * 6]
            elif "rolsuper,rolbypassrls" in rendered:
                rows = [(False,) * 5]
            elif "SELECT EXISTS" in rendered:
                rows = [(False,)]
            else:
                rows = []
            return type(
                "Result",
                (),
                {
                    "fetchone": lambda self: rows[0] if rows else None,
                    "fetchall": lambda self: rows,
                },
            )()

    monkeypatch.setattr(fresh.psycopg, "connect", lambda dsn, **_: Fake(dsn))
    result = fresh.bootstrap(
        base_profile=profile,
        name="purpose_probe",
        model_purposes=train_evaluate,
        model_score_purpose=score,
        assistant_budget_admin=budget_admin,
    )
    expected = (
        *fresh.CAPABILITIES,
        *(fresh.PURPOSES if train_evaluate else ()),
        *(("model_score",) if score else ()),
        *(("assistant_budget_admin",) if budget_admin else ()),
    )
    assert set(result["logins"]) == set(expected)
    assert all(item["dbname"] == "tender_checks_purpose_probe" for item in identities[1:])
    assert len([s for s in statements if s.startswith("CREATE ROLE")]) == len(expected)
    business = fresh.file_configuration(tmp_path / result["profiles"]["business"])
    assert not any(
        key in business
        for key in (
            "OWNER_DATABASE_DSN",
            "MODEL_TRAIN_DATABASE_DSN",
            "MODEL_EVALUATE_DATABASE_DSN",
            "MODEL_SCORE_DATABASE_DSN",
            "ASSISTANT_BUDGET_ADMIN_DATABASE_DSN",
        )
    )
    for purpose in set(expected) - set(fresh.CAPABILITIES):
        path = tmp_path / result["profiles"][purpose]
        values = fresh.file_configuration(path)
        assert path.stat().st_mode & 0o777 == 0o600
        assert set(values) == {purpose.upper() + "_DATABASE_DSN"}
        assert conninfo_to_dict(next(iter(values.values())))["user"] == result["logins"][purpose]
        assert any(
            s == f'GRANT "tender_{purpose}" TO "{result["logins"][purpose]}"' for s in statements
        )


def test_budget_admin_requires_reviewed_sql_before_profile_or_connection(monkeypatch):
    monkeypatch.setattr(fresh, "sql_plan", lambda _: ())

    def forbidden(*args, **kwargs):
        pytest.fail("admin prerequisite must reject before private file or connection")

    monkeypatch.setattr(fresh, "private_configuration", forbidden)
    monkeypatch.setattr(fresh.psycopg, "connect", forbidden)
    with pytest.raises(fresh.BootstrapError, match="ADMIN_REQUIRES_REVIEWED_BUDGET_SQL"):
        fresh.bootstrap(
            base_profile=Path("absent"), name="admin_guard", assistant_budget_admin=True
        )


@pytest.mark.parametrize(
    "flags",
    [
        None,
        *((True,) + (False,) * 5,),
        (False, True, False, False, False, False),
        (False, False, True, False, False, False),
        (False, False, False, True, False, False),
        (False, False, False, False, True, False),
        (False, False, False, False, False, True),
    ],
)
def test_budget_admin_unsafe_group_blocks_login_creation(tmp_path, monkeypatch, flags):
    from psycopg.conninfo import conninfo_to_dict

    monkeypatch.setattr(fresh, "ROOT", tmp_path)
    monkeypatch.setattr(
        fresh,
        "sql_plan",
        lambda _: (
            fresh.ReviewedSql("packages/assistant/sql/intent-budget.sql", "a" * 64, b"SELECT 1"),
        ),
    )
    base = tmp_path / "base.env"
    fresh.write_private(
        base,
        {
            "OWNER_DATABASE_DSN": "postgresql://synthetic:synthetic@127.0.0.1:1/seed",
            "OBJECT_STORE_PREFIX": "synthetic/",
        },
    )
    statements = []

    class Fake:
        def __init__(self, dsn):
            self.identity = conninfo_to_dict(dsn)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement, params=()):
            rendered = statement if isinstance(statement, str) else statement.as_string(None)
            statements.append(rendered)
            if "current_database(),current_user" in rendered:
                rows = [(self.identity["dbname"], self.identity["user"])]
            elif "WHERE rolname=ANY" in rendered and "rolcanlogin" in rendered:
                rows = [(g, False, False, False, False, False, False) for g in params[0]]
            elif "WHERE rolname=%s" in rendered:
                rows = [] if flags is None else [flags]
            else:
                rows = []
            return type(
                "Result",
                (),
                {"fetchone": lambda self: rows[0] if rows else None, "fetchall": lambda self: rows},
            )()

    monkeypatch.setattr(fresh.psycopg, "connect", lambda dsn, **_: Fake(dsn))
    with pytest.raises(fresh.BootstrapError, match="BUDGET_ADMIN_NOLOGIN_GROUP_MISSING_OR_UNSAFE"):
        fresh.bootstrap(base_profile=base, name="admin_guard", assistant_budget_admin=True)
    assert not any(s.startswith("CREATE ROLE") or s.startswith("GRANT ") for s in statements)
    assert not (tmp_path / ".local/admin_guard-checks.env").exists()
    assert not (tmp_path / ".local/admin_guard-assistant_budget_admin.env").exists()
