"""Real restricted PG and Cookie/CSRF HTTP; storage transport is explicitly in-memory.

The optional real renderer case uses the actual Docker sandbox. No Kafka consumer,
provider model or external object-store request is started by this suite.
"""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api import word_intake_routes
from apps.api.auth import csrf
from apps.api.word_intake_routes import router
from apps.worker import word_intake_handlers as worker
from packages.domain import storage
from packages.domain.runtime import (
    Command,
    DomainError,
    assert_role,
    connection,
    enqueue,
    fetch,
    now,
    secret_hash,
    sha,
)
from packages.word_intake import repository
from packages.word_intake.inspection import PDF, inspect_upload
from packages.word_intake.renderer import configured_profile
from tests.test_word_intake import make_docx

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORD_INTAKE_INTEGRATION") != "1",
    reason="需要精确Word SQL的全新独立PG账号；不补运行库",
)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "synthetic-word-session-secret-for-tests")
    monkeypatch.setenv("CSRF_SECRET", "synthetic-word-csrf-secret-for-tests")
    monkeypatch.setenv("APP_ORIGIN", "http://word-intake.test")
    actor, bid, project, round_id, bidder, session_id = [uuid4() for _ in range(6)]
    token = uuid4().hex
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成Word用户')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成外来Word测试','{}',%s)",
            (bid, actor),
        )
        c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成工程')", (project,))
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                session_id,
                actor,
                secret_hash(token),
                hashlib.sha256(csrf(token).encode()).hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    objects = {}
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(
            {"code": error.code, "message": error.message}, status_code=error.status
        )

    with TestClient(app) as client:
        client.cookies.set("session", token)
        yield {
            "actor": actor,
            "bid": bid,
            "objects": objects,
            "client": client,
            "csrf": csrf(token),
        }


def original(world, data, *, filename="body.docx", role="body", source=None, profile=None):
    actor, bid = world["actor"], world["bid"]
    batch, upload, file, binding, blob, version = [uuid4() for _ in range(6)]
    checked = inspect_upload(filename, data, sha(data), len(data))
    key = "synthetic/" + sha(data)
    world["objects"][key] = data
    with connection() as c:
        cmd = Command(c, actor, "word_fixture_original", bid, str(uuid4()), {})
        c.execute(
            "INSERT INTO upload_batches(id,bid_id,revision,state,created_by) VALUES(%s,%s,1,'open',%s)",
            (batch, bid, actor),
        )
        c.execute(
            "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
            (file, bid, role),
        )
        c.execute(
            "INSERT INTO uploads(id,bid_id,batch_id,filename,role,declared_digest,declared_size,staging_key,state,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'authorized',%s)",
            (
                upload,
                bid,
                batch,
                filename,
                role,
                sha(data),
                len(data),
                "staging/" + str(upload),
                now() + timedelta(days=1),
            ),
        )
        c.execute(
            "INSERT INTO upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,reason,command_id,created_by) VALUES(%s,%s,%s,%s,1,'合成明确目标',%s,%s)",
            (binding, bid, upload, file, cmd.id, actor),
        )
        c.execute(
            "INSERT INTO upload_transfers(upload_id,bid_id,file_id,expected_file_revision,mode,part_size,part_count,revision,current_binding_id) VALUES(%s,%s,%s,1,'single',%s,1,1,%s)",
            (upload, bid, file, len(data), binding),
        )
        if source:
            repository.declare_pdf_source(c, bid, upload, source, actor, cmd.id)
        operation = enqueue(c, bid, cmd.id, "verify-upload", {"id": upload, "digest": sha(data)})
        cmd.finish({"fixture": True})
    with connection(True) as c:
        existing = c.execute("SELECT id FROM blobs WHERE digest=%s", (sha(data),)).fetchone()
        if existing:
            blob = existing["id"]
        else:
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s)",
                (blob, sha(data), key, len(data), checked["media_type"]),
            )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),'bid_body',%s)",
            (version, bid, blob, filename, actor),
        )
        c.execute(
            "UPDATE uploads SET state='verified',verified_version_id=%s,verified_retain_until=now()+interval '7 days' WHERE id=%s",
            (version, upload),
        )
        row = fetch(c, "SELECT * FROM uploads WHERE id=%s", (upload,))
        published = repository.publish_verified(
            c, {"operation_id": operation["id"]}, row, version, checked, render_profile=profile
        )
    return {
        "upload": upload,
        "version": version,
        "file": file,
        "published": published,
        "checked": checked,
    }


def disabled():
    identity = {"protocol": "word-docker-render/1", "enabled": False}
    return {"identity": identity, "profile_digest": sha(identity)}


def message_for(operation):
    with connection(True) as c:
        return fetch(c, "SELECT payload FROM outbox WHERE operation_id=%s", (operation,))["payload"]


def test_nonowner_real_original_saved_failure_and_pdf_followup(world, monkeypatch):
    monkeypatch.delenv("WORD_RENDER_PROFILE_PATH", raising=False)
    assert_role()
    assert_role(True)
    word = original(world, make_docx(), profile=disabled())
    message = message_for(word["published"]["conversion_operation_id"])
    computed = worker.compute(message)
    assert computed["outcome"]["error_code"] == "WORD_RENDER_SANDBOX_UNAVAILABLE"
    with connection(True) as c:
        worker.publish(c, message, computed)
    path = f"/api/v1/bids/{world['bid']}/word-originals/{word['version']}"
    response = world["client"].get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["original_saved"] is True
    assert body["message"] == "Word原件已保存／本次对应检查未完成"
    assert body["conversion"]["state"] == "failed"
    assert not body["conversion"]["retryable"]
    assert body["primary_action"]["context"]["declared_source_document_version_id"] == str(
        word["version"]
    )
    assert world["client"].get(path.replace(str(world["bid"]), str(uuid4()))).status_code == 404
    pdf = original(
        world,
        b"%PDF-1.7\nexplicit-synthetic-byte-fixture",
        filename="body.pdf",
        source=word["version"],
    )
    with connection() as c:
        relation = fetch(
            c, "SELECT * FROM document_relations WHERE document_version_id=%s", (pdf["version"],)
        )
        assert (
            relation["kind"] == "user_declared_same_draft"
            and relation["verification"] == "unverified"
        )
        assert pdf["file"] != word["file"]
        with pytest.raises(DomainError) as caught:
            repository.assert_file_format(c, word["file"], PDF)
        assert caught.value.code == "FILE_FORMAT_MISMATCH"
    listed = world["client"].get(f"/api/v1/bids/{world['bid']}/word-originals").json()
    assert any(row["id"] == str(word["version"]) for row in listed["items"])
    world["client"].cookies.clear()
    assert world["client"].get(path).status_code == 401


def test_sql_rejects_control_plane_result_forgery_and_json_null(world):
    word = original(world, make_docx("SQL permission fixture"), profile=disabled())
    operation = word["published"]["conversion_operation_id"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:
            c.execute(
                "INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile,outcome) VALUES(%s,%s,%s,%s,%s,%s)",
                (uuid4(), world["bid"], word["version"], "0" * 64, Jsonb({}), Jsonb({})),
            )
    invalid = {
        "state": None,
        "coverage": "complete",
        "gaps": [],
        "chain": {},
        "conversion_attempted": False,
    }
    with pytest.raises(psycopg.errors.CheckViolation):
        with connection(True) as c:
            c.execute(
                "UPDATE word_render_runs SET outcome=%s,finished_at=now() WHERE operation_id=%s",
                (Jsonb(invalid), operation),
            )
    with connection() as c:
        assert (
            fetch(c, "SELECT outcome FROM word_render_runs WHERE operation_id=%s", (operation,))[
                "outcome"
            ]
            is None
        )


def test_cross_role_source_and_retry_without_csrf_are_rejected(world):
    word = original(world, make_docx("PDF relation scope"), profile=disabled())
    with pytest.raises(DomainError) as caught:
        original(
            world, b"%PDF-1.7\ntender", filename="tender.pdf", role="tender", source=word["version"]
        )
    assert caught.value.code == "WORD_PDF_ROLE_MISMATCH"
    path = f"/api/v1/bids/{world['bid']}/word-originals/{word['version']}/retry-conversion"
    assert world["client"].post(path, json={}).status_code == 403


def test_recoverable_configuration_keeps_original_and_replays_accepted_retry(world, monkeypatch):
    word = original(world, make_docx("Configuration recovery 120 days"), profile=disabled())
    message = message_for(word["published"]["conversion_operation_id"])
    computed = worker.compute(message)
    with connection(True) as c:
        worker.publish(c, message, computed)
    identity = {"protocol": "explicit-test-capability-only", "enabled": True}
    profile = {"identity": identity, "profile_digest": sha(identity)}
    monkeypatch.setattr(word_intake_routes, "configured_profile", lambda: profile)
    path = f"/api/v1/bids/{world['bid']}/word-originals/{word['version']}"
    view = world["client"].get(path)
    assert view.json()["conversion"]["retryable"] is True
    headers = {
        "Origin": "http://word-intake.test",
        "X-CSRF-Token": world["csrf"],
        "Idempotency-Key": str(uuid4()),
        "If-Match": view.headers["etag"],
    }
    retry = world["client"].post(path + "/retry-conversion", json={}, headers=headers)
    assert retry.status_code == 202
    repeated = world["client"].post(path + "/retry-conversion", json={}, headers=headers)
    assert repeated.status_code == 202 and repeated.headers["Idempotent-Replayed"] == "true"

    def unavailable():
        raise DomainError("WORD_RENDER_PROFILE_UNAVAILABLE", "synthetic configuration removal", 503)

    monkeypatch.setattr(word_intake_routes, "configured_profile", unavailable)
    replay_after_config_removed = world["client"].post(
        path + "/retry-conversion", json={}, headers=headers
    )
    assert replay_after_config_removed.status_code == 202
    assert replay_after_config_removed.headers["Idempotent-Replayed"] == "true"
    assert world["client"].get(path).status_code == 200


@pytest.mark.skipif(
    os.environ.get("RUN_WORD_RENDER_REAL") != "1", reason="可选真实Docker转换，未配置不冒充已执行"
)
def test_real_docker_to_restricted_pg_success_and_exact_chain(world):
    profile = configured_profile()
    source = make_docx("施工工期120天，质量合格。")
    word = original(world, source, profile=profile)
    message = message_for(word["published"]["conversion_operation_id"])
    computed = worker.compute(message)
    assert computed["outcome"]["state"] == "succeeded"
    assert computed["outcome"]["coverage"] == "complete"
    assert repository.controlled_qualified(computed["outcome"])
    with connection(True) as c:
        result = worker.publish(c, message, computed)
    with connection() as c:
        relation = fetch(
            c,
            "SELECT * FROM document_relations WHERE document_version_id=%s",
            (result["pdf_document_version_id"],),
        )
        assert relation["kind"] == "controlled_render" and relation["verification"] == "verified"
        run = fetch(
            c, "SELECT * FROM word_render_runs WHERE operation_id=%s", (message["operation_id"],)
        )
        assert run["outcome"]["chain"]["source_docx_digest"] == sha(source)
        pdf = fetch(
            c,
            "SELECT b.digest,b.object_key FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
            (run["pdf_document_version_id"],),
        )
        assert pdf["digest"] == sha(world["objects"][pdf["object_key"]])
        assert pdf["digest"] == run["outcome"]["chain"]["pdf_digest"]
        assert not result["word_render_adoption"]["source_still_current_word_body"]
    output = os.environ.get("WORD_INTAKE_RESULT_DIRECTORY")
    if output:
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / "real-word-source.docx").write_bytes(source)
        (path / "real-word-output.pdf").write_bytes(world["objects"][pdf["object_key"]])
        (path / "real-word-chain.json").write_text(
            json.dumps(computed["outcome"], ensure_ascii=False, indent=2) + "\n"
        )
