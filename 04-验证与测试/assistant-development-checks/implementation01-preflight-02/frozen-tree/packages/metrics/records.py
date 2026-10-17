"""Append-only FR011 records with explicit per-bid monotonic authority epochs."""

from psycopg.types.json import Jsonb

from packages.domain.runtime import clean, fetch, uid

RESULT = "metrics.business_result_recorded"
VERIFY = "metrics.business_result_verified"
RISK = "metrics.submission_risks_frozen"
PREDICTION = "metrics.prediction_frozen"
PAIR = "metrics.submission_prediction_bound"
OBSERVATION = "metrics.review_risks_observed"
EVENTS = (RESULT, VERIFY, RISK, PREDICTION, PAIR, OBSERVATION)


def lock_bid(c, bid):
    capability = c.execute(
        "SELECT pg_has_role(current_user,'tender_api','USAGE') AS api"
    ).fetchone()["api"]
    if capability:
        return fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
    return fetch(c, "SELECT id FROM bids WHERE id=%s", (bid,))


def append(c, *, bid, actor, command, event, payload, identity=None):
    if event not in EVENTS:
        raise ValueError("unregistered metrics record")
    lock_bid(c, bid)
    epoch = c.execute(
        "SELECT coalesce(max((payload->>'metrics_epoch')::bigint),0)+1 AS n FROM audit_entries WHERE bid_id=%s AND event_type=ANY(%s)",
        (bid, list(EVENTS)),
    ).fetchone()["n"]
    event_id = identity or uid()
    value = clean(payload | {"record_schema": "metrics-record/v1", "metrics_epoch": epoch})
    row = c.execute(
        "INSERT INTO audit_entries(id,bid_id,actor_id,command_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp()) RETURNING *",
        (event_id, bid, actor, command, event, Jsonb(value)),
    ).fetchone()
    stream = c.execute(
        "UPDATE stream_heads SET last_seq=last_seq+1 WHERE bid_id=%s RETURNING id,last_seq", (bid,)
    ).fetchone()
    if stream:
        c.execute(
            "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (stream["id"], stream["last_seq"], event_id, event, Jsonb(value), row["created_at"]),
        )
    return row


def rows(c, bid, events=EVENTS, *, as_of=None):
    return c.execute(
        "SELECT * FROM audit_entries WHERE bid_id=%s AND event_type=ANY(%s) AND (%s::timestamptz IS NULL OR created_at<=%s) ORDER BY (payload->>'metrics_epoch')::bigint,id",
        (bid, list(events), as_of, as_of),
    ).fetchall()


def result_head(c, bid, submission, *, as_of=None):
    candidates = [
        r
        for r in rows(c, bid, (RESULT, VERIFY), as_of=as_of)
        if r["payload"]["submission_id"] == str(submission)
    ]
    if not candidates:
        return {"result_id": None, "revision": 0, "authority_id": None}
    value = candidates[-1]
    return {
        "result_id": value["payload"]["result_id"],
        "revision": value["payload"]["head_revision"],
        "authority_id": str(value["id"]),
    }
