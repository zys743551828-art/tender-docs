"""持久SSE的游标、秘密投影、断线重放与会话撤销；真实PG须显式启用。"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from apps.api import event_routes as streams
from packages.domain.runtime import DomainError, connection, secret_hash


def test_cursor_binds_stream_and_rejects_invalid_input():
    stream = uuid4()
    assert streams.cursor_value(None, stream) is None
    assert streams.cursor_value(f"{stream}:3", stream) == 3
    for value in (f"{uuid4()}:3", f"{stream}:-1", f"{stream}:１", f"{stream}:{2**63}", "bad"):
        with pytest.raises(DomainError) as error:
            streams.cursor_value(value, stream)
        assert error.value.status == 422


@pytest.fixture
def fixture():
    if os.environ.get("RUN_EVENT_PG") != "1":
        pytest.skip("Requires explicit isolated PG fixture database")
    member, session, bid, stream = (uuid4() for _ in range(4))
    token = str(uuid4())
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'SSE合成检验')", (member,))
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (session, member, secret_hash(token), secret_hash("synthetic csrf")),
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'SSE合成测试','{}',%s)",
            (bid, member),
        )
        c.execute("INSERT INTO stream_heads(id,bid_id,last_seq) VALUES(%s,%s,2)", (stream, bid))
        for seq in (1, 2):
            c.execute(
                "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload) VALUES(%s,%s,%s,'test.changed',%s)",
                (
                    stream,
                    seq,
                    uuid4(),
                    Jsonb(
                        {
                            "revision": seq,
                            "private_text": "不应回放正文",
                            "signed_url": "private_grant",
                        }
                    ),
                ),
            )
    return {"member": member, "session": session, "bid": bid, "stream": stream, "token": token}


def test_real_replay_order_and_private_payload_projection(fixture):
    stream, session = fixture["stream"], fixture["session"]
    after, rows = streams.read_batch(stream, None, session)
    assert after == 0 and [row["seq"] for row in rows] == [1, 2]
    _, replay = streams.read_batch(stream, 1, session)
    assert [row["seq"] for row in replay] == [2]
    wire = "".join(streams.frame(stream, row) for row in rows)
    assert "不应回放正文" not in wire and "private_grant" not in wire
    assert "event: change\n" in wire
    assert json.loads(wire.split("data: ")[1].split("\n")[0])["object_ref"]["id"] == str(
        fixture["bid"]
    )
    with pytest.raises(DomainError) as error:
        streams.read_batch(stream, 3, session)
    assert error.value.code == "RESET_REQUIRED" and error.value.status == 410


def test_real_expired_cursor_returns_reset_and_new_snapshot_can_connect(fixture):
    stream, session = fixture["stream"], fixture["session"]
    with connection() as c:
        c.execute("UPDATE stream_heads SET last_seq=3 WHERE id=%s", (stream,))
        # Append-only events are never mutated to manufacture retention fixtures.
        c.execute(
            "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload,created_at) VALUES(%s,3,%s,'old.changed','{}',now()-interval '8 days')",
            (stream, uuid4()),
        )
        expired_stream = uuid4()
        c.execute(
            "INSERT INTO stream_heads(id,bid_id,last_seq) VALUES(%s,%s,1)",
            (expired_stream, fixture["bid"]),
        )
        c.execute(
            "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload,created_at) VALUES(%s,1,%s,'old.changed','{}',now()-interval '8 days')",
            (expired_stream, uuid4()),
        )
    with pytest.raises(DomainError) as error:
        streams.read_batch(expired_stream, 0, session)
    assert error.value.status == 410
    assert streams.read_batch(expired_stream, None, session) == (1, [])


def test_real_session_revocation_stops_next_batch(fixture):
    streams.read_batch(fixture["stream"], 0, fixture["session"])
    with connection() as c:
        c.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (fixture["session"],))
    with pytest.raises(DomainError) as error:
        streams.read_batch(fixture["stream"], 0, fixture["session"])
    assert error.value.status == 401


def test_real_revocation_during_stream_emits_control_and_closes(fixture):
    class Connected:
        async def is_disconnected(self):
            return False

    async def run():
        cursor, initial = streams.read_batch(fixture["stream"], 1, fixture["session"])
        body = streams.event_body(
            Connected(), fixture["stream"], fixture["session"], cursor, initial
        )
        assert "event: change" in await anext(body)
        with connection() as c:
            c.execute("UPDATE sessions SET revoked_at=now() WHERE id=%s", (fixture["session"],))
        control = await anext(body)
        assert "event: control" in control and "SESSION_EXPIRED" in control
        with pytest.raises(StopAsyncIteration):
            await anext(body)

    asyncio.run(run())


def test_real_http_cookie_replay_and_status_before_headers(fixture, monkeypatch):
    monkeypatch.setattr(streams, "CONNECTION_SECONDS", 0.03)
    monkeypatch.setattr(streams, "POLL_SECONDS", 0.005)
    app = FastAPI()
    app.include_router(streams.router)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = f"/api/v1/streams/{fixture['stream']}/events"
            assert (await client.get(path)).status_code == 401
            client.cookies.set("session", fixture["token"])
            invalid = await client.get(path, headers={"Last-Event-ID": f"{fixture['stream']}:999"})
            assert invalid.status_code == 410 and invalid.json()["code"] == "RESET_REQUIRED"
            response = await client.get(path, headers={"Last-Event-ID": f"{fixture['stream']}:1"})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            assert f"id: {fixture['stream']}:2\n" in response.text
            assert f"id: {fixture['stream']}:1\n" not in response.text
            assert "private_grant" not in response.text

    asyncio.run(run())
