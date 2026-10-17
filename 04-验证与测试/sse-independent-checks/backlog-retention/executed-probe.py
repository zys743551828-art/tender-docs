"""Actual SSE backlog, reverse proxy and append-only temporal fixtures; no model work."""

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
    candidate = Path(
        "/workspace/tender-docs/04-验证与测试/event-stream-development-checks/candidate.json"
    )
    declared = json.loads(candidate.read_text())
    for row in declared["sources"]:
        source = Path(row["path"])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row["sha256"]
        target = out / "frozen-inputs" / source.relative_to(Path.cwd())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    member = json.loads(Path(".local/external-test-members.json").read_text())[0]
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        scope="Actual live HTTP/PG and5175 proxy SSE, own append-only temporal fixture; source SSE unchanged across main release",
    )

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    with httpx.Client(base_url="http://127.0.0.1:8802", trust_env=False, timeout=12) as client:
        login = client.post(
            "/api/v1/auth/login",
            json=dict(login_name=member["login"], password=member["password"]),
            headers={"Origin": "http://127.0.0.1:5175"},
        )
        assert login.status_code == 200
        h = {
            "Origin": "http://127.0.0.1:5175",
            "X-CSRF-Token": login.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        bid = client.post(
            "/api/v1/bids", json={"title": "独立SSE积压合成-" + uuid4().hex[:8]}, headers=h
        ).json()["id"]
        snapshot = client.get(f"/api/v1/bids/{bid}").json()
        stream = snapshot["stream_id"]
        with connection() as c:
            original = c.execute(
                "SELECT last_seq FROM stream_heads WHERE id=%s FOR UPDATE", (stream,)
            ).fetchone()["last_seq"]
            assert original == 1
            for seq in range(2, 252):
                c.execute(
                    "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload) VALUES(%s,%s,%s,'qa.backlog',%s)",
                    (
                        stream,
                        seq,
                        uuid4(),
                        Jsonb(
                            {
                                "revision": True if seq == 2 else seq,
                                "private_prose": "SYNTHETIC_PRIVATE_CANARY",
                            }
                        ),
                    ),
                )
            c.execute("UPDATE stream_heads SET last_seq=251 WHERE id=%s", (stream,))

        def read(selected_stream, after, n, base=None):
            changes = []
            wire = []
            started = time.monotonic()
            with client.stream(
                "GET",
                (base or "") + f"/api/v1/streams/{selected_stream}/events",
                headers={} if after is None else {"Last-Event-ID": f"{selected_stream}:{after}"},
            ) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    wire.append(line)
                    if line.startswith("data: "):
                        changes.append(json.loads(line[6:]))
                        if len(changes) == n:
                            break
            return changes, "\n".join(wire), time.monotonic() - started

        rows, wire, seconds = read(stream, 0, 251)
        check(
            "251_event_backlog_drains_across_200_row_batches_in_order",
            [r["seq"] for r in rows] == list(range(1, 252)),
            {"seconds": seconds},
        )
        check(
            "backlog_private_prose_absent_boolean_not_revision",
            all(r["object_ref"] == {"kind": "bid", "id": bid} for r in rows)
            and "SYNTHETIC_PRIVATE_CANARY" not in wire
            and rows[1]["revision"] is None,
        )
        replay, _, _ = read(stream, 200, 51)
        check(
            "resume_inside_backlog_has_exact_new_suffix",
            [r["seq"] for r in replay] == list(range(201, 252)),
        )
        proxy, _, proxy_seconds = read(stream, 250, 1, "http://127.0.0.1:5175")
        check(
            "same_origin5175_proxy_flushes_real_sse_frame",
            proxy[0]["seq"] == 251 and proxy_seconds < 5,
            {"seconds": proxy_seconds},
        )
        old_bid, old_stream = uuid4(), uuid4()
        with connection() as c:
            who = c.execute(
                "SELECT member_id FROM credentials WHERE login_name=%s", (member["login"],)
            ).fetchone()["member_id"]
            c.execute(
                "INSERT INTO bids(id,title,settings,created_by) SELECT %s,'独立SSE八天历史夹具',settings,%s FROM bids WHERE id=%s",
                (old_bid, who, bid),
            )
            c.execute(
                "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) SELECT %s,round_id,bidder_id,'preparation' FROM bid_profiles WHERE bid_id=%s",
                (old_bid, bid),
            )
            c.execute(
                "INSERT INTO stream_heads(id,bid_id,last_seq) VALUES(%s,%s,2)",
                (old_stream, old_bid),
            )
            c.execute(
                "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload,created_at) VALUES(%s,1,%s,'qa.old','{}',now()-interval '8 days'),(%s,2,%s,'qa.current','{}',now())",
                (old_stream, uuid4(), old_stream, uuid4()),
            )
        response = client.get(
            f"/api/v1/streams/{old_stream}/events", headers={"Last-Event-ID": f"{old_stream}:0"}
        )
        check(
            "eight_day_cursor410_requires_snapshot_reset",
            response.status_code == 410 and response.json()["code"] == "RESET_REQUIRED",
        )
        renewed = client.get(f"/api/v1/bids/{old_bid}")
        rows, _, _ = read(old_stream, None, 1)
        check(
            "snapshot_refresh_then_new_connection_only_retained_event",
            renewed.status_code == 200
            and renewed.json()["stream_id"] == str(old_stream)
            and [r["seq"] for r in rows] == [2],
        )
        anonymous = httpx.get(
            f"http://127.0.0.1:8802/api/v1/streams/{stream}/events", trust_env=False
        )
        check("anonymous_stream401", anonymous.status_code == 401)
        api = client.get("/openapi.json").content
        (out / "actual.openapi.json").write_bytes(api)
        result.update(
            passed=all(c["passed"] for c in result["checks"]),
            bid_id=bid,
            temporal_fixture_bid_id=str(old_bid),
            source_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            live_openapi_sha256=hashlib.sha256(api).hexdigest(),
            live_path_count=len(json.loads(api)["paths"]),
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
