"""Actual export/1 + nonowner PG + Memory; hard deny Docker and all provider HTTP."""

import copy
import io
import os
import zipfile
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from test_authoring_rich_integration import images, no_external, rich_api, rich_change, upload_body

from packages.authoring import rich_assets, rich_renderer, snapshot_structure
from packages.authoring.repository import freeze, latest_sections, save_draft, snapshot_content
from packages.domain import storage
from packages.domain.runtime import DomainError, connection, etag

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_RICH_INTEGRATION") != "1",
    reason="Explicit fresh reviewed RICH nonowner profile required",
)

# Register the fixtures imported from the existing explicit Memory/HTTP harness.
_FIXTURES = (images, no_external, rich_api)


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Memory RICH build tests forbid all Docker access")

    monkeypatch.setattr(rich_renderer.sandbox, "_docker", forbidden)
    monkeypatch.setattr(rich_renderer.sandbox, "build_profile", forbidden)
    monkeypatch.delenv("AUTHORING_RICH_RENDER_PROFILE_PATH", raising=False)
    monkeypatch.setattr(storage, "bucket", lambda: "explicit-memory-only")


@pytest.fixture
def environment(images, rich_api):
    api, headers, path = rich_api
    uploaded = api.post(path, headers=headers, json=upload_body(images))
    assert uploaded.status_code == 201, uploaded.json()
    value = uploaded.json()
    with connection() as c:
        rows = latest_sections(c, images["bid"], images["draft"], include_deleted=True)
        state = snapshot_structure.reconcile(None, rows)
        c.execute("UPDATE drafts SET revision=2 WHERE id=%s", (images["draft"],))
        freeze(c, images["bid"], images["draft"], 2, actor=images["actor"], structure_state=state)
        body = rich_change(images, value)
        body["base_draft_revision"] = 2
        saved = save_draft(
            c,
            images["bid"],
            images["draft"],
            images["actor"],
            body,
            etag("draft", images["draft"], 2),
        )
        snapshot = snapshot_content(c, images["bid"], UUID(saved["snapshot_id"]))
    return images | {"asset": value, "snapshot": snapshot, "api": api, "headers": headers}


def request_body(env):
    extension = env["snapshot"]["authoring_structure"]
    return {
        "draft_id": str(env["draft"]),
        "draft_revision": 3,
        "directory_template_version_id": extension["directory_template"]["id"],
        "style_template_version_id": extension["style_template"]["id"],
        "formats": ["docx", "pdf"],
        "follow_up": "none",
        "pagination_mode": "chapter_start_new_page",
    }


def queue(env, body=None):
    result = env["api"].post(
        f"/api/v1/bids/{env['bid']}/builds",
        headers=env["headers"] | {"Idempotency-Key": uuid4().hex},
        json=body or request_body(env),
    )
    assert result.status_code == 202, result.json()
    with connection() as c:
        row = c.execute(
            "SELECT b.id,o.payload FROM builds b JOIN outbox o ON o.operation_id=b.operation_id WHERE b.bid_id=%s AND o.topic='tender.task.export.v1' ORDER BY o.created_at DESC LIMIT 1",
            (env["bid"],),
        ).fetchone()
    return row["id"], row["payload"]


def test_actual_export_no_profile_retains_real_docx_pdf_failed_and_explicit_unknowns(
    environment, monkeypatch
):
    from apps.worker import engine, export_handlers

    env = environment
    build, message = queue(env)
    connections = []
    original_connection = export_handlers.connection

    @contextmanager
    def observed(*args, **kwargs):
        with original_connection(*args, **kwargs) as c:
            connections.append(c)
            yield c

    original_put = storage.put

    def put(*args):
        assert connections and all(c.closed for c in connections)
        return original_put(*args)

    monkeypatch.setattr(export_handlers, "connection", observed)
    monkeypatch.setattr(storage, "put", put)
    lease = engine.acquire(message)
    assert lease
    computed = engine.run_compute(message, lease)
    assert computed["manifest"]["writer_kind"] == "generated_rich/3"
    docx, pdf = computed["outputs"]
    assert docx["state"] == "succeeded" and pdf["state"] == "failed"
    assert "TOC_PAGE_NUMBERS_UNKNOWN" in docx["gaps"]
    assert "ACTUAL_SECTION_PAGE_RANGES_UNKNOWN" in docx["gaps"]
    with zipfile.ZipFile(io.BytesIO(env["objects"][docx["object_key"]])) as archive:
        xml = archive.read("word/document.xml")
        assert b"gridSpan" in xml and b"vMerge" in xml and b"bookmarkStart" in xml
        assert len([name for name in archive.namelist() if name.startswith("word/media/")]) == 1
    assert engine.finish(message, lease, computed)
    assert engine.acquire(message) is None
    view = env["api"].get(f"/api/v1/builds/{build}", headers=env["headers"])
    assert view.status_code == 200, view.json()
    assert view.json()["layout"]["state"] == "partial"
    assert view.json()["layout"]["page_count"] is None
    assert view.json()["follow_up"]["state"] == "not_requested"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM build_outputs WHERE build_id=%s", (build,)
            ).fetchone()["n"]
            == 2
        )
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (env["bid"],)
            ).fetchone()["current_body_id"]
            is None
        )


def test_asset_withdraw_after_compute_rejects_all_publication_but_keeps_old_snapshot(environment):
    from apps.worker import engine

    env = environment
    build, message = queue(env)
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    with connection() as c:
        value = env["asset"]
        rich_assets.withdraw(
            c, env["bid"], UUID(value["asset_id"]), env["actor"], value["etag"], "计算后停用"
        )
    with pytest.raises(DomainError) as error:
        engine.finish(message, lease, computed)
    assert error.value.code == "AUTHORING_IMAGE_STALE"
    with connection() as c:
        assert not c.execute("SELECT 1 FROM build_outputs WHERE build_id=%s", (build,)).fetchone()
        assert not c.execute(
            "SELECT 1 FROM effect_receipts WHERE operation_id=%s", (UUID(message["operation_id"]),)
        ).fetchone()
        assert (
            snapshot_content(c, env["bid"], UUID(env["snapshot"]["snapshot_id"])) == env["snapshot"]
        )


def test_rich_requires_explicit_mode_and_exact_template_versions_and_pdf_for_review(environment):
    env = environment
    original = request_body(env)
    variants = [
        ({key: value for key, value in original.items() if key != "pagination_mode"}, 422),
        (original | {"style_template_version_id": str(uuid4())}, 409),
        (original | {"formats": ["docx"], "follow_up": "review"}, 422),
        (original | {"pagination_mode": "guess"}, 422),
    ]
    for body, status in variants:
        response = env["api"].post(
            f"/api/v1/bids/{env['bid']}/builds",
            headers=env["headers"] | {"Idempotency-Key": uuid4().hex},
            json=body,
        )
        assert response.status_code == status, response.json()
    with connection() as c:
        assert not c.execute("SELECT 1 FROM builds WHERE bid_id=%s", (env["bid"],)).fetchone()


def test_tampered_snapshot_identity_cannot_publish_same_manifest(environment):
    from apps.worker import engine

    env = environment
    build, message = queue(env)
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    bad = copy.deepcopy(computed)
    bad["snapshot_id"] = str(uuid4())
    with pytest.raises(DomainError) as error:
        engine.finish(message, lease, bad)
    assert error.value.code == "BUILD_RESULT_IDENTITY_MISMATCH"
    with connection() as c:
        assert not c.execute("SELECT 1 FROM build_outputs WHERE build_id=%s", (build,)).fetchone()
