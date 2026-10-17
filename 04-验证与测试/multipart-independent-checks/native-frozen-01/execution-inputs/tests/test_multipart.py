"""分片边界：票据不能换会话/片号，状态元数据拒绝误解释。"""

from datetime import timedelta
from uuid import uuid4

import pytest

from apps.api.object_transfer import _verify
from packages.domain.runtime import DomainError, now, validate
from packages.uploads import service
from packages.uploads.protocol import PART_SIZE, check_parts, layout, pack, unpack


def test_layout_and_complete_receipts_reject_missing_duplicate_or_wrong_size():
    size, count = layout(PART_SIZE + 19)
    row = {"declared_size": PART_SIZE + 19, "part_size": size, "part_count": count}
    good = [
        {"part_number": 1, "byte_size": PART_SIZE, "etag": "one"},
        {"part_number": 2, "byte_size": 19, "etag": "two"},
    ]
    assert check_parts(row, good, complete=True) == good
    for wrong in (good[:1], [good[0], good[0]], [good[0], {**good[1], "byte_size": 20}]):
        with pytest.raises(DomainError):
            check_parts(row, wrong, complete=True)
    with pytest.raises(DomainError):
        layout(PART_SIZE * 10000 + 1)


def test_control_state_has_explicit_version_and_strict_shape():
    key = "staging/synthetic/key"
    encoded = pack("ready", key=key, nonce=str(uuid4()), provider_id="synthetic-native-identifier")
    assert unpack(encoded, key)["phase"] == "ready"
    for invalid in (
        "ready",
        "synthetic-native-identifier",
        encoded.replace('"ready"', '"arbitrary"'),
        encoded.replace('"provider_id"', '"other_field"'),
    ):
        with pytest.raises(DomainError):
            unpack(invalid, key)
    with pytest.raises(DomainError):
        unpack(encoded, key + "other")


def test_grants_bind_session_method_part_size_revision_and_expiry(monkeypatch):
    monkeypatch.setenv("APP_ORIGIN", "https://workbench.test")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-multipart-secret-only")
    session = {"id": uuid4(), "member_id": uuid4(), "expires_at": now() + timedelta(hours=1)}
    row = {
        "id": uuid4(),
        "bid_id": uuid4(),
        "file_id": uuid4(),
        "staging_key": "staging/synthetic/key",
        "declared_digest": "a" * 64,
        "declared_size": PART_SIZE + 19,
        "part_size": PART_SIZE,
        "part_count": 2,
        "revision": 1,
        "expires_at": now() + timedelta(hours=24),
    }
    grant = service.grant(row, session, [1, 2])
    validate("UploadGrant", grant)
    assert grant["upload_url"] is None
    token = grant["parts"][0]["url"].split("ticket=", 1)[1]
    claims = service.claims(row, session, 1)
    _verify(token, claims)
    for altered in (
        {**claims, "method": "GET"},
        {**claims, "part": 2},
        {**claims, "revision": 2},
        {**claims, "size": 5},
        {**claims, "session": str(uuid4())},
        {**claims, "key": "other"},
    ):
        with pytest.raises(DomainError):
            _verify(token, altered)
    with pytest.raises(DomainError):
        service.grant(row, session, [1, 1])
    with pytest.raises(DomainError):
        service.grant(row, session, list(range(1, 130)))
