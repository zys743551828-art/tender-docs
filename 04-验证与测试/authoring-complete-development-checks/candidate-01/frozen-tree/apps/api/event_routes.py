"""会话认证的持久事件流；只通知刷新，不转播审计原始内容。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from apps.api.auth import auth
from packages.domain.runtime import DomainError, connection, fetch, now

router = APIRouter()
POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15.0
CONNECTION_SECONDS = 55.0
RETENTION = timedelta(days=7)


def cursor_value(value: str | None, stream_id: UUID) -> int | None:
    if value is None:
        return None
    try:
        identity, sequence = value.split(":")
        if UUID(identity) != stream_id or not sequence.isascii() or not sequence.isdecimal():
            raise ValueError
        result = int(sequence)
        if result < 0 or result > 2**63 - 1:
            raise ValueError
        return result
    except (ValueError, AttributeError):
        raise DomainError(
            "INVALID_EVENT_CURSOR", "事件游标不属于此项目，请重新读取快照", 422
        ) from None


def read_batch(stream_id: UUID, after: int | None, session_id: UUID) -> tuple[int, list[dict]]:
    with connection() as c:
        # One consistent view of head and retained events; no transaction survives a yield.
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        active = c.execute(
            """SELECT 1 FROM sessions s JOIN members m ON m.id=s.member_id
               WHERE s.id=%s AND revoked_at IS NULL AND expires_at>now() AND m.active""",
            (session_id,),
        ).fetchone()
        if not active:
            raise DomainError("SESSION_EXPIRED", "登录已过期，请重新登录", 401)
        head = fetch(c, "SELECT bid_id,last_seq FROM stream_heads WHERE id=%s", (stream_id,))
        cutoff = now() - RETENTION
        start = c.execute(
            "SELECT min(seq) AS first FROM stream_events WHERE stream_id=%s AND created_at>=%s",
            (stream_id, cutoff),
        ).fetchone()["first"]
        minimum = start - 1 if start is not None else head["last_seq"]
        if after is None:
            after = minimum
        if after < minimum or after > head["last_seq"]:
            raise DomainError(
                "RESET_REQUIRED",
                "事件游标已失效，请先重新读取项目和操作快照",
                410,
                {"stream_id": str(stream_id)},
            )
        rows = c.execute(
            """SELECT seq,event_type,payload FROM stream_events
               WHERE stream_id=%s AND seq>%s AND created_at>=%s ORDER BY seq LIMIT 200""",
            (stream_id, after, cutoff),
        ).fetchall()
        # Payloads can contain private prose or short-lived grants. Never replay them.
        changes = []
        for row in rows:
            payload = row["payload"] if isinstance(row["payload"], dict) else {}
            revision = payload.get("revision")
            changes.append(
                {
                    "stream_id": str(stream_id),
                    "seq": row["seq"],
                    "event_type": row["event_type"],
                    "object_ref": {"kind": "bid", "id": str(head["bid_id"])},
                    "revision": revision if type(revision) is int and revision > 0 else None,
                }
            )
        return after, changes


def frame(stream_id: UUID, change: dict) -> str:
    data = json.dumps(change, ensure_ascii=False, separators=(",", ":"))
    return f"id: {stream_id}:{change['seq']}\nevent: change\ndata: {data}\n\n"


async def event_body(request, stream_id, session_id, cursor, initial):
    started = heartbeat = time.monotonic()
    pending = initial
    while time.monotonic() - started < CONNECTION_SECONDS:
        if await request.is_disconnected():
            return
        for change in pending:
            cursor = change["seq"]
            yield frame(stream_id, change)
        if pending:
            # Drain a backlog without an artificial per-batch delay.
            await asyncio.sleep(0)
        else:
            if time.monotonic() - heartbeat >= KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(POLL_SECONDS)
        try:
            cursor, pending = await run_in_threadpool(read_batch, stream_id, cursor, session_id)
        except DomainError as exc:
            # Headers have been sent. Explicit control event then close; no stale events follow.
            code = exc.code if exc.status in (401, 410) else "STREAM_UNAVAILABLE"
            yield f"event: control\ndata: {json.dumps({'code': code})}\n\n"
            return
        except psycopg.Error:
            yield 'event: control\ndata: {"code":"STREAM_UNAVAILABLE"}\n\n'
            return
    yield ": reconnect\n\n"


@router.get("/api/v1/streams/{stream_id}/events")
async def read_events(stream_id: UUID, request: Request):
    actor = await run_in_threadpool(auth, request)
    cursor = cursor_value(request.headers.get("last-event-id"), stream_id)
    cursor, initial = await run_in_threadpool(read_batch, stream_id, cursor, actor["id"])
    return StreamingResponse(
        event_body(request, stream_id, actor["id"], cursor, initial),
        media_type="text/event-stream",
        headers={"Cache-Control": "private,no-store", "X-Accel-Buffering": "no", "Vary": "Cookie"},
    )
