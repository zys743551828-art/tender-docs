"""Closed work cannot be recreated via editor or knowledge entrypoints; old bytes stay readable."""

import os
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api import authoring
from packages.authoring.repository import create_draft, draft_view, save_draft
from packages.domain.runtime import DomainError, connection, etag
from packages.knowledge import bridge
from tests.test_authoring_integration import change
from tests.test_knowledge_rag_integration import (
    cite_body,
    indexed,
    publish,
    traced,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_KNOWLEDGE_LIFECYCLE") != "1", reason="Isolated nonowner PG only"
)


@pytest.fixture
def draft():
    from tests.test_authoring_integration import draft as fixture

    return fixture.__wrapped__()


@pytest.fixture
def environment(monkeypatch):
    from tests.test_knowledge_rag_integration import environment as fixture

    return fixture.__wrapped__(monkeypatch)


def close(bid, mode):
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute(
            "UPDATE bid_profiles SET phase=%s,archived_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE bid_id=%s",
            ("closed" if mode == "archived" else mode, mode == "archived", bid),
        )


@pytest.mark.parametrize("mode", ["closed", "terminated", "archived"])
def test_editor_new_work_is_atomic_but_original_ack_and_snapshot_remain(draft, monkeypatch, mode):
    actor, bid, draft_id, original, section = draft
    app = FastAPI()
    app.include_router(authoring.router)
    monkeypatch.setattr(authoring, "auth", lambda *args: {"member_id": actor})

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"code": error.code}, status_code=error.status)

    body = change(section, "关闭前确实保存的正文")
    headers = {"Idempotency-Key": uuid4().hex, "If-Match": etag("draft", draft_id, 1)}
    path = f"/api/v1/bids/{bid}/drafts/{draft_id}"
    with TestClient(app) as client:
        first = client.patch(path, json=body, headers=headers)
        assert first.status_code == 200
        close(bid, mode)
        replay = client.patch(path, json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert replay.headers["Idempotent-Replayed"] == "true"
        current = client.get(path)
        assert current.status_code == 200 and current.json()["revision"] == 2
        denied = client.patch(path, json=body, headers=headers | {"Idempotency-Key": uuid4().hex})
        assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_WORK_CLOSED"
        added = client.post(path + "/sections", json={"title": "不得偷偷续编"}, headers=headers)
        assert added.status_code == 409 and added.json()["code"] == "PROJECT_WORK_CLOSED"
        restored = client.post(
            f"/api/v1/bids/{bid}/sections/{section['section_id']}/restore",
            json={"revision": 1, "base_draft_revision": 2, "base_section_revision": 2},
            headers=headers,
        )
        assert restored.status_code == 409 and restored.json()["code"] == "PROJECT_WORK_CLOSED"
        # The same persisted command cannot be returned under a foreign project route.
        _, other_bid, _, _, _ = draft_fixture_for_other_project()
        foreign = client.patch(
            f"/api/v1/bids/{other_bid}/drafts/{draft_id}", json=body, headers=headers
        )
        assert foreign.status_code == 404
    for action in (
        lambda c: create_draft(c, bid, actor, "无权新增"),
        lambda c: save_draft(c, bid, draft_id, actor, body, etag("draft", draft_id, 2)),
    ):
        with pytest.raises(DomainError) as error, connection() as c:
            action(c)
        assert error.value.code == "PROJECT_WORK_CLOSED"
    with connection() as c:
        assert draft_view(c, bid, draft_id)["revision"] == 2
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (draft_id,)
            ).fetchone()["n"]
            == 2
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM commands WHERE command_kind='save-draft' AND target_id=%s",
                (draft_id,),
            ).fetchone()["n"]
            == 1
        )


def draft_fixture_for_other_project():
    from tests.test_authoring_integration import draft as fixture

    return fixture.__wrapped__()


def test_archived_target_cannot_gain_either_reference_but_origin_archive_allows_index(environment):
    from packages.knowledge.rag_search import cite_unit, reference_history

    env = environment
    active = publish(env)
    # The historical source's project can be archived while the enterprise item remains useful.
    close(env["origin"], "archived")
    index, _ = indexed(env)
    _, result = traced(env, index, limit=1)
    body = cite_body(result["hits"][0]) | {"target_object_id": str(env["target"])}
    trace_id = UUID(result["trace_id"])
    with connection() as c:
        existing = cite_unit(c, trace_id, env["actor"], body, None)
    close(env["target"], "archived")
    with pytest.raises(DomainError) as error, connection() as c:
        cite_unit(c, trace_id, env["actor"], body, None)
    assert error.value.code == "PROJECT_WORK_CLOSED"
    with pytest.raises(DomainError) as error, connection() as c:
        bridge.cite(
            c, env["target"], env["item"], UUID(active["version_id"]), env["actor"], {}, None
        )
    assert error.value.code == "PROJECT_WORK_CLOSED"
    with connection() as c:
        historical = reference_history(c, UUID(existing["citation_id"]))
        assert historical["reference"] == existing
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (env["target"],)
            ).fetchone()["n"]
            == 1
        )
