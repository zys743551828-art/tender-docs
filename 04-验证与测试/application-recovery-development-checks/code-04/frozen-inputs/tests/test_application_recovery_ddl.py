"""AR04: nonpinned temporary DDL owner and preservation of initdb infrastructure."""

from unittest.mock import Mock

import pytest

from deploy.application_recovery import cluster, security


def specification():
    return {
        "database": "own_new_db",
        "owner_label": "archive_owner",
        "infrastructure_objects": [],
        "accounts": {
            "owner": {"user": "initdb_infrastructure"},
            "ddl": {"user": "temporary_business_ddl", "password": "synthetic-private"},
        },
    }


def test_initial_bootstrap_cannot_be_used_as_ddl_owner():
    value = specification()
    value["accounts"]["ddl"]["user"] = value["accounts"]["owner"]["user"]
    c = Mock()
    with pytest.raises(security.RecoveryError, match="INITIAL_BOOTSTRAP_CANNOT_BE_DDL"):
        cluster.assert_ddl_role(c, value)
    c.execute.assert_not_called()


@pytest.mark.parametrize(
    "row",
    [
        (10, True, True, False),
        (16390, True, True, True),
        (16390, False, True, False),
        (16390, True, False, False),
        None,
    ],
)
def test_pinned_system_or_noninitialization_role_is_rejected(row):
    c = Mock()
    c.execute.return_value.fetchone.return_value = row
    with pytest.raises(security.RecoveryError, match="NONPINNED_TEMPORARY"):
        cluster.assert_ddl_role(c, specification())
    assert c.execute.call_count == 1


def test_created_role_can_be_used_for_temporary_initialization():
    c = Mock()
    c.execute.return_value.fetchone.return_value = (16390, True, True, False)
    cluster.assert_ddl_role(c, specification())


def test_reassign_changes_only_own_new_database_and_temp_role(monkeypatch):
    monkeypatch.setattr(security, "verify", lambda *args, **kwargs: None)
    monkeypatch.setattr(cluster, "infrastructure_inventory", lambda *args: [])
    c = Mock()
    role, before, assignment, after = [Mock() for _ in range(4)]
    role.fetchone.return_value = (16390, True, True, False)
    before.fetchall.return_value = [("database", "own_new_db")]
    after.fetchall.return_value = [("database", "own_new_db")]
    c.execute.side_effect = [role, before, assignment, after]
    value = specification()
    cluster.reassign(c, value)
    statement = str(c.execute.call_args_list[2].args[0])
    assert "temporary_business_ddl" in statement and "archive_owner" in statement
    assert "initdb_infrastructure" not in statement
    assert value["reassigned_shared_objects"] == [["database", "own_new_db"]]


@pytest.mark.parametrize(
    "unexpected",
    [("database", "postgres"), ("tablespace", "pg_default"), ("database", "someone_elses_db")],
)
def test_unknown_shared_object_blocks_reassign_before_ddl(unexpected, monkeypatch):
    monkeypatch.setattr(security, "verify", lambda *args, **kwargs: None)
    monkeypatch.setattr(cluster, "infrastructure_inventory", lambda *args: [])
    c = Mock()
    role, before = Mock(), Mock()
    role.fetchone.return_value = (16390, True, True, False)
    before.fetchall.return_value = [("database", "own_new_db"), unexpected]
    c.execute.side_effect = [role, before]
    with pytest.raises(security.RecoveryError, match="UNKNOWN_SHARED_OBJECT"):
        cluster.reassign(c, specification())
    assert c.execute.call_count == 2


def test_default_infrastructure_owner_change_is_not_normalized_away():
    c = Mock()
    c.execute.return_value.fetchall.return_value = [("database", "postgres", "archive_owner")]
    with pytest.raises(security.RecoveryError, match="INFRASTRUCTURE_DEFAULT_OWNERSHIP_CHANGED"):
        cluster.infrastructure_inventory(c, specification())


def test_temporary_ddl_demoted_disabled_password_removed_and_no_ownership(monkeypatch):
    monkeypatch.setattr(security, "verify", lambda *args, **kwargs: None)
    monkeypatch.setattr(cluster, "infrastructure_inventory", lambda *args: [])
    c = Mock()
    role, ownership, alteration, flags = [Mock() for _ in range(4)]
    role.fetchone.return_value = (16390, True, True, False)
    ownership.fetchone.return_value = (False,)
    flags.fetchone.return_value = (False, False, False, False, False, False)
    c.execute.side_effect = [role, ownership, alteration, flags]
    value = specification()
    cluster.finish_ddl(c, value)
    assert value["temporary_ddl_disabled_and_nonowner"]
    assert "password" not in value["accounts"]["ddl"]
    statement = str(c.execute.call_args_list[2].args[0])
    assert "NOLOGIN NOSUPERUSER" in statement and "PASSWORD NULL" in statement
    assert "initdb_infrastructure" not in statement
