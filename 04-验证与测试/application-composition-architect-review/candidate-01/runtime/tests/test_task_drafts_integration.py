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
    assert updated.json()["created_at"] == first.json()["created_at"]
    assert updated.json()["updated_at"] >= first.json()["updated_at"]
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


def test_unresolved_blocks_save_and_explicit_form_edit_then_single_save_recovers(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, _, _ = create(draft_world)
    url += "/" + first.json()["id"]
    denied = client.post(url + "/save", json={}, headers=headers(tag=first.headers["etag"]))
    assert denied.status_code == 409 and denied.json()["code"] == "TASK_DRAFT_NEEDS_CLARIFICATION"
    assert_no_execution(bids[0])
    updated = client.put(url, json=configuration(), headers=headers(tag=first.headers["etag"]))
    h = headers(tag=updated.headers["etag"])
    saved = client.post(url + "/save", json={}, headers=h)
    assert saved.status_code == 201, saved.text
    assert saved.json()["configuration"]["trigger"]["mode"] == "manual"
    replay = client.post(url + "/save", json={}, headers=h)
    assert replay.json() == saved.json() and replay.headers["Idempotent-Replayed"] == "true"
    current = client.get(url, headers=headers())
    assert current.headers["etag"] != updated.headers["etag"]
    assert current.json()["revision"] == updated.json()["revision"] + 1
    assert current.json()["created_at"] == updated.json()["created_at"]
    assert current.json()["updated_at"] >= updated.json()["updated_at"]
    assert (
        client.post(
            url + "/save", json={}, headers=headers(tag=updated.headers["etag"])
        ).status_code
        == 412
    )
    duplicate = client.post(url + "/save", json={}, headers=headers(tag=current.headers["etag"]))
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "TASK_DRAFT_ALREADY_SAVED"
    assert (
        client.put(
            url,
            json=configuration(name="不再编辑已保存草案"),
            headers=headers(tag=current.headers["etag"]),
        ).status_code
        == 409
    )
    assert client.get(url, headers=headers()).json()["saved_template_id"] == saved.json()["id"]
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


def test_daily_draft_does_not_create_schedule_until_explicit_save_and_no_run(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, _, _ = create(draft_world, "每天9:30核对否决条款")
    assert_no_execution(bids[0])
    saved = client.post(
        url + "/" + first.json()["id"] + "/save",
        json={},
        headers=headers(tag=first.headers["etag"]),
    )
    assert saved.status_code == 201 and saved.json()["next_run_at"] is not None
    with connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM schedules WHERE bid_id=%s", (bids[0],)).fetchone()[
                "n"
            ]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )


def test_two_save_commands_race_to_one_template_and_old_ack_survives_archive(draft_world):
    client, bids, _, headers, _ = draft_world
    url, first, _, _ = create(draft_world, "核对当前项目否决条款")
    url += "/" + first.json()["id"] + "/save"
    requests = [headers(tag=first.headers["etag"]) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda h: client.post(url, json={}, headers=h), requests))
    assert sorted(r.status_code for r in responses) == [201, 412]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 201)
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM task_definitions WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 1
        )
        c.execute(
            "UPDATE bid_profiles SET phase='terminated',archived_at=clock_timestamp() WHERE bid_id=%s",
            (bids[0],),
        )
    replay = client.post(url, json={}, headers=requests[winner])
    assert replay.status_code == 201 and replay.json() == responses[winner].json()
    assert client.post(url, json={}, headers=headers(tag=first.headers["etag"])).status_code == 409


def test_private_draft_directory_restores_current_versions_with_scoped_pagination(draft_world):
    client, bids, _, headers, _ = draft_world
    ids = []
    for _ in range(3):
        url, value, _, _ = create(draft_world)
        ids.append(value.json()["id"])
    first = client.get(url, params={"limit": 2}, headers=headers())
    assert first.status_code == 200 and len(first.json()["items"]) == 2 and first.json()["has_more"]
    cursor = first.json()["next_cursor"]
    second = client.get(url, params={"limit": 2, "cursor": cursor}, headers=headers())
    assert len(second.json()["items"]) == 1 and not second.json()["has_more"]
    assert {v["id"] for v in first.json()["items"] + second.json()["items"]} == set(ids)
    assert client.get(url, headers=headers(1)).json()["items"] == []
    assert client.get(url, params={"cursor": cursor}, headers=headers(1)).status_code == 422
    assert (
        client.get(
            f"/api/v1/bids/{bids[1]}/task-definition-drafts",
            params={"cursor": cursor},
            headers=headers(),
        ).status_code
        == 422
    )
    assert_no_execution(bids[0])
