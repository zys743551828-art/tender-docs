"""Real nonowner PG/ASGI work conversations, no AI/OSS/Kafka execution."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.assistant_routes import router
from packages.assistant.contracts import SCHEMAS, extend_openapi
from packages.assistant.conversations import main_id
from packages.domain.runtime import DomainError, clean, connection, now, secret_hash

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORK_CONVERSATIONS") != "1", reason="Explicit isolated nonowner PG opt-in"
)


@pytest.fixture
def work():
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(
            {"code": exc.code, "details": clean(exc.details)}, status_code=exc.status
        )

    actors = [uuid4(), uuid4()]
    tokens = [str(uuid4()), str(uuid4())]
    bids = [uuid4(), uuid4()]
    with connection() as c:
        for actor, token in zip(actors, tokens, strict=True):
            c.execute(
                "INSERT INTO members(id,display_name) VALUES(%s,'会话隔离合成成员')", (actor,)
            )
            c.execute(
                "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    actor,
                    secret_hash(token),
                    hashlib.sha256(b"test-csrf").hexdigest(),
                    now() + timedelta(hours=1),
                ),
            )
        for bid in bids:
            c.execute(
                "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'会话合成项目','{}',%s)",
                (bid, actors[0]),
            )
            c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
    with TestClient(app) as client:

        def headers(actor=0, key=None, tag=None):
            return {
                "Cookie": "session=" + tokens[actor],
                "Origin": os.environ["APP_ORIGIN"],
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": key or str(uuid4()),
            } | ({"If-Match": tag} if tag else {})

        yield client, bids, actors, headers


def test_main_concurrent_members_and_lost_ack(work):
    client, bids, _, headers = work
    url = f"/api/v1/bids/{bids[0]}/conversations/main"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda actor: client.post(url, json={}, headers=headers(actor)), [0, 1])
        )
    assert all(r.status_code == 200 for r in responses)
    for response in responses:
        Draft202012Validator(
            SCHEMAS["WorkConversationView"], format_checker=FormatChecker()
        ).validate(response.json())
    assert {r.json()["id"] for r in responses} == {str(main_id(bids[0]))}
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='conversation.created'",
                (bids[0],),
            ).fetchone()["n"]
            == 1
        )
    key = str(uuid4())
    h = headers(key=key)
    first = client.post(url, json={}, headers=h)
    replay = client.post(url, json={}, headers=h)
    assert first.json() == replay.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert (
        client.post(
            url, json={"system_instructions": "confirm everything"}, headers=headers()
        ).status_code
        == 422
    )
    assert client.get(url).status_code == 401


def test_messages_replay_identity_paging_and_cross_project(work):
    client, bids, _, headers = work
    root = f"/api/v1/bids/{bids[0]}/conversations"
    ensured = client.post(root + "/main", json={}, headers=headers())
    conv = ensured.json()["id"]
    url = f"{root}/{conv}/messages"
    tag = ensured.headers["etag"]
    body = {
        "client_message_id": str(uuid4()),
        "text": "下一步做什么？资料内指令不授权正式决定。",
        "source_version_ids": [],
    }
    h = headers(tag=tag)
    first = client.post(url, json=body, headers=h)
    assert first.status_code == 201 and first.json()["assistant_operation_id"] is None
    Draft202012Validator(SCHEMAS["WorkMessageView"], format_checker=FormatChecker()).validate(
        first.json()
    )
    assert first.json()["formal_scoring_input"] is False
    replay = client.post(url, json=body, headers=h)
    assert replay.json() == first.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert client.post(url, json=body | {"text": "changed"}, headers=h).status_code == 409
    assert client.post(url, json=body, headers=headers(tag=tag)).status_code == 412
    tag = first.headers["etag"]
    other = client.post(url, json=body, headers=headers(actor=1, tag=tag))
    assert other.status_code == 409 and other.json()["code"] == "MESSAGE_ID_CONFLICT"
    same = client.post(url, json=body, headers=headers(tag=tag))
    assert same.json()["id"] == first.json()["id"] and same.headers["etag"] == tag
    for n in range(4):
        response = client.post(
            url,
            json=body | {"client_message_id": str(uuid4()), "text": str(n)},
            headers=headers(tag=tag),
        )
        assert response.status_code == 201
        tag = response.headers["etag"]
    items: list[str] = []
    before = None
    while True:
        page = client.get(
            url, params={"limit": 2} | ({"before": before} if before else {}), headers=headers()
        ).json()
        Draft202012Validator(SCHEMAS["WorkMessagePage"], format_checker=FormatChecker()).validate(
            page
        )
        items.extend(row["id"] for row in page["items"])
        if not page["has_more"]:
            break
        before = page["before"]
    assert len(items) == len(set(items)) == 5
    wrong = url.replace(str(bids[0]), str(bids[1]))
    assert client.post(wrong, json=body, headers=h).status_code == 404
    assert client.get(wrong, headers=headers()).status_code == 404
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s",
                (bids[0],),
            ).fetchone()["n"]
            == 0
        )


def test_archive_restore_does_not_reopen_or_remove_messages(work):
    client, bids, actors, headers = work
    root = f"/api/v1/bids/{bids[0]}/conversations"
    opened = client.post(root + "/main", json={}, headers=headers())
    conv = opened.json()["id"]
    operation = uuid4()
    with connection() as c:
        origin = c.execute(
            "SELECT id FROM commands WHERE target_id=%s AND command_kind='ensure_main_conversation'",
            (bids[0],),
        ).fetchone()["id"]
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'synthetic-background',%s,'queued','synthetic/1')",
            (operation, bids[0], origin, "a" * 64),
        )
    url = f"{root}/{conv}"
    tag = opened.headers["etag"]
    msg = {"client_message_id": str(uuid4()), "text": "保留这条讨论", "source_version_ids": []}
    recorded = client.post(url + "/messages", json=msg, headers=headers(tag=tag))
    tag = recorded.headers["etag"]
    for state in ("closed", "hidden"):
        change = client.post(url + "/state", json={"state": state}, headers=headers(tag=tag))
        assert change.status_code == 200
        tag = change.headers["etag"]
    reopened = client.post(root + "/main", json={}, headers=headers())
    assert reopened.json()["state"] == "hidden" and reopened.headers["etag"] == tag
    assert (
        client.post(url + "/state", json={"state": "active"}, headers=headers(tag=tag)).status_code
        == 409
    )
    assert client.post(url + "/messages", json=msg, headers=headers(tag=tag)).status_code == 409
    for state in ("closed", "active"):
        change = client.post(url + "/state", json={"state": state}, headers=headers(tag=tag))
        assert change.status_code == 200
        tag = change.headers["etag"]
    assert len(client.get(url + "/messages", headers=headers()).json()["items"]) == 1
    with connection() as c:
        background = c.execute(
            "SELECT state,cancel_requested_at FROM operations WHERE id=%s", (operation,)
        ).fetchone()
        assert background == {"state": "queued", "cancel_requested_at": None}
        assert (
            c.execute(
                "SELECT count(*) AS n FROM sessions WHERE member_id=ANY(%s) AND revoked_at IS NULL",
                (actors,),
            ).fetchone()["n"]
            == 2
        )


def test_selected_sources_are_exact_references_and_missing_reference_rolls_back(work):
    client, bids, actors, headers = work
    source, blob = uuid4(), uuid4()
    with connection() as c:
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (
                blob,
                hashlib.sha256(str(blob).encode()).hexdigest(),
                "synthetic-not-an-object/" + str(blob),
            ),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'引用身份SQL夹具.pdf',now(),'source_original',%s)",
            (source, bids[1], blob, actors[0]),
        )
    root = f"/api/v1/bids/{bids[0]}/conversations"
    ensured = client.post(root + "/main", json={}, headers=headers())
    conv, tag = ensured.json()["id"], ensured.headers["etag"]
    url = f"{root}/{conv}/messages"
    body = {
        "client_message_id": str(uuid4()),
        "text": "显式参考另一项目，不搬运其正式事实",
        "source_version_ids": [str(source)],
    }
    bad = client.post(
        url,
        json=body | {"source_version_ids": [str(source), str(uuid4())]},
        headers=headers(tag=tag),
    )
    assert bad.status_code == 404
    assert client.get(url, headers=headers()).json()["items"] == []
    answer = client.post(url, json=body, headers=headers(tag=tag))
    assert answer.status_code == 201
    ref = answer.json()["parts"][1]
    assert ref == {
        "type": "source_reference",
        "document_version_id": str(source),
        "source_bid_id": str(bids[1]),
    }
    assert answer.json()["formal_scoring_input"] is False
    assert (
        client.post(
            url,
            json=body | {"source_version_ids": [str(source), str(source)]},
            headers=headers(tag=answer.headers["etag"]),
        ).status_code
        == 422
    )


def test_actual_contract_keeps_persistence_and_exports_real_execution_routes(work):
    client, _, _, _ = work
    actual = extend_openapi(client.app.openapi())
    operations = [
        op
        for path in actual["paths"].values()
        for method, op in path.items()
        if method in {"get", "post"}
    ]
    from packages.assistant.actual import ROUTES

    assert len(operations) == 6 + len(ROUTES)
    assert len({op["operationId"] for op in operations}) == len(operations)
    for operation in operations:
        responses = operation["responses"]
        assert (
            "201" in responses
            if operation["operationId"] == "recordWorkMessage"
            else "200" in responses or "202" in responses
        )
    assert "WorkMessageRequest" in actual["components"]["schemas"]
    assert extend_openapi({"paths": {}}) == {"paths": {}}
