"""Real nonowner PG/ASGI drafts, privacy, CAS and explicit template saving; no external work."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from apps.api.task_draft_routes import router
from apps.api.views import bid_etag
from packages.domain.runtime import connection

pytest_plugins = ["tests.test_task_templates_integration"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TASK_DRAFTS") != "1", reason="Explicit isolated nonowner PG opt-in"
)


@pytest.fixture
def draft_world(world):
    client, bids, actors, headers, create_template = world
    client.app.include_router(router)
    return client, bids, actors, headers, create_template


def create(world, text="每天核对否决项", **extra):
    client, bids, _, headers, _ = world
    with connection() as c:
        tag = bid_etag(c, bids[0])
    url = f"/api/v1/bids/{bids[0]}/task-definition-drafts"
    h = headers(tag=tag)
    body = {"request": text} | extra
    result = client.post(url, json=body, headers=h)
    assert result.status_code == 201, result.text
    return url, result, h, body


def configuration(**changes):
    return {
        "name": "显式编辑任务",
        "task_kind": "denial_check",
        "instructions": "保留待补事实，仅核对",
        "trigger": {"mode": "manual"},
    } | changes


def assert_no_execution(bid):
    with connection() as c:
        for table in (
            "task_definitions",
            "task_runs",
            "schedules",
            "operations",
        ):
            assert (
                c.execute(f"SELECT count(*) AS n FROM {table} WHERE bid_id=%s", (bid,)).fetchone()[
                    "n"
                ]
                == 0
            )
        for table in ("model_calls", "outbox"):
            assert (
                c.execute(
                    f"SELECT count(*) AS n FROM {table} t JOIN operations o ON o.id=t.operation_id WHERE o.bid_id=%s",
                    (bid,),
                ).fetchone()["n"]
                == 0
            )


def test_ambiguous_draft_lost_ack_is_private_and_never_schedules(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, h, body = create(draft_world, "每天核对否决项 private-request-marker")
    value = first.json()
    assert value["revision"] == 1 and value["interpretation"]["unresolved"]
    assert value["interpretation"]["trigger"]["mode"] == "manual"
    replay = client.post(url, json=body, headers=h)
    assert replay.json() == value and replay.headers["Idempotent-Replayed"] == "true"
    get = client.get(url + "/" + value["id"], headers=headers())
    assert get.json() == value
    assert client.get(url + "/" + value["id"], headers=headers(1)).status_code == 404
    assert client.get(url + "/" + value["id"]).status_code == 401
    with connection() as c:
        payloads = c.execute(
            "SELECT e.payload FROM stream_events e JOIN stream_heads h ON h.id=e.stream_id WHERE h.bid_id=%s",
            (bids[0],),
        ).fetchall()
        assert "private-request-marker" not in json.dumps(payloads)
    assert_no_execution(bids[0])


def test_edit_revision_cas_and_explicit_template_save_are_separate(draft_world):
    client, bids, _, headers, save = draft_world
    url, first, _, _ = create(draft_world)
    url += "/" + first.json()["id"]
    body = configuration()
    h = headers(tag=first.headers["etag"])
    updated = client.put(url, json=body, headers=h)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2 and updated.json()["explicitly_edited"]
    interpretation = updated.json()["interpretation"]
    assert interpretation["raw_request"] == first.json()["interpretation"]["raw_request"]
    assert interpretation["unresolved"] == [] and interpretation["ready_for_template_form"]
    assert client.put(url, json=body, headers=h).json() == updated.json()
    assert (
        client.put(
            url, json=body | {"name": "stale"}, headers=headers(tag=first.headers["etag"])
        ).status_code
        == 412
    )
    assert_no_execution(bids[0])
    created = save(client, body)
    assert created.json()["configuration"]["trigger"]["mode"] == "manual"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM task_definitions WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 1
        )
        for table in ("operations", "task_runs", "schedules"):
            assert (
                c.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE bid_id=%s", (bids[0],)
                ).fetchone()["n"]
                == 0
            )


def test_cross_project_actor_and_untrusted_selected_version_are_refused(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, _, _ = create(draft_world)
    identity = first.json()["id"]
    wrong = f"/api/v1/bids/{bids[1]}/task-definition-drafts/{identity}"
    assert client.get(wrong, headers=headers()).status_code == 404
    assert (
        client.put(
            wrong, json=configuration(), headers=headers(tag=first.headers["etag"])
        ).status_code
        == 404
    )
    assert (
        client.put(
            url + "/" + identity,
            json=configuration(),
            headers=headers(1, tag=first.headers["etag"]),
        ).status_code
        == 404
    )
    selected = configuration(
        source_selection={"mode": "selected_versions", "version_ids": [str(uuid4())]}
    )
    assert (
        client.put(
            url + "/" + identity, json=selected, headers=headers(tag=first.headers["etag"])
        ).status_code
        == 404
    )
    assert client.get(url + "/" + identity, headers=headers()).json()["revision"] == 1
    assert_no_execution(bids[0])


def test_concurrent_draft_edits_have_one_winner_and_keep_original(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, _, _ = create(draft_world)
    url += "/" + first.json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda i: client.put(
                    url, json=configuration(name=str(i)), headers=headers(tag=first.headers["etag"])
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in results) == [200, 412]
    with connection() as c:
        rows = c.execute(
            "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='task.draft_revision' ORDER BY (payload->>'revision')::integer",
            (bids[0],),
        ).fetchall()
        assert len(rows) == 2 and rows[0]["payload"] == first.json()
    assert_no_execution(bids[0])


def test_closed_archive_preserves_read_and_old_ack_but_refuses_new_work(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, h, body = create(draft_world)
    with connection() as c:
        c.execute(
            "UPDATE bid_profiles SET phase='terminated',archived_at=clock_timestamp() WHERE bid_id=%s",
            (bids[0],),
        )
    assert client.get(url + "/" + first.json()["id"], headers=headers()).status_code == 200
    assert client.post(url, json=body, headers=h).json() == first.json()
    assert client.post(url, json=body, headers=headers(tag=h["If-Match"])).status_code == 409
    assert (
        client.put(
            url + "/" + first.json()["id"],
            json=configuration(),
            headers=headers(tag=first.headers["etag"]),
        ).status_code
        == 409
    )
    assert_no_execution(bids[0])
