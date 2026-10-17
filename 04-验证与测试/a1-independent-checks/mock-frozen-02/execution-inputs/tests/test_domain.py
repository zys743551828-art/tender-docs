from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.api.auth import password_hash, password_ok
from packages.domain.runtime import (
    FORMAT_CHECKER,
    DomainError,
    clean,
    decode_cursor,
    encode_cursor,
    etag,
    match,
    validate,
)


def test_password_salted_and_wrong_password_rejected():
    encoded = password_hash("correct-password-123")
    assert password_ok("correct-password-123", encoded)
    assert not password_ok("wrong-password-123", encoded)
    assert encoded != password_hash("correct-password-123")


def test_validation_does_not_echo_input():
    marker = "secret-must-not-appear"
    with pytest.raises(DomainError) as error:
        validate("LoginRequest", {"login_name": "alice", "password": [marker]})
    assert marker not in str(error.value.details)


def test_etag_preserves_resource_identity():
    identity = uuid4()
    with pytest.raises(DomainError) as error:
        match(etag("upload", identity, 3), "bid", identity, 3)
    assert error.value.status == 412


def test_cursor_rejects_tampering(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "synthetic-cursor-test-secret")
    row = {"created_at": datetime.now(timezone.utc), "id": uuid4()}
    cursor = encode_cursor(row)
    assert decode_cursor(cursor) == (row["created_at"], row["id"])
    with pytest.raises(DomainError):
        decode_cursor(cursor + "tampered")


def test_protocol_formats_are_enforced_and_datetime_is_rfc3339():
    assert not FORMAT_CHECKER.conforms("relative/download", "uri")
    assert not FORMAT_CHECKER.conforms("invalid-date", "date-time")
    assert not FORMAT_CHECKER.conforms("invalid-uuid", "uuid")
    assert FORMAT_CHECKER.conforms(clean(datetime.now(timezone.utc)), "date-time")
