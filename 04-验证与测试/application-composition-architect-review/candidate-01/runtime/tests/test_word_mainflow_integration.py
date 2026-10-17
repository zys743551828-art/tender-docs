"""共享主流程的受限PG/实际fence；对象端口按用例明确为合成内存或原生OSS，不请求收费模型。"""

import io
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.worker import engine
from packages.domain import storage
from packages.domain.runtime import connection, fetch, sha
from packages.word_intake.inspection import DOCX, PDF
from tests.test_word_intake import make_docx
from tests.test_word_intake_integration import world as world

REAL_STORAGE_CLIENT, REAL_STORAGE_GET, REAL_STORAGE_PUT = storage.client, storage.get, storage.put

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORD_MAINFLOW_INTEGRATION") != "1", reason="Word fresh非owner角色库"
)


@pytest.fixture
def shared(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WORD_INTAKE_ENABLED", "1")
    monkeypatch.delenv("WORD_RENDER_PROFILE_PATH", raising=False)
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "oss")
    monkeypatch.delenv("PARSING_RUNTIME_URL", raising=False)
    monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)
    monkeypatch.setattr("apps.api.main.configured_parser", lambda: None)

    class MemoryCloud:
        def head_object(self, *, Bucket, Key):
            return {"ContentLength": len(world["objects"][Key])}

        def get_object(self, *, Bucket, Key):
            data = world["objects"][Key]
            return {"ContentLength": len(data), "Body": io.BytesIO(data)}

    monkeypatch.setattr(storage, "client", lambda: MemoryCloud())

    def put(key, data, media):
        world["objects"][key] = data
        return {"ETag": '"synthetic-byte-transport"'}

    monkeypatch.setattr(storage, "put", put)
    with connection() as c:
        c.execute(
            "INSERT INTO review_slots(bid_id,revision) VALUES(%s,1) ON CONFLICT DO NOTHING",
            (world["bid"],),
        )
    with TestClient(app) as client:
        client.cookies.update(world["client"].cookies)
        yield world | {"client": client}


def headers(w, tag=None):
    return {
        "Origin": "http://word-intake.test",
        "X-CSRF-Token": w["csrf"],
        "Idempotency-Key": uuid4().hex,
    } | ({"If-Match": tag} if tag else {})


def pump(op, *, compute=True):
    with connection(True) as c:
        msg = fetch(
            c,
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' ORDER BY id LIMIT 1",
            (op,),
        )["payload"]
    lease = engine.acquire(msg)
    assert lease is not None
    if compute:
        result = engine.run_compute(msg, lease)
        assert engine.finish(msg, lease, result)
        with connection(True) as c:
            effect = fetch(
                c,
                "SELECT result FROM effect_receipts WHERE operation_id=%s AND effect_key=%s",
                (op, msg["step_key"]),
            )
        return msg, result | effect["result"] if isinstance(result, dict) else effect["result"]
    return msg, lease


def upload(w, data, *, filename="body.docx", role="body", target=None, source=None):
    client, bid = w["client"], w["bid"]
    batch = client.post(f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers(w))
    assert batch.status_code == 201, batch.text
    body = {
        "batch_id": batch.json()["id"],
        "filename": filename,
        "role": role,
        "declared_sha256": sha(data),
        "byte_size": len(data),
        "target": target or {"kind": "new_file"},
    }
    if source:
        body["declared_source_document_version_id"] = str(source)
    grant = client.post(f"/api/v1/bids/{bid}/uploads", json=body, headers=headers(w))
    assert grant.status_code == 200, grant.text
    g = grant.json()
    media = DOCX if filename.endswith(".docx") else PDF
    sent = client.put(g["upload_url"], content=data, headers=headers(w) | {"Content-Type": media})
    assert sent.status_code == 200
    complete = client.post(
        f"/api/v1/uploads/{g['upload_id']}/complete",
        json={"parts": [], "declared_sha256": sha(data)},
        headers=headers(w),
    )
    assert complete.status_code == 202, complete.text
    _, verified = pump(complete.json()["operation"]["id"])
    state = client.get(f"/api/v1/uploads/{g['upload_id']}").json()
    return g | {
        "batch_id": body["batch_id"],
        "binding": state["binding_id"],
        "version": verified["document_version_id"],
        "verified": verified,
    }


def commit(w, u):
    tag = w["client"].get(f"/api/v1/bids/{w['bid']}").headers["etag"]
    response = w["client"].post(
        f"/api/v1/bids/{w['bid']}/upload-batches/{u['batch_id']}/commit",
        json={
            "accepted_upload_ids": [u["upload_id"]],
            "accepted_binding_ids": [u["binding"]],
            "explicit_partial_acceptance": False,
        },
        headers=headers(w, tag),
    )
    assert response.status_code == 202, response.text
    from packages.domain.runtime import validate

    validate("Accepted", response.json())
    return response.json()


def test_shared_docx_verify_failure_keeps_original_then_unverified_pdf_recovery(shared):
    w = shared
    u = upload(w, make_docx("Visible shared Word original " + uuid4().hex))
    assert u["verified"]["word_original_saved"]
    result = commit(w, u)
    _, parsed = pump(result["operation"]["id"])
    assert parsed["coverage"] == "failed"
    _, rendered = pump(u["verified"]["conversion_operation_id"])
    assert rendered["outcome"]["error_code"] == "WORD_RENDER_SANDBOX_UNAVAILABLE"
    bid = w["client"].get(f"/api/v1/bids/{w['bid']}").json()
    assert bid["current_body_id"] is None
    assert bid["next_action"]["action"] == "inspect_word_conversion"
    status = w["client"].get(f"/api/v1/bids/{w['bid']}/word-originals/{u['version']}")
    assert status.status_code == 200
    assert (
        status.json()["original_saved"]
        and not status.json()["conversion"]["controlled_render_verified"]
    )
    from tests.test_slice_integration import pdf_bytes

    pdf = upload(w, pdf_bytes(), filename="body.pdf", source=u["version"])
    commit(w, pdf)
    with connection() as c:
        relation = fetch(
            c,
            "SELECT kind,verification FROM document_relations WHERE document_version_id=%s",
            (pdf["version"],),
        )
        assert dict(relation) == {"kind": "user_declared_same_draft", "verification": "unverified"}
        assert fetch(c, "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (w["bid"],))[
            "current_body_id"
        ] == uuid4().__class__(pdf["version"])


def test_new_word_clears_old_pdf_and_rejects_same_logical_file(shared):
    from tests.test_slice_integration import pdf_bytes

    w = shared
    pdf = upload(w, pdf_bytes(), filename="prior.pdf")
    commit(w, pdf)
    word = upload(w, make_docx("Current Word replaces old PDF " + uuid4().hex))
    commit(w, word)
    with connection() as c:
        assert (
            fetch(c, "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (w["bid"],))[
                "current_body_id"
            ]
            is None
        )
    batch = (
        w["client"]
        .post(f"/api/v1/bids/{w['bid']}/upload-batches", json={}, headers=headers(w))
        .json()
    )
    bad = w["client"].post(
        f"/api/v1/bids/{w['bid']}/uploads",
        json={
            "batch_id": batch["id"],
            "filename": "mixed.docx",
            "role": "body",
            "byte_size": 100,
            "declared_sha256": "a" * 64,
            "target": {
                "kind": "new_version",
                "file_id": pdf["file_id"],
                "expected_file_revision": 2,
            },
        },
        headers=headers(w),
    )
    assert bad.status_code == 409 and bad.json()["code"] == "FILE_FORMAT_MISMATCH"


def test_default_closed_word_gate_precedes_storage(shared, monkeypatch):
    w = shared
    monkeypatch.delenv("WORD_INTAKE_ENABLED")
    batch = (
        w["client"]
        .post(f"/api/v1/bids/{w['bid']}/upload-batches", json={}, headers=headers(w))
        .json()
    )
    blocked = w["client"].post(
        f"/api/v1/bids/{w['bid']}/uploads",
        json={
            "batch_id": batch["id"],
            "filename": "closed.docx",
            "role": "body",
            "byte_size": 100,
            "declared_sha256": "a" * 64,
            "target": {"kind": "new_file"},
        },
        headers=headers(w),
    )
    assert blocked.status_code == 503 and blocked.json()["code"] == "WORD_INTAKE_DISABLED"


def enable_real_ports(monkeypatch):
    from pathlib import Path

    from deploy.initialize_database import file_configuration
    from packages.domain.parsing_profile import configured_parser

    monkeypatch.setenv(
        "WORD_RENDER_PROFILE_PATH",
        str(
            Path(
                os.environ.get(
                    "WORD_BRIDGE_RENDER_PROFILE", ".local/word-render-profile-dev06.json"
                )
            ).resolve()
        ),
    )
    for key, value in file_configuration(Path(".local/parsing.env")).items():
        if key.startswith("PARSING_RUNTIME_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setattr("apps.api.main.configured_parser", configured_parser)


@pytest.mark.skipif(
    os.environ.get("RUN_WORD_BRIDGE_REAL_RENDER") != "1", reason="实际固定Docker与Parser03本地端口"
)
@pytest.mark.parametrize("timing", ["early", "late"])
def test_real_controlled_pdf_adoption_and_word_pdf_parse_ancestry(shared, monkeypatch, timing):
    from uuid import UUID

    from apps.api.word_coordinator import tick
    from apps.worker.handlers import publish_readable_source
    from packages.domain.runtime import DomainError
    from packages.rag.indexing import original_source_id, parse_source_id

    w = shared
    enable_real_ports(monkeypatch)
    original = make_docx(
        "本轮施工组织设计说明，施工工期120天，质量标准为合格。原件只用于受控转换身份校验。"
        + uuid4().hex
    )
    u = upload(w, original)
    if timing == "late":
        commit(w, u)
    _, rendered = pump(u["verified"]["conversion_operation_id"])
    assert rendered["outcome"]["coverage"] == "complete"
    if timing == "early":
        committed = commit(w, u)
        parse_op = committed["operation"]["id"]
    else:
        adoption = tick(w["bid"], UUID(u["version"]))
        assert adoption["state"] == "adopted", adoption
        parse_op = adoption["parse_operation_id"]
        assert tick(w["bid"], UUID(u["version"])) == adoption
    with connection() as c:
        p = fetch(
            c,
            "SELECT current_body_id,current_material_id FROM bid_profiles WHERE bid_id=%s",
            (w["bid"],),
        )
        pdf = p["current_body_id"]
        assert pdf is not None and str(pdf) != u["version"]
        relation = fetch(
            c,
            "SELECT kind,verification,source_document_version_id FROM document_relations WHERE document_version_id=%s",
            (pdf,),
        )
        assert relation["kind"] == "controlled_render" and relation["verification"] == "verified"
        assert str(relation["source_document_version_id"]) == u["version"]
        assert (
            fetch(
                c,
                "SELECT b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
                (pdf,),
            )["media_type"]
            == PDF
        )
    projected = w["client"].get(f"/api/v1/bids/{w['bid']}/word-materials/{u['version']}")
    assert projected.status_code == 200
    assert (
        projected.json()["adoption_state"] == "adopted"
        and projected.json()["is_current_converted_pdf"] is True
    )
    assert not projected.json()["selected_as_current_material"]
    pump(parse_op)
    with connection(True) as c:
        parsed = fetch(
            c,
            "SELECT id,coverage FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC LIMIT 1",
            (pdf,),
        )
        source, wordroot = parse_source_id(parsed["id"]), original_source_id(UUID(u["version"]))
        assert c.execute(
            "SELECT 1 FROM retrieval.source_ancestors WHERE source_id=%s AND ancestor_id=%s",
            (source, wordroot),
        ).fetchone()
        assert (
            fetch(c, "SELECT count(*) AS n FROM retrieval.chunks WHERE source_id=%s", (wordroot,))[
                "n"
            ]
            == 0
        )
        assert (
            fetch(
                c, "SELECT count(*) AS n FROM evidence_blocks WHERE parse_id=%s", (parsed["id"],)
            )["n"]
            > 0
        )
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (w["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (wordroot,),
        )
    with pytest.raises(DomainError) as blocked:
        with connection(True) as c:
            c.execute("SELECT design.lock_worker_bid(%s)", (w["bid"],))
            publish_readable_source(c, w["bid"], parsed["id"])
    assert blocked.value.code == "WORD_SOURCE_WITHDRAWN"


def test_word_render_cancel_and_closed_project_have_no_publication(shared):
    w = shared
    u = upload(w, make_docx("Conversion fence cancellation " + uuid4().hex))
    message, lease = pump(u["verified"]["conversion_operation_id"], compute=False)
    computed = engine.run_compute(message, lease)
    tag = w["client"].get(f"/api/v1/operations/{message['operation_id']}").headers["etag"]
    cancelled = w["client"].post(
        f"/api/v1/operations/{message['operation_id']}/cancel",
        json={"reason": "合成取消验证", "cancel_pending_same_input": False},
        headers=headers(w, tag),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert engine.finish(message, lease, computed) is False
    with connection(True) as c:
        run = fetch(
            c,
            "SELECT outcome,pdf_document_version_id FROM word_render_runs WHERE operation_id=%s",
            (message["operation_id"],),
        )
        assert run["outcome"] is None and run["pdf_document_version_id"] is None
    other = upload(w, make_docx("Conversion closes before finish " + uuid4().hex))
    message, lease = pump(other["verified"]["conversion_operation_id"], compute=False)
    computed = engine.run_compute(message, lease)
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (w["bid"],))
        c.execute("UPDATE bid_profiles SET phase='closed' WHERE bid_id=%s", (w["bid"],))
    assert engine.finish(message, lease, computed) is True
    with connection(True) as c:
        assert (
            fetch(
                c,
                "SELECT outcome FROM word_render_runs WHERE operation_id=%s",
                (message["operation_id"],),
            )["outcome"]
            is None
        )
        assert (
            fetch(c, "SELECT state FROM operations WHERE id=%s", (message["operation_id"],))[
                "state"
            ]
            == "failed"
        )


def test_retained_word_reuses_verified_blob_and_worker_rebuilds_receipt(shared):
    from packages.domain.runtime import Command
    from packages.lifecycle.service import close_batch

    w = shared
    u = upload(w, make_docx("Closed batch Word retained byte verification " + uuid4().hex))
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (w["bid"],))
        command = Command(c, w["actor"], "synthetic_close_word_batch", w["bid"], uuid4().hex, {})
        close_batch(c, w["bid"], u["batch_id"], w["actor"], "显式保留暂存后改选", command.id)
        command.finish({"closed": True})
    assert engine.acquire(pump_message(u["verified"]["conversion_operation_id"])) is None
    new = (
        w["client"]
        .post(f"/api/v1/bids/{w['bid']}/upload-batches", json={}, headers=headers(w))
        .json()
    )
    old = w["client"].get(f"/api/v1/uploads/{u['upload_id']}")
    reused = w["client"].post(
        f"/api/v1/bids/{w['bid']}/upload-batches/{new['id']}/retained-uploads",
        json={
            "source_upload_id": u["upload_id"],
            "target": {"kind": "new_file"},
            "reason": "复用已核验Word原件",
        },
        headers=headers(w, old.headers["etag"]),
    )
    assert reused.status_code == 201, reused.text
    value = reused.json()
    assert value["state"] == "verifying" and value["document_version_id"] is None
    with connection() as c:
        op = fetch(
            c,
            "SELECT o.id FROM operations o JOIN outbox x ON x.operation_id=o.id WHERE o.bid_id=%s AND x.payload->>'payload_ref'=%s",
            (w["bid"], value["id"]),
        )
    _, verified = pump(op["id"])
    assert verified["word_original_saved"]
    got = w["client"].get(f"/api/v1/uploads/{value['id']}").json()
    assert got["state"] == "verified" and got["document_version_id"] != u["version"]
    with connection() as c:
        row = fetch(
            c,
            "SELECT b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN word_intake_receipts w ON w.document_version_id=v.id WHERE v.id=%s",
            (got["document_version_id"],),
        )
        assert row["digest"] == u["verified"]["digest"]
    commit(w, {"upload_id": value["id"], "batch_id": new["id"], "binding": got["binding_id"]})


def pump_message(op):
    with connection(True) as c:
        return fetch(
            c,
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' ORDER BY id LIMIT 1",
            (op,),
        )["payload"]


@pytest.mark.skipif(
    os.environ.get("RUN_WORD_BRIDGE_REAL_RENDER") != "1", reason="实际隔离Docker转换"
)
def test_late_controlled_pdf_cannot_grab_new_word_head(shared, monkeypatch):
    from uuid import UUID

    from apps.api.word_coordinator import tick
    from packages.domain.word_materials import pending_body

    w = shared
    enable_real_ports(monkeypatch)
    first = upload(w, make_docx("旧Word完整转换，仅保留旧产物。" + uuid4().hex))
    commit(w, first)
    msg, lease = pump(first["verified"]["conversion_operation_id"], compute=False)
    computed = engine.run_compute(msg, lease)
    assert computed["outcome"]["coverage"] == "complete"
    second = upload(
        w,
        make_docx("新Word必须保持当前待检，不受旧转换覆盖。" + uuid4().hex),
        target={"kind": "new_version", "file_id": first["file_id"], "expected_file_revision": 2},
    )
    commit(w, second)
    assert engine.finish(msg, lease, computed)
    adoption = tick(w["bid"], UUID(first["version"]))
    assert adoption["state"] == "needs_selection"
    with connection() as c:
        assert str(pending_body(c, w["bid"])["document_version_id"]) == second["version"]
        assert (
            fetch(c, "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (w["bid"],))[
                "current_body_id"
            ]
            is None
        )
        assert (
            fetch(
                c,
                "SELECT pdf_document_version_id FROM word_render_runs WHERE operation_id=%s",
                (first["verified"]["conversion_operation_id"],),
            )["pdf_document_version_id"]
            is not None
        )


def test_broken_renderer_config_still_saves_word_original(shared, monkeypatch):
    w = shared
    monkeypatch.setenv("WORD_RENDER_PROFILE_PATH", "/tmp/no-such-synthetic-word-profile.json")
    u = upload(w, make_docx("Broken configured renderer preserves original " + uuid4().hex))
    assert u["verified"]["word_original_saved"]
    _, rendered = pump(u["verified"]["conversion_operation_id"])
    assert rendered["outcome"]["state"] == "failed"
    with connection() as c:
        assert (
            fetch(c, "SELECT state FROM uploads WHERE id=%s", (u["upload_id"],))["state"]
            == "verified"
        )


def test_shared_multipart_docx_preserves_native_content_type_and_full_worker_inspection(
    shared, monkeypatch
):
    import zipfile

    from apps.api import multipart_routes
    from packages.uploads import service
    from packages.uploads.protocol import PART_SIZE

    w = shared
    package = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(make_docx("Multipart original " + uuid4().hex))) as source,
        zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as target,
    ):
        for name in source.namelist():
            content = source.read(name)
            if name == "[Content_Types].xml":
                content = content.replace(
                    b"</Types>", b'<Default Extension="png" ContentType="image/png"/></Types>'
                )
            target.writestr(name, content)
        target.writestr("word/media/retained.png", b"\x89PNG\r\n\x1a\n" + os.urandom(PART_SIZE))
    data = package.getvalue()
    sessions = {}

    class ExplicitMemoryNative:
        def initialize(self, key, media_type=PDF):
            assert media_type == DOCX
            identity = uuid4().hex
            sessions[identity] = {"key": key, "parts": {}}
            return identity

        def parts(self, key, identity):
            assert key == sessions[identity]["key"]
            return [
                {"part_number": number, "etag": sha(value), "byte_size": len(value)}
                for number, value in sorted(sessions[identity]["parts"].items())
            ]

        def upload(self, key, identity, number, value):
            assert key == sessions[identity]["key"]
            sessions[identity]["parts"][number] = value
            return sha(value)

        def complete(self, key, identity, parts):
            assert parts == self.parts(key, identity)
            w["objects"][key] = b"".join(
                sessions[identity]["parts"][number]
                for number in sorted(sessions[identity]["parts"])
            )

        def verify_complete(self, row):
            from packages.domain.runtime import DomainError

            if row["staging_key"] not in w["objects"]:
                raise DomainError(
                    "MULTIPART_COMPLETE_UNCONFIRMED", "合成native尚未完成", 503, retryable=True
                )
            assert sha(w["objects"][row["staging_key"]]) == row["declared_digest"]

        def abort(self, key, identity):
            raise AssertionError("No abort expected in valid multipart DOCX")

    monkeypatch.setattr(service, "NativeMultipart", ExplicitMemoryNative)
    monkeypatch.setattr(multipart_routes, "NativeMultipart", ExplicitMemoryNative)
    batch = (
        w["client"]
        .post(f"/api/v1/bids/{w['bid']}/upload-batches", json={}, headers=headers(w))
        .json()
    )
    created = w["client"].post(
        f"/api/v1/bids/{w['bid']}/uploads",
        json={
            "batch_id": batch["id"],
            "filename": "large.docx",
            "role": "body",
            "declared_sha256": sha(data),
            "byte_size": len(data),
            "target": {"kind": "new_file"},
        },
        headers=headers(w),
    )
    assert created.status_code == 200, created.text
    grant = created.json()
    assert grant["mode"] == "multipart" and grant["part_count"] == 2
    for part in grant["parts"]:
        number = part["part_number"]
        value = data[(number - 1) * grant["part_size"] : number * grant["part_size"]]
        sent = w["client"].put(
            part["url"],
            content=value,
            headers=headers(w) | {"Content-Type": "application/octet-stream"},
        )
        assert sent.status_code == 200
    actual = (
        w["client"].get(f"/api/v1/uploads/{grant['upload_id']}/parts").json()["completed_parts"]
    )
    tag = w["client"].get(f"/api/v1/uploads/{grant['upload_id']}").headers["etag"]
    finished = w["client"].post(
        f"/api/v1/uploads/{grant['upload_id']}/complete",
        json={"parts": actual, "declared_sha256": sha(data)},
        headers=headers(w, tag),
    )
    assert finished.status_code == 202, finished.text
    _, verified = pump(finished.json()["operation"]["id"])
    assert verified["checked"]["media_type"] == DOCX and verified["word_original_saved"]


@pytest.mark.skipif(
    os.environ.get("RUN_WORD_BRIDGE_NATIVE_OSS") != "1", reason="全新独立Word数据库+OSS前缀"
)
def test_native_oss_cookie_single_render_adopt_parse_and_exact_byte_sha(shared, monkeypatch):
    from uuid import UUID

    from apps.api.word_coordinator import tick

    w = shared
    enable_real_ports(monkeypatch)
    monkeypatch.setattr(storage, "client", REAL_STORAGE_CLIENT)
    monkeypatch.setattr(storage, "get", REAL_STORAGE_GET)
    monkeypatch.setattr(storage, "put", REAL_STORAGE_PUT)
    data = make_docx(
        "原生OSS私有合成Word。施工工期120天，质量合格。受控转换及对应原页检查。" + uuid4().hex
    )
    u = upload(w, data)
    commit(w, u)
    _, rendered = pump(u["verified"]["conversion_operation_id"])
    assert rendered["outcome"]["coverage"] == "complete"
    adopted = tick(w["bid"], UUID(u["version"]))
    assert adopted["state"] == "adopted"
    pump(adopted["parse_operation_id"])
    with connection() as c:
        rows = c.execute(
            "SELECT v.id,b.object_key,b.digest,b.byte_size,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=ANY(%s::uuid[])",
            ([u["version"], adopted["document_version_id"]],),
        ).fetchall()
        assert len(rows) == 2
    for row in rows:
        actual = REAL_STORAGE_GET(row["object_key"])
        assert len(actual) == row["byte_size"] and sha(actual) == row["digest"]
    assert {row["media_type"] for row in rows} == {DOCX, PDF}
    with connection() as c:
        assert fetch(c, "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (w["bid"],))[
            "current_body_id"
        ] == UUID(adopted["document_version_id"])
        parsed = fetch(
            c,
            "SELECT id,coverage FROM parse_runs WHERE document_version_id=%s",
            (adopted["document_version_id"],),
        )
        evidence = fetch(
            c,
            "SELECT id,text_content,physical_page FROM evidence_blocks WHERE parse_id=%s ORDER BY physical_page,ordinal LIMIT 1",
            (parsed["id"],),
        )
        assert evidence["physical_page"] == 1 and evidence["text_content"]
    preview = w["client"].get(f"/api/v1/artifacts/{adopted['document_version_id']}/download")
    assert preview.status_code == 200
    download = w["client"].get(preview.json()["download_url"])
    assert download.status_code == 200 and sha(download.content) == next(
        row["digest"] for row in rows if row["media_type"] == PDF
    )
