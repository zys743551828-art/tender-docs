"""显式启用的真实PG+原生OSS+ASGI；不改桶设置，不调用模型。"""

import hashlib
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api import multipart_routes
from apps.api.auth import auth
from apps.api.views import upload_row
from apps.worker import engine
from packages.domain.runtime import DomainError, clean, connection, now, secret_hash
from packages.uploads import service
from packages.uploads.native import NativeMultipart
from packages.uploads.protocol import PART_SIZE, unpack

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MULTIPART_INTEGRATION") != "1", reason="需显式真实隔离PG/原生OSS环境"
)


def synthetic_pdf():
    content = b"BT /F1 12 Tf 20 80 Td (Synthetic multipart transport.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    return (
        data
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n%"
        + b"x" * PART_SIZE
        + b"\n"
    )


DATA = synthetic_pdf()


@pytest.fixture
def harness(monkeypatch):
    assert os.environ.get("OBJECT_STORE_BACKEND") == "oss"
    monkeypatch.setenv("PGAPPNAME", "multipart-development-checks")
    member, bid, batch, session_id = uuid4(), uuid4(), uuid4(), uuid4()
    token, csrf = secrets.token_hex(32), secrets.token_hex(24)
    with connection() as c:
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (member, "合成分片机制自测成员")
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                session_id,
                member,
                secret_hash(token),
                hashlib.sha256(csrf.encode()).hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{}',%s)",
            (bid, "合成分片机制自测项目", member),
        )
        c.execute(
            "INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,'open',%s)",
            (batch, bid, member),
        )
    app = FastAPI()
    app.include_router(multipart_routes.router)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "details": clean(exc.details)}, status_code=exc.status
        )

    def wrap(value):
        return JSONResponse(value["body"], status_code=value["status"], headers=value["headers"])

    @app.post("/create")
    def create(request: Request, body: dict = Body(...)):
        return wrap(service.create_upload(bid, request, auth(request, True), body))

    @app.get("/{upload_id}/parts")
    def parts(upload_id: UUID, request: Request):
        return service.get_parts(upload_id, auth(request))

    @app.post("/{upload_id}/refresh")
    def refresh(upload_id: UUID, request: Request, body: dict = Body(...)):
        return wrap(service.refresh(upload_id, request, auth(request, True), body))

    @app.post("/{upload_id}/complete")
    def complete(upload_id: UUID, request: Request, body: dict = Body(...)):
        return wrap(service.complete(upload_id, request, auth(request, True), body))

    @app.post("/{upload_id}/abort")
    def abort(upload_id: UUID, request: Request):
        return wrap(service.abort(upload_id, request, auth(request, True)))

    # Prove remote operations never run while this test process holds an API transaction.
    calls = []
    original_connection = service.connection

    @contextmanager
    def tagged_connection():
        with original_connection() as c:
            c.execute(
                "SELECT set_config('application_name',%s,true)",
                ("multipart-call-" + str(threading.get_ident()),),
            )
            yield c

    monkeypatch.setattr(service, "connection", tagged_connection)
    for method in ("initialize", "parts", "upload", "complete", "abort", "verify_complete"):
        original = getattr(NativeMultipart, method)

        def guard(self, *args, _method=method, _original=original, **kwargs):
            with connection() as c:
                held = c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s AND pid<>pg_backend_pid() AND state='idle in transaction'",
                    ("multipart-call-" + str(threading.get_ident()),),
                ).fetchone()["n"]
            assert held == 0, "remote operation held a source transaction"
            calls.append(_method)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(NativeMultipart, method, guard)
    with TestClient(app) as client:
        client.cookies.set("session", token)
        headers = {"Origin": os.environ["APP_ORIGIN"], "X-CSRF-Token": csrf}
        body = {
            "batch_id": str(batch),
            "filename": "合成分片.pdf",
            "role": "tender",
            "byte_size": len(DATA),
            "declared_sha256": hashlib.sha256(DATA).hexdigest(),
            "target": {"kind": "new_file"},
        }
        yield client, headers, body, calls


def command_headers(headers, revision=1, upload=None, key=None):
    return {
        **headers,
        "Idempotency-Key": key or uuid4().hex,
        **({"If-Match": f'"upload:{upload}:{revision}"'} if upload else {}),
    }


def send_part(client, headers, grant, index, data=None):
    part = grant["parts"][index]
    payload = (
        DATA[part["byte_offset"] : part["byte_offset"] + part["byte_size"]]
        if data is None
        else data
    )
    response = client.put(
        part["url"],
        content=payload,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    return response


def test_real_missing_part_recovery_lost_complete_response_and_replay(harness, monkeypatch):
    client, headers, body, calls = harness
    created = client.post("/create", json=body, headers=command_headers(headers))
    assert created.status_code == 200, created.text
    grant = created.json()
    upload = grant["upload_id"]
    assert send_part(client, headers, grant, 0).status_code == 200
    observed = client.get(f"/{upload}/parts").json()["completed_parts"]
    assert [p["part_number"] for p in observed] == [1]
    fake = observed + [
        {"part_number": 2, "etag": "not-uploaded", "byte_size": len(DATA) - PART_SIZE}
    ]
    missing = client.post(
        f"/{upload}/complete",
        json={"parts": fake, "declared_sha256": body["declared_sha256"]},
        headers=command_headers(headers, upload=upload),
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "MULTIPART_INCOMPLETE"
    refreshed = client.post(
        f"/{upload}/refresh",
        json={"part_numbers": [1, 2]},
        headers=command_headers(headers, revision=3, upload=upload),
    )
    assert refreshed.status_code == 200, refreshed.text
    grant = refreshed.json()
    assert send_part(client, headers, grant, 1).status_code == 200
    actual = client.get(f"/{upload}/parts").json()["completed_parts"]
    complete_original = NativeMultipart.complete

    def lose_response(self, *args, **kwargs):
        complete_original(self, *args, **kwargs)
        raise DomainError("MULTIPART_PROVIDER_UNAVAILABLE", "Synthetic lost success response", 503)

    monkeypatch.setattr(NativeMultipart, "complete", lose_response)
    payload = {"parts": actual, "declared_sha256": body["declared_sha256"]}
    hdr = command_headers(headers, revision=grant["revision"], upload=upload)
    done = client.post(f"/{upload}/complete", json=payload, headers=hdr)
    assert done.status_code == 202, done.text
    replay = client.post(f"/{upload}/complete", json=payload, headers=hdr)
    assert replay.status_code == 202
    assert replay.json()["operation"]["id"] == done.json()["operation"]["id"]
    with connection() as c:
        row = upload_row(c, upload)
        assert row["state"] in {"verifying", "verified"}
        assert unpack(row["provider_upload_id"], row["staging_key"])["phase"] == "completed"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM operations WHERE command_id=(SELECT id FROM commands WHERE target_id=%s AND command_kind='complete_upload' AND response->'body' ? 'operation')",
                (upload,),
            ).fetchone()["n"]
            == 1
        )
    with connection() as c:
        event = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY id LIMIT 1",
            (done.json()["operation"]["id"],),
        ).fetchone()["payload"]
    engine.handle(event)
    engine.handle(event)
    with connection() as c:
        row = upload_row(c, upload)
        assert row["state"] == "verified" and row["verified_version_id"] is not None
        assert (
            c.execute(
                "SELECT count(*) AS n FROM document_versions WHERE bid_id=%s", (row["bid_id"],)
            ).fetchone()["n"]
            == 1
        )
    assert "initialize" in calls and "complete" in calls


def test_real_stale_remote_write_is_not_published_and_full_sha_quarantines(harness, monkeypatch):
    client, headers, body, calls = harness
    created = client.post("/create", json=body, headers=command_headers(headers))
    assert created.status_code == 200, created.text
    grant = created.json()
    upload = grant["upload_id"]
    original_upload = NativeMultipart.upload
    refreshed = {}

    def race(self, key, provider_id, number, data):
        value = original_upload(self, key, provider_id, number, data)
        if number == 1 and not refreshed:
            response = client.post(
                f"/{upload}/refresh",
                json={"part_numbers": [1, 2]},
                headers=command_headers(headers, upload=upload),
            )
            assert response.status_code == 200, response.text
            refreshed.update(response.json())
        return value

    monkeypatch.setattr(NativeMultipart, "upload", race)
    stale = send_part(client, headers, grant, 0, data=b"%PDF-" + b"WRONG" + b"z" * (PART_SIZE - 10))
    assert stale.status_code in {403, 412}, stale.text
    # The remote write actually happened; DB fencing must not pretend it was rolled back.
    assert client.get(f"/{upload}/parts").json()["completed_parts"][0]["part_number"] == 1
    assert send_part(client, headers, refreshed, 1).status_code == 200
    parts = client.get(f"/{upload}/parts").json()["completed_parts"]
    result = client.post(
        f"/{upload}/complete",
        json={"parts": parts, "declared_sha256": body["declared_sha256"]},
        headers=command_headers(headers, revision=2, upload=upload),
    )
    assert result.status_code == 422, result.text
    assert result.json()["code"] == "UPLOAD_DIGEST_MISMATCH"
    with connection() as c:
        row = upload_row(c, upload)
        assert row["state"] == "quarantined" and row["verified_version_id"] is None
        assert (
            c.execute(
                "SELECT count(*) AS n FROM operations WHERE command_id IN(SELECT id FROM commands WHERE target_id=%s AND command_kind='complete_upload')",
                (upload,),
            ).fetchone()["n"]
            == 0
        )


def test_real_init_response_loss_never_reinitializes_and_abort_is_exact(harness, monkeypatch):
    client, headers, body, calls = harness
    initialized = []
    original = NativeMultipart.initialize

    def lose_response(self, key):
        provider = original(self, key)
        initialized.append((key, provider))
        raise DomainError("MULTIPART_PROVIDER_UNAVAILABLE", "Synthetic init response loss", 503)

    monkeypatch.setattr(NativeMultipart, "initialize", lose_response)
    hdr = command_headers(headers)
    first = client.post("/create", json=body, headers=hdr)
    assert first.status_code == 503, first.text
    upload = first.json()["details"]["upload_id"]
    again = client.post("/create", json=body, headers=hdr)
    assert again.status_code == 503 and len(initialized) == 1
    with connection() as c:
        row = upload_row(c, upload)
        assert row["error_code"] == "MULTIPART_INIT_UNCERTAIN"
        assert unpack(row["provider_upload_id"], row["staging_key"])["phase"] == "init_uncertain"
    # This test retained its exact synthetic native identity in memory, so it can abort it.
    native = NativeMultipart()
    native.abort(*initialized[0])
    native.abort(*initialized[0])


def test_real_abort_closes_old_tickets_and_replay(harness):
    client, headers, body, calls = harness
    created = client.post("/create", json=body, headers=command_headers(headers))
    assert created.status_code == 200, created.text
    grant = created.json()
    upload = grant["upload_id"]
    hdr = command_headers(headers, upload=upload)
    aborted = client.post(f"/{upload}/abort", headers=hdr)
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["state"] == "aborted"
    assert client.post(f"/{upload}/abort", headers=hdr).status_code == 200
    assert send_part(client, headers, grant, 0).status_code == 409


def test_real_two_complete_requests_share_one_operation_and_block_refresh_abort(
    harness, monkeypatch
):
    client, headers, body, calls = harness
    created = client.post("/create", json=body, headers=command_headers(headers))
    assert created.status_code == 200, created.text
    grant = created.json()
    upload = grant["upload_id"]
    for index in range(2):
        assert send_part(client, headers, grant, index).status_code == 200
    parts = client.get(f"/{upload}/parts").json()["completed_parts"]
    complete_original = NativeMultipart.complete
    barrier = threading.Barrier(2)
    results = []

    def competing_complete(self, *args, **kwargs):
        # completing is already committed while remote completion is in flight.
        refreshed = client.post(
            f"/{upload}/refresh",
            json={"part_numbers": [1, 2]},
            headers=command_headers(headers, revision=2, upload=upload),
        )
        aborted = client.post(
            f"/{upload}/abort", headers=command_headers(headers, revision=2, upload=upload)
        )
        assert refreshed.status_code == 409, refreshed.text
        assert aborted.status_code == 409, aborted.text
        results.append((refreshed.status_code, aborted.status_code))
        barrier.wait(timeout=10)
        return complete_original(self, *args, **kwargs)

    monkeypatch.setattr(NativeMultipart, "complete", competing_complete)
    payload = {"parts": parts, "declared_sha256": body["declared_sha256"]}
    hdr = command_headers(headers, revision=1, upload=upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(f"/{upload}/complete", json=payload, headers=hdr), range(2)
            )
        )
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()["operation"]["id"] == responses[1].json()["operation"]["id"]
    assert len(results) == 2


def test_real_foreign_session_and_wrong_part_ticket_are_rejected_before_storage(harness):
    client, headers, body, calls = harness
    created = client.post("/create", json=body, headers=command_headers(headers))
    assert created.status_code == 200, created.text
    grant = created.json()
    upload = grant["upload_id"]
    part = grant["parts"][1]
    wrong_url = part["url"].replace("/parts/2?", "/parts/1?")
    invalid = client.put(
        wrong_url,
        content=DATA[PART_SIZE:],
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert invalid.status_code == 403
    original_cookie = client.cookies.get("session")
    foreign = secrets.token_hex(32)
    with connection() as c:
        member = uuid4()
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (member, "合成另一分片会话成员")
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                member,
                secret_hash(foreign),
                hashlib.sha256(headers["X-CSRF-Token"].encode()).hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    client.cookies.set("session", foreign)
    rejected = send_part(client, headers, grant, 1)
    assert rejected.status_code == 403
    assert "upload" not in calls
    client.cookies.set("session", original_cookie)
    assert (
        client.post(f"/{upload}/abort", headers=command_headers(headers, upload=upload)).status_code
        == 200
    )
