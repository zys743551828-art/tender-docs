"""Explicit opt-in real PAGE sandbox + restricted PG + actual export engine, no Kafka/model."""

import hashlib
import io
import json
import os
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import test_authoring_integration as legacy
import test_authoring_structure_integration as structure

from packages.authoring import generated_renderer
from packages.authoring.build_repository import configured_render_identity, freeze_build
from packages.authoring.repository import save_draft, snapshot_content
from packages.domain import storage
from packages.domain.runtime import (
    Command,
    DomainError,
    connection,
    enqueue_plan,
    etag,
    now,
    secret_hash,
    uid,
)
from packages.word_intake import renderer as sandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_GENERATED_INTEGRATION") != "1",
    reason="Explicit isolated fresh PG and approved PAGE sandbox required; no paid calls",
)


@pytest.fixture(scope="module")
def installed_profile(tmp_path_factory):
    base = sandbox.build_profile()
    value = generated_renderer.profile_for(base)
    path = tmp_path_factory.mktemp("authoring-generated-profile") / "profile.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, value


@pytest.fixture
def environment(installed_profile, monkeypatch):
    import httpx
    import requests

    from packages.domain.providers.deepseek import DeepSeekClient

    def forbidden_remote(*args, **kwargs):
        raise AssertionError("This mechanism test forbids external HTTP and model providers")

    async def forbidden_async_remote(*args, **kwargs):
        forbidden_remote()

    # TestClient's in-process transport remains usable. PG and the explicitly
    # approved Docker socket are independent of these real HTTP transports.
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_remote)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_remote)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async_remote)
    monkeypatch.setattr(DeepSeekClient, "from_env", forbidden_remote)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)
    actor, bid, draft, old, section, saved = structure.structured.__wrapped__()
    monkeypatch.setenv("AUTHORING_GENERATED_RENDER_PROFILE_PATH", str(installed_profile[0]))
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", objects.__getitem__)

    class MemoryClient:
        def get_object(self, *, Bucket, Key):
            return {"Body": io.BytesIO(objects[Key])}

    monkeypatch.setattr(storage, "client", MemoryClient)
    return {
        "actor": actor,
        "bid": bid,
        "draft": draft,
        "section": section,
        "saved": saved,
        "objects": objects,
        "profile": installed_profile[1],
    }


def build_request(env, follow_up="none"):
    with connection() as c:
        snapshot = snapshot_content(c, env["bid"], UUID(env["saved"]["snapshot_id"]))
    extension = snapshot["authoring_structure"]
    return {
        "draft_id": str(env["draft"]),
        "draft_revision": env["saved"]["draft_revision"],
        "directory_template_version_id": extension["directory_template"]["id"],
        "style_template_version_id": extension["style_template"]["id"],
        "formats": ["docx", "pdf"],
        "follow_up": follow_up,
    }


def queue_build(env, follow_up="none"):
    body = build_request(env, follow_up)
    renderer = configured_render_identity(body["formats"], generated=True)
    assert renderer == env["profile"]
    with connection() as c:
        cmd = Command(c, env["actor"], "generated-build-real-test", env["bid"], str(uid()), body)
        input_id, manifest = freeze_build(c, env["bid"], body, env["actor"], renderer)
        operation = enqueue_plan(
            c,
            env["bid"],
            cmd.id,
            "build",
            {"id": str(input_id), "manifest": manifest},
            stages=[{"key": "build-document", "depends_on": []}],
            handler_version="export/1",
            topic="tender.task.export.v1",
        )
        build = uid()
        c.execute(
            "INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (build, env["bid"], input_id, UUID(operation["id"])),
        )
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.export.v1'",
            (UUID(operation["id"]),),
        ).fetchone()["payload"]
        cmd.finish({"build_id": str(build)})
    return build, message, manifest


def test_actual_registered_export_engine_closes_db_then_publishes_verified_PAGE(
    environment, monkeypatch
):
    from apps.worker import engine, export_handlers

    env = environment
    build, message, manifest = queue_build(env)
    connections = []
    original_connection = export_handlers.connection

    @contextmanager
    def observed_connection(*args, **kwargs):
        with original_connection(*args, **kwargs) as c:
            connections.append(c)
            yield c

    original_render = generated_renderer.render

    def observed_render(*args, **kwargs):
        assert connections and all(c.closed for c in connections)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(export_handlers, "connection", observed_connection)
    monkeypatch.setattr(generated_renderer, "render", observed_render)
    lease = engine.acquire(message)
    assert lease
    computed = engine.run_compute(message, lease)
    assert computed["render_coverage"] == "complete", computed
    assert computed["conversion_chain"]["page_count"] >= 2
    assert all(p["footer_matches"] for p in computed["render_correspondence"]["pages"])
    assert engine.finish(message, lease, computed)
    assert engine.acquire(message) is None
    with connection() as c:
        outputs = c.execute(
            "SELECT format,state,document_version_id FROM build_outputs WHERE build_id=%s ORDER BY format",
            (build,),
        ).fetchall()
        assert len(outputs) == 2 and all(o["state"] == "succeeded" for o in outputs)
        relation = c.execute(
            "SELECT r.* FROM document_relations r JOIN build_outputs b ON b.document_version_id=r.document_version_id WHERE b.build_id=%s AND b.format='pdf'",
            (build,),
        ).fetchone()
        assert relation["verification"] == "verified" and relation["kind"] == "controlled_render"
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (env["bid"],)
            ).fetchone()["current_body_id"]
            is None
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (UUID(message["operation_id"]),),
            ).fetchone()["n"]
            == 1
        )
    assert manifest["render_protocol"] == "authoring-generated-sandbox/1"


def test_real_http_cancel_discards_computed_late_PAGE_without_publication(environment):
    from fastapi.testclient import TestClient

    from apps.api.main import app
    from apps.worker import engine

    env = environment
    build, message, _ = queue_build(env)
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    assert computed["render_coverage"] == "complete"
    token = uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                env["actor"],
                secret_hash(token),
                hashlib.sha256(b"explicit-page-csrf").hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    with TestClient(app) as api:
        path = f"/api/v1/operations/{message['operation_id']}"
        headers = {"Cookie": "session=" + token}
        current = api.get(path, headers=headers)
        assert current.status_code == 200
        response = api.post(
            path + "/cancel",
            headers=headers
            | {
                "Origin": os.environ["APP_ORIGIN"],
                "X-CSRF-Token": "explicit-page-csrf",
                "Idempotency-Key": uuid4().hex,
                "If-Match": current.headers["ETag"],
            },
            json={"reason": "合成取消验证", "cancel_pending_same_input": False},
        )
        assert response.status_code == 200, response.json()
    assert engine.finish(message, lease, computed) is False
    with connection() as c:
        assert not c.execute("SELECT 1 FROM build_outputs WHERE build_id=%s", (build,)).fetchone()
        assert not c.execute(
            "SELECT 1 FROM effect_receipts WHERE operation_id=%s", (UUID(message["operation_id"]),)
        ).fetchone()


def test_old_verified_PAGE_result_stays_history_after_editor_moves(environment):
    from apps.worker import engine
    from packages.domain.build_followups import tick

    env = environment
    build, message, _ = queue_build(env, "review")
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    assert computed["render_coverage"] == "complete"
    content = env["section"]["content"]
    content["content"][0]["content"] = [{"type": "text", "text": "后续编辑不能被旧生成抢头"}]
    with connection() as c:
        save_draft(
            c,
            env["bid"],
            env["draft"],
            env["actor"],
            {
                "base_draft_revision": 2,
                "changes": [
                    {
                        "section_id": str(env["section"]["section_id"]),
                        "base_section_revision": 1,
                        "schema_digest": env["section"]["schema_digest"],
                        "content": content,
                    }
                ],
            },
            etag("draft", env["draft"], 2),
        )
    assert engine.finish(message, lease, computed)
    outcome = tick(build)
    assert outcome["state"] == "needs_selection" and outcome["adopted_document_version_id"] is None
    with connection() as c:
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (env["bid"],)
            ).fetchone()["current_body_id"]
            is None
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM build_outputs WHERE build_id=%s AND state='succeeded'",
                (build,),
            ).fetchone()["n"]
            == 2
        )


def test_final_source_withdrawal_blocks_computed_PAGE_publish(environment):
    from apps.worker import engine
    from packages.authoring.structure_repository import save_plan

    env = environment
    with connection() as c:
        ref, ancestor = legacy.create_authoritative_source(c, env["bid"], env["actor"])
        env["saved"] = save_plan(
            c,
            env["bid"],
            env["draft"],
            env["section"]["section_id"],
            env["actor"],
            {
                "base_draft_revision": 2,
                "base_plan_revision": 0,
                "state": "drafting",
                "goal": "引用需最终有效",
                "key_points": [],
                "rule_ids": [],
                "source_refs": [ref],
                "pending_facts": [],
                "planned_pages": None,
                "budget_basis": "manual",
                "budget_rule_id": None,
                "budget_note": "",
            },
            etag("draft", env["draft"], 2),
        )
    build, message, _ = queue_build(env)
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    assert computed["render_coverage"] == "complete"
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (ancestor,),
        )
    with pytest.raises(DomainError) as error:
        engine.finish(message, lease, computed)
    assert error.value.code == "AUTHORING_SOURCE_STALE"
    with connection() as c:
        assert not c.execute("SELECT 1 FROM build_outputs WHERE build_id=%s", (build,)).fetchone()
        assert not c.execute(
            "SELECT 1 FROM effect_receipts WHERE operation_id=%s", (UUID(message["operation_id"]),)
        ).fetchone()
