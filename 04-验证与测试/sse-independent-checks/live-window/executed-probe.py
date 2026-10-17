"""Actual unchanged8802 SSE:55s reconnect/keepalive/replay/session revocation."""

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from packages.domain.runtime import connection


def main():
    out = Path(os.environ["QA_SSE_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    member = json.loads(Path(".local/external-test-members.json").read_text())[0]
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        scope="Actual TCP8802 stream, real release PG own synthetic metadata; no review scheduling",
    )

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    with httpx.Client(base_url="http://127.0.0.1:8802", trust_env=False, timeout=65) as client:
        origin = "http://127.0.0.1:5175"
        login = client.post(
            "/api/v1/auth/login",
            json=dict(login_name=member["login"], password=member["password"]),
            headers={"Origin": origin},
        )
        assert login.status_code == 200
        headers = {
            "Origin": origin,
            "X-CSRF-Token": login.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        bid = client.post(
            "/api/v1/bids", json=dict(title="独立SSE窗口-" + uuid4().hex[:8]), headers=headers
        ).json()["id"]
        stream = client.get(f"/api/v1/bids/{bid}").json()["stream_id"]
        path = f"/api/v1/streams/{stream}/events"
        canary = "SYNTHETIC_PRIVATE_PROSE_" + uuid4().hex

        def append():
            with connection() as c:
                head = c.execute(
                    "UPDATE stream_heads SET last_seq=last_seq+1 WHERE id=%s RETURNING last_seq",
                    (stream,),
                ).fetchone()
                seq = head["last_seq"]
                c.execute(
                    "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload) VALUES(%s,%s,%s,'qa.private.changed',%s)",
                    (
                        stream,
                        seq,
                        uuid4(),
                        Jsonb(
                            dict(
                                revision=seq,
                                private_text=canary,
                                signed_url="SYNTHETIC_GRANT_SHOULD_NOT_REPLAY",
                                object_ref=dict(kind="foreign", id=str(uuid4())),
                            )
                        ),
                    ),
                )
            return seq

        def take(after, expected, logout=False):
            frames = []
            lines = []
            with client.stream(
                "GET", path, headers={"Last-Event-ID": f"{stream}:{after}"}
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    lines.append(line)
                    if line.startswith("data: "):
                        frame = json.loads(line[6:])
                        frames.append(frame)
                        if logout and "seq" in frame:
                            with httpx.Client(
                                base_url="http://127.0.0.1:8802",
                                trust_env=False,
                                cookies=client.cookies,
                            ) as other:
                                ended = other.post("/api/v1/auth/logout", headers=headers)
                                assert ended.status_code == 204
                        if logout and frame.get("code") == "SESSION_EXPIRED":
                            break
                        if not logout and frame.get("seq") == expected:
                            break
            return frames, lines

        first_seq = append()
        frames, lines = take(first_seq - 1, first_seq)
        check(
            "private_payload_projected_to_metadata_only",
            frames[0]["seq"] == first_seq
            and frames[0]["object_ref"] == dict(kind="bid", id=bid)
            and canary not in "\n".join(lines)
            and "SYNTHETIC_GRANT" not in "\n".join(lines),
        )
        started = time.monotonic()
        heartbeats = 0
        reconnect = False
        with client.stream(
            "GET", path, headers={"Last-Event-ID": f"{stream}:{first_seq}"}
        ) as response:
            check(
                "stream_cache_and_proxy_headers",
                response.status_code == 200
                and "no-store" in response.headers.get("Cache-Control", "")
                and response.headers.get("X-Accel-Buffering") == "no"
                and "Cookie" in response.headers.get("Vary", ""),
            )
            for line in response.iter_lines():
                if line == ": keepalive":
                    heartbeats += 1
                if line == ": reconnect":
                    reconnect = True
        duration = time.monotonic() - started
        check(
            "real_55second_window_keeps_alive_and_requests_reconnect",
            reconnect and heartbeats >= 3 and 50 <= duration < 65,
            dict(seconds=duration, keepalives=heartbeats),
        )
        next_seq = append()
        frames, lines = take(first_seq, next_seq)
        check("reconnect_cursor_replays_only_new_event", [f["seq"] for f in frames] == [next_seq])
        with client.stream(
            "GET", path, headers={"Last-Event-ID": f"{stream}:{next_seq + 1000}"}
        ) as response:
            future_status = response.status_code
        with client.stream("GET", path, headers={"Last-Event-ID": f"{uuid4()}:0"}) as response:
            foreign_status = response.status_code
        check(
            "future_cursor410_cross_project_cursor422_before_stream",
            future_status == 410 and foreign_status == 422,
        )
        final_seq = append()
        frames, lines = take(next_seq, final_seq, logout=True)
        check(
            "logout_during_actual_stream_control_then_close",
            [f.get("seq") for f in frames if "seq" in f] == [final_seq]
            and frames[-1] == dict(code="SESSION_EXPIRED"),
        )
        check("revoked_session_cannot_reconnect", client.get(path).status_code == 401)
        result.update(
            bid_id=bid, stream_id=stream, passed=all(c["passed"] for c in result["checks"])
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
