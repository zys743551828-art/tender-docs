"""Regression for the real 23502 fixture failure and pre-PUT durable key records."""

import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from deploy.application_recovery import fixture, seed_retry
from deploy.application_recovery import security as s


class StopAtReviewInput(Exception):
    pass


def test_seed_covers_current_required_columns_and_records_all_keys_before_put(
    monkeypatch, tmp_path
):
    saved, puts = {}, []
    monkeypatch.setattr(fixture.binding, "require", lambda: None)
    monkeypatch.setattr(s, "save", lambda path, value: saved.__setitem__(Path(path).name, value))
    port = Mock()

    def put(**kwargs):
        intent = saved["object-put-intent.json"]
        assert len(intent["objects"]) == 7
        assert kwargs["Key"] in {item["key"] for item in intent["objects"].values()}
        puts.append(kwargs["Key"])

    port.put_object.side_effect = put
    monkeypatch.setattr(fixture.archive, "object_port", lambda _: port)
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(fixture.psycopg, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(s, "connection_dsn", lambda *args: "synthetic-only")
    schema = Path(
        "/workspace/tender-docs/04-验证与测试/historical-input-initialization-checks/candidate02/sql-inputs/packages/contracts/sql/domain.sql"
    ).read_text()
    current_required = set(
        re.findall(r"ALTER TABLE review_inputs ADD COLUMN (\w+) [^;]*NOT NULL", schema)
    )
    assert len(current_required) == 4

    def execute(query, params=None):
        if query.startswith("INSERT INTO review_inputs("):
            columns = set(query.split("(", 1)[1].split(")", 1)[0].split(","))
            assert current_required <= columns
            assert query.count("%s") == len(params)
            assert all(value is not None for value in params)
            assert str(params[7]) == saved["object-put-intent.json"]["objects"]["chapter"]["id"]
            raise StopAtReviewInput()

    connection.execute.side_effect = execute
    with pytest.raises(StopAtReviewInput):
        fixture.create({}, {"OBJECT_STORE_BUCKET": "synthetic-only"}, tmp_path)
    assert len(puts) == 7 and len(set(puts)) == 7


@pytest.mark.parametrize("rows", [2, 1])
def test_retry_refuses_any_existing_business_row(rows, monkeypatch):
    monkeypatch.setattr(seed_retry.archive, "reader_guard", lambda *args: None)
    monkeypatch.setattr(
        seed_retry.archive.generic,
        "inventory",
        lambda _: [{"schema": "design", "name": "members", "rows": rows}],
    )
    with pytest.raises(s.RecoveryError, match="EMPTY_SYNTHETIC_SOURCE"):
        seed_retry.require_empty(None, {"temporary_ddl_disabled_and_nonowner": True})


def test_retry_requires_completed_temporary_role_demotion(monkeypatch):
    monkeypatch.setattr(seed_retry.archive, "reader_guard", lambda *args: None)
    monkeypatch.setattr(
        seed_retry.archive.generic,
        "inventory",
        lambda _: [{"schema": "recovery_control", "name": "identity", "rows": 1}],
    )
    with pytest.raises(s.RecoveryError, match="FINALIZED_SOURCE_INITIALIZATION"):
        seed_retry.require_empty(None, {"temporary_ddl_disabled_and_nonowner": False})
