"""同源传输的授权、实际长度及发布竞态；对象端口使用显式合成适配器。"""

import asyncio
import hashlib
import io
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api import auth as authentication
from apps.api import object_transfer as transfer
from packages.domain.runtime import DomainError, now, validate

PDF = b"%PDF-1.7\nsynthetic bytes for transport only\n"
ORIGIN = "https://workbench.test"


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "synthetic-transfer-secret")
    monkeypatch.setenv("APP_ORIGIN", ORIGIN)
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "104857600")
    monkeypatch.setenv("OBJECT_TRANSFER_MAX_BYTES", "104857600")
    monkeypatch.setenv("OBJECT_TRANSFER_DEADLINE_SECONDS", "300")
    identity = uuid4()
    bid = uuid4()
    session = {
        "id": uuid4(),
        "member_id": uuid4(),
        "expires_at": now() + timedelta(hours=1),
        "csrf_digest": hashlib.sha256(b"synthetic-csrf").hexdigest(),
    }
    row = {
        "id": identity,
        "bid_id": bid,
        "batch_id": uuid4(),
        "staging_key": f"staging/{bid}/{identity}",
        "declared_size": len(PDF),
        "filename": "synthetic.pdf",
        "revision": 1,
        "state": "authorized",
        "aborted_at": None,
        "expires_at": now() + timedelta(hours=2),
    }
    data = SimpleNamespace(
        row=row,
        session=session,
        authenticated=True,
        batch_state="open",
        objects={},
        calls=[],
        before_put=None,
        temporary_keys=[],
        body=None,
        version=uuid4(),
        filename="中文 招标书\r\n.pdf",
        download_key="blobs/synthetic-hash",
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def execute(self, sql, params=()):
            data.calls.append(sql)
            if sql == "SHOW transaction_isolation":
                return Result({"transaction_isolation": "read committed"})
            if "SELECT p.bid_id,p.phase" in sql:
                return Result(
                    {"bid_id": bid, "phase": "working", "archived_at": None, "revision": 1}
                )
            if "to_regclass" in sql:
                return Result({"ready": False})
            if "SELECT * FROM upload_batches" in sql:
                return Result({"id": row["batch_id"], "bid_id": bid, "state": data.batch_state})
            if "JOIN members" in sql:
                return Result(dict(data.session) if data.authenticated else None)
            if "SELECT id FROM bids" in sql:
                return Result({"id": bid})
            if "SELECT state FROM upload_batches" in sql:
                return Result({"state": data.batch_state})
            if "UPDATE uploads SET staging_key" in sql:
                assert params[1] == identity and params[2] == row["staging_key"]
                row["staging_key"] = params[0]
                return Result(None)
            if "FROM document_versions" in sql:
                return Result(
                    {
                        "original_name": data.filename,
                        "object_key": data.download_key,
                        "byte_size": len(PDF),
                        "media_type": "application/pdf",
                    }
                )
            raise AssertionError("Unexpected SQL in explicit test adapter")

    @contextmanager
    def connection():
        yield Connection()

    def upload_row(c, upload_id, lock=False):
        if upload_id != identity:
            raise DomainError("NOT_FOUND", "未找到指定对象", 404)
        data.calls.append("upload_locked" if lock else "upload_unlocked")
        return dict(row)

    def put(key, value, media):
        assert "upload_locked" not in data.calls
        assert value == PDF and media == "application/pdf"
        if data.before_put:
            data.before_put()
        assert key not in data.objects
        data.objects[key] = value
        return {"ETag": '"synthetic-etag"'}

    def get_object(**kwargs):
        assert kwargs["Key"] == data.download_key
        data.body = io.BytesIO(PDF)
        return {"Body": data.body, "ContentLength": len(PDF)}

    monkeypatch.setattr(authentication, "connection", connection)
    monkeypatch.setattr(transfer, "connection", connection)
    monkeypatch.setattr(transfer, "upload_row", upload_row)
    monkeypatch.setattr(transfer, "_register_temporary_key", data.temporary_keys.append)
    monkeypatch.setattr(transfer.storage, "put", put)
    monkeypatch.setattr(transfer.storage, "bucket", lambda: "synthetic-private-bucket")
    monkeypatch.setattr(transfer.storage, "client", lambda: SimpleNamespace(get_object=get_object))
    app = FastAPI()
    app.include_router(transfer.router)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    data.client = TestClient(app)
    data.client.cookies.set(authentication.COOKIE, "synthetic-cookie")
    data.headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": "synthetic-csrf",
        "Content-Type": "application/pdf",
    }
    data.upload_url = transfer.make_upload_url(
        None,
        identity,
        session=session,
        bid_id=bid,
        staging_key=row["staging_key"],
        byte_size=len(PDF),
        revision=1,
        expires_at=row["expires_at"],
    )
    data.download_url = transfer.make_download_url(
        None,
        data.version,
        session=session,
        object_key=data.download_key,
        filename=data.filename,
        byte_size=len(PDF),
    )
    yield data
    data.client.close()


def test_ticket_never_discloses_locator_and_clamps_session_lifetime(harness):
    value = harness
    assert value.row["staging_key"] not in value.upload_url
    assert value.download_key not in value.download_url
    assert value.filename not in value.download_url
    stamp = int(parse_qs(urlsplit(value.upload_url).query)["ticket"][0].split(".")[0])
    assert stamp <= int((now() + timedelta(minutes=15)).timestamp())
    assert stamp <= int(value.session["expires_at"].timestamp())


@pytest.mark.parametrize("grant_kind", ["UploadGrant", "DownloadGrant"])
def test_grants_use_absolute_uri_and_validate_actual_contract(harness, grant_kind):
    value = harness
    expiry = (now() + timedelta(minutes=15)).isoformat()
    if grant_kind == "UploadGrant":
        grant = {
            "upload_id": str(value.row["id"]),
            "object_ref": str(value.row["id"]),
            "file_id": str(uuid4()),
            "mode": "single",
            "upload_url": value.upload_url,
            "part_size": len(PDF),
            "part_count": 1,
            "parts": [],
            "expires_at": expiry,
            "session_expires_at": value.row["expires_at"].isoformat(),
            "revision": 1,
        }
        field = "upload_url"
    else:
        grant = {
            "artifact_id": str(value.version),
            "download_url": value.download_url,
            "expires_at": expiry,
            "filename": value.filename,
            "artifact_kind": "source_original",
            "presentation_revision": None,
        }
        field = "download_url"
    assert grant[field].startswith(ORIGIN + "/api/v1/object-transfers/")
    validate(grant_kind, grant)
    # 回归反例：原先相对地址不能满足format:uri。
    relative = grant[field].removeprefix(ORIGIN)
    with pytest.raises(DomainError) as error:
        validate(grant_kind, grant | {field: relative})
    assert error.value.status == 422


@pytest.mark.parametrize(
    "origin",
    [
        "https://workbench.test",
        "http://127.0.0.1:5175",
        "http://localhost:5173",
        "http://[::1]:5175",
        "https://workbench.example:8443",
    ],
)
def test_configured_origin_supports_canonical_web_and_local_deployments(monkeypatch, origin):
    monkeypatch.setenv("APP_ORIGIN", origin)
    assert transfer._configured_origin() == origin


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "/relative",
        "//workbench.test",
        "ftp://workbench.test",
        "https://",
        "https://user:password@workbench.test",
        "https://workbench.test/path",
        "https://workbench.test/",
        "https://workbench.test?query=value",
        "https://workbench.test?",
        "https://workbench.test#fragment",
        "https://workbench.test#",
        " https://workbench.test",
        "https://workbench.test\r\n",
        "https://workbench.test:bad",
        "https://workbench.test:65536",
        "https://workbench.test:0",
        "https://workbench.test\\@attacker.test",
        "https://workbench.test%2f.attacker.test",
        "https://工作台.example",
        "https://WORKBENCH.test",
        "HTTPS://workbench.test",
        "https://workbench.test:443",
        "https://bad..test",
        "http://2130706433",
        "http://0x7f000001",
        "http://127.1",
        "http://example.123",
    ],
)
def test_malformed_or_noncanonical_origin_rejected_by_both_helpers(harness, monkeypatch, origin):
    value = harness
    monkeypatch.setenv("APP_ORIGIN", origin)
    with pytest.raises(DomainError) as upload_error:
        transfer.make_upload_url(
            None,
            value.row["id"],
            session=value.session,
            bid_id=value.row["bid_id"],
            staging_key=value.row["staging_key"],
            byte_size=len(PDF),
            revision=1,
            expires_at=value.row["expires_at"],
        )
    with pytest.raises(DomainError) as download_error:
        transfer.make_download_url(
            None,
            value.version,
            session=value.session,
            object_key=value.download_key,
            filename=value.filename,
            byte_size=len(PDF),
        )
    for error in (upload_error, download_error):
        assert error.value.code == "OBJECT_TRANSFER_CONFIG_INVALID" and error.value.status == 503
        assert origin not in error.value.message or origin == ""


def test_untrusted_host_and_forwarded_headers_cannot_control_transfer_origin(harness):
    value = harness
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("attacker.test", 443),
            "path": "/",
            "query_string": b"",
            "headers": [
                (b"host", b"attacker.test"),
                (b"x-forwarded-host", b"other-attacker.test"),
                (b"x-forwarded-proto", b"http"),
            ],
        }
    )
    upload_url = transfer.make_upload_url(
        request,
        value.row["id"],
        session=value.session,
        bid_id=value.row["bid_id"],
        staging_key=value.row["staging_key"],
        byte_size=len(PDF),
        revision=1,
        expires_at=value.row["expires_at"],
    )
    download_url = transfer.make_download_url(
        request,
        value.version,
        session=value.session,
        object_key=value.download_key,
        filename=value.filename,
        byte_size=len(PDF),
    )
    assert upload_url.startswith(ORIGIN + "/") and download_url.startswith(ORIGIN + "/")
    assert "attacker" not in upload_url and "attacker" not in download_url


@pytest.mark.parametrize(
    "change",
    [
        "missing_cookie",
        "wrong_origin",
        "missing_csrf",
        "expired_session",
        "another_session",
        "another_member",
    ],
)
def test_upload_requires_live_same_session_origin_and_csrf(harness, change):
    value = harness
    if change == "missing_cookie":
        value.client.cookies.clear()
    elif change == "wrong_origin":
        value.headers["Origin"] = "https://attacker.test"
    elif change == "missing_csrf":
        del value.headers["X-CSRF-Token"]
    elif change == "expired_session":
        value.authenticated = False
    elif change == "another_session":
        value.session["id"] = uuid4()
    else:
        value.session["member_id"] = uuid4()
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code in (401, 403)
    assert not value.objects


def test_put_uses_unique_key_then_short_lock_publish_and_old_ticket_cannot_replay(harness):
    value = harness
    original = value.row["staging_key"]
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code == 200
    assert response.headers["ETag"] == '"synthetic-etag"'
    new_key = value.row["staging_key"]
    assert new_key != original and new_key.startswith(original + "/transfers/")
    UUID(new_key.rsplit("/", 1)[1])
    assert value.objects[new_key] == PDF
    assert value.row["state"] == "authorized"  # 传输成功不冒称Worker校验通过。
    assert value.calls.index("SELECT id FROM bids WHERE id=%s FOR UPDATE") < value.calls.index(
        "upload_locked"
    )
    assert value.temporary_keys == [new_key]
    value.calls.clear()
    replay = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert replay.status_code == 403
    assert len(value.objects) == 1


@pytest.mark.parametrize(
    "race", ["complete", "abort", "refresh", "expiry", "batch_commit", "session_revoked"]
)
def test_state_change_during_network_cannot_publish_or_overwrite(harness, race):
    value = harness
    original = value.row["staging_key"]

    def racing_change():
        if race == "complete":
            value.row["state"] = "verifying"
        elif race == "abort":
            value.row["aborted_at"] = now()
        elif race == "refresh":
            value.row["revision"] += 1
        elif race == "expiry":
            value.row["expires_at"] = now() - timedelta(seconds=1)
        elif race == "batch_commit":
            value.batch_state = "committed"
        else:
            value.authenticated = False

    value.before_put = racing_change
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code in (401, 403, 409, 410)
    assert value.row["staging_key"] == original
    assert len(value.objects) == 1  # 仅本请求独立key成为待GC候选，无旧key/正式blob覆盖。
    assert original not in value.objects
    assert value.temporary_keys == list(value.objects)


@pytest.mark.parametrize(
    "case,status",
    [
        ("media", 415),
        ("encoding", 415),
        ("over_declared", 422),
        ("actual_overflow", 413),
        ("short", 422),
        ("missing_length", 411),
        ("not_pdf", 415),
        ("bad_key", 409),
    ],
)
def test_upload_limits_and_actual_content_checked_before_object_write(harness, case, status):
    value = harness
    content = PDF
    if case == "media":
        value.headers["Content-Type"] = "application/octet-stream"
    elif case == "encoding":
        value.headers["Content-Encoding"] = "gzip"
    elif case == "over_declared":
        value.headers["Content-Length"] = str(len(PDF) + 1)
    elif case == "actual_overflow":
        content = PDF + b"extra"
        value.headers["Content-Length"] = str(len(PDF))
    elif case == "short":
        content = PDF[:-1]
        value.headers["Content-Length"] = str(len(PDF))
    elif case == "missing_length":
        content = iter([PDF])
    elif case == "not_pdf":
        content = b"x" * len(PDF)
    elif case == "bad_key":
        value.row["staging_key"] = "blobs/forbidden-final-key"
    response = value.client.put(value.upload_url, content=content, headers=value.headers)
    assert response.status_code == status
    assert not value.objects


def test_expired_and_tampered_tickets_rejected(harness, monkeypatch):
    value = harness
    response = value.client.put(value.upload_url + "changed", content=PDF, headers=value.headers)
    assert response.status_code == 403
    monkeypatch.setattr(transfer, "now", lambda: now() + timedelta(minutes=16))
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code == 410
    assert not value.objects


def test_upload_deadline_applies_to_slow_client(harness, monkeypatch):
    value = harness
    monkeypatch.setattr(transfer, "_limits", lambda: (104857600, 0.01))

    class SlowRequest:
        headers = value.headers | {
            "content-length": str(len(PDF)),
            "origin": ORIGIN,
            "x-csrf-token": "synthetic-csrf",
            "content-type": "application/pdf",
        }
        cookies = {authentication.COOKIE: "synthetic-cookie"}

        async def stream(self):
            await asyncio.sleep(1)
            yield PDF

    ticket = parse_qs(urlsplit(value.upload_url).query)["ticket"][0]
    with pytest.raises(DomainError) as error:
        asyncio.run(transfer.upload_bytes(value.row["id"], SlowRequest(), ticket))
    assert error.value.status == 408
    assert not value.objects


def test_download_authenticated_original_bytes_unicode_name_range_returns_full_200(harness):
    value = harness
    response = value.client.get(value.download_url, headers={"Range": "bytes=0-4"})
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["accept-ranges"] == "none"
    assert response.headers["content-length"] == str(len(PDF))
    assert "filename*=UTF-8''%E4%B8%AD%E6%96%87" in response.headers["content-disposition"]
    assert "%0D%0A" in response.headers["content-disposition"]
    assert "\r" not in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private,no-store"
    assert value.body.closed


@pytest.mark.parametrize("case", ["no_cookie", "another_session", "tampered", "another_version"])
def test_download_ticket_and_cookie_both_required(harness, case):
    value = harness
    url = value.download_url
    if case == "no_cookie":
        value.client.cookies.clear()
    elif case == "another_session":
        value.session["id"] = uuid4()
    elif case == "tampered":
        url += "changed"
    else:
        url = url.replace(str(value.version), str(uuid4()))
    response = value.client.get(url)
    assert response.status_code in (401, 403)
    assert value.body is None


def test_download_short_body_and_deadline_close_underlying_stream():
    body = io.BytesIO(b"short")
    with pytest.raises(DomainError) as error:
        list(transfer._download_chunks(body, 10, 30))
    assert error.value.status == 409 and body.closed
    body = io.BytesIO(PDF)
    with pytest.raises(DomainError) as error:
        list(transfer._download_chunks(body, len(PDF), -1))
    assert error.value.status == 408 and body.closed


def test_limits_config_and_grant_fail_before_remote_write(harness, monkeypatch):
    value = harness
    monkeypatch.setenv("OBJECT_TRANSFER_MAX_BYTES", "5")
    with pytest.raises(DomainError) as error:
        transfer.make_upload_url(
            None,
            value.row["id"],
            session=value.session,
            bid_id=value.row["bid_id"],
            staging_key=value.row["staging_key"],
            byte_size=len(PDF),
            revision=1,
            expires_at=value.row["expires_at"],
        )
    assert error.value.status == 413
    monkeypatch.setenv("OBJECT_TRANSFER_MAX_BYTES", "unbounded")
    with pytest.raises(DomainError) as error:
        transfer._limits()
    assert error.value.status == 503


def test_router_documents_cookie_and_binary_transfer(harness):
    app = FastAPI()
    app.include_router(transfer.router)
    paths = app.openapi()["paths"]
    upload = paths["/api/v1/object-transfers/uploads/{upload_id}"]["put"]
    assert upload["security"] == [{"SessionCookie": []}]
    assert "application/pdf" in upload["requestBody"]["content"]


def test_ambiguous_storage_failure_leaves_old_key_and_retry_never_overwrites(harness, monkeypatch):
    value = harness
    original = value.row["staging_key"]

    def ambiguous_put(key, body, media):
        value.objects[key] = body
        raise DomainError("OBJECT_STORE_UNAVAILABLE", "合成连接中断", 503, retryable=True)

    monkeypatch.setattr(transfer.storage, "put", ambiguous_put)
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code == 503
    assert value.row["staging_key"] == original
    assert len(value.temporary_keys) == 1

    def retry_put(key, body, media):
        assert key not in value.objects
        value.objects[key] = body
        return {"ETag": "synthetic-retry-etag"}

    monkeypatch.setattr(transfer.storage, "put", retry_put)
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code == 200
    assert len(value.objects) == 2
    assert value.row["staging_key"] == value.temporary_keys[-1]


def test_temporary_file_is_private_and_closed_after_transfer(harness, monkeypatch):
    value = harness
    captured = []

    def store(file, key, media_type="application/pdf"):
        import os

        assert os.fstat(file.fileno()).st_mode & 0o777 == 0o600
        captured.append(file)
        file.seek(0)
        value.objects[key] = file.read()
        return {"ETag": "synthetic-private-temp"}

    monkeypatch.setattr(transfer, "_store_file", store)
    response = value.client.put(value.upload_url, content=PDF, headers=value.headers)
    assert response.status_code == 200 and captured[0].closed


def test_temporary_registry_private_only_tracks_scoped_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "__file__", str(tmp_path / "apps/api/object_transfer.py"))
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "oss")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "synthetic-private-bucket")
    monkeypatch.setenv("OBJECT_STORE_PREFIX", "synthetic-isolated-environment/")
    key = f"staging/{uuid4()}/{uuid4()}/transfers/{uuid4()}"
    transfer._register_temporary_key(key)
    journal = tmp_path / ".local/object-transfer-temporary-keys.jsonl"
    assert journal.stat().st_mode & 0o777 == 0o600
    assert journal.parent.stat().st_mode & 0o777 == 0o700
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert records == [
        {
            "backend": "oss",
            "bucket": "synthetic-private-bucket",
            "key": key,
            "prefix": "synthetic-isolated-environment/",
            "created_at": records[0]["created_at"],
            "state": "requires_database_reference_check",
        }
    ]


def test_download_size_mismatch_closes_remote_response_before_sending(harness, monkeypatch):
    value = harness
    body = io.BytesIO(PDF)
    monkeypatch.setattr(
        transfer.storage,
        "client",
        lambda: SimpleNamespace(
            get_object=lambda **kwargs: {"Body": body, "ContentLength": len(PDF) + 1}
        ),
    )
    response = value.client.get(value.download_url)
    assert response.status_code == 409 and body.closed


def test_download_client_disconnect_before_iteration_closes_remote_body():
    body = io.BytesIO(PDF)
    response = transfer._DownloadResponse(iter([PDF]), source_body=body, timeout=1)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            await asyncio.sleep(10)

    asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send))
    assert body.closed
