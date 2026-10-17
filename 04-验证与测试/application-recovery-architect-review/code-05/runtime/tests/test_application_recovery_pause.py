"""AR03: bounded asynchronous ASGI creation, replay and unknown-outcome fences."""

import asyncio
import json
from uuid import uuid4

import pytest
from starlette.responses import JSONResponse

from deploy.application_recovery.application import PausedApplication


def fixture():
    return {"ids": {name: str(uuid4()) for name in ("bid", "knowledge", "report")}}


async def request(app, key, *, cookie="same-session", csrf="same-csrf"):
    sent = []
    raw = json.dumps(app.create_body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/bids",
        "headers": [
            (b"idempotency-key", key.encode()),
            (b"cookie", cookie.encode()),
            (b"origin", b"http://127.0.0.1:5199"),
            (b"x-csrf-token", csrf.encode()),
        ],
    }
    await app(scope, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    return status, json.loads(
        b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
    )


def test_architect_manual_bind_counterexample_now_rejects_second_create():
    async def inner(*args):
        pytest.fail("a second create must not reach the application")

    app = PausedApplication(inner, fixture())
    app.bind_new_bid(str(uuid4()))
    assert asyncio.run(request(app, "new-key"))[0] == 423


def test_first_success_same_command_replay_and_different_key_session_denials():
    calls, rows = [], {}

    async def inner(scope, receive, send):
        headers = dict(scope["headers"])
        key = headers[b"idempotency-key"]
        calls.append(key)
        identity = rows.setdefault(key, str(uuid4()))
        await JSONResponse({"id": identity}, status_code=201)(scope, receive, send)

    async def scenario():
        app = PausedApplication(inner, fixture())
        first = await request(app, "first-key")
        assert first[0] == 201 and app.new_bid == first[1]["id"]
        assert await request(app, "first-key") == first
        assert (await request(app, "second-key"))[0] == 423
        assert (await request(app, "first-key", cookie="another-member"))[0] == 423
        assert (await request(app, "first-key", csrf="another-csrf"))[0] == 423

    asyncio.run(scenario())
    assert len(rows) == 1 and len(calls) == 2


def test_concurrent_distinct_keys_dispatch_only_one_create():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def inner(scope, receive, send):
            calls.append(dict(scope["headers"])[b"idempotency-key"])
            entered.set()
            await release.wait()
            await JSONResponse({"id": str(uuid4())}, status_code=201)(scope, receive, send)

        app = PausedApplication(inner, fixture())
        first = asyncio.create_task(request(app, "first-key"))
        await entered.wait()
        second = asyncio.create_task(request(app, "second-key"))
        release.set()
        a, b = await asyncio.gather(first, second)
        assert (a[0], b[0]) == (201, 423) and len(calls) == 1

    asyncio.run(scenario())


def test_real_csrf_rejection_does_not_consume_the_success_slot():
    async def inner(scope, receive, send):
        if dict(scope["headers"])[b"x-csrf-token"] != b"same-csrf":
            return await JSONResponse({"code": "CSRF_REJECTED"}, status_code=403)(
                scope, receive, send
            )
        return await JSONResponse({"id": str(uuid4())}, status_code=201)(scope, receive, send)

    async def scenario():
        app = PausedApplication(inner, fixture())
        assert (await request(app, "failed-probe", csrf="missing"))[0] == 403
        assert app.new_bid is None and app.create_command is None
        assert (await request(app, "success-key"))[0] == 201

    asyncio.run(scenario())


def test_unknown_lost_response_never_permits_a_new_command():
    rows = {}

    async def inner(scope, receive, send):
        key = dict(scope["headers"])[b"idempotency-key"]
        if key not in rows:
            rows[key] = str(uuid4())
            raise RuntimeError("synthetic lost response after application commit")
        await JSONResponse({"id": rows[key]}, status_code=201)(scope, receive, send)

    async def scenario():
        app = PausedApplication(inner, fixture())
        with pytest.raises(RuntimeError):
            await request(app, "unknown-key")
        assert (await request(app, "another-key"))[0] == 423
        assert (await request(app, "unknown-key"))[1]["id"] == rows[b"unknown-key"]

    asyncio.run(scenario())
    assert len(rows) == 1
