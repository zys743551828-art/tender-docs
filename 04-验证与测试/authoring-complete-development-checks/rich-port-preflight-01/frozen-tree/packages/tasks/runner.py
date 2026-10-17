"""API-role bounded task scheduler/coordinator; no model I/O or Kafka consumer."""

import argparse
import json
import signal
import threading
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, assert_role, connection, uid
from packages.tasks import coordinator

STOP = threading.Event()


def cycle(after=None, limit=50):
    with connection() as c:
        rows = c.execute(
            "SELECT b.id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE p.archived_at IS NULL AND p.phase NOT IN('closed','terminated') AND (%s::uuid IS NULL OR b.id>%s::uuid) AND EXISTS(SELECT FROM task_definitions d WHERE d.bid_id=b.id) ORDER BY b.id LIMIT %s",
            (after, after, limit + 1),
        ).fetchall()
    processed = errors = 0
    for row in rows[:limit]:
        try:
            with connection() as c:
                coordinator.tick(c, bid=row["id"])
                coordinator.pending_events(c, bid=row["id"])
                # Per-bid notification batches are bounded; resume via recorded cursor audit.
                cursor = c.execute(
                    "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='task.notification_scan_cursor' ORDER BY (payload->>'epoch')::bigint DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                value = coordinator.reconcile_notifications(
                    c, bid=row["id"], after=(cursor["payload"]["next_after"] if cursor else None)
                )
                if value["next_after"] != (cursor["payload"]["next_after"] if cursor else None):
                    epoch = (cursor["payload"]["epoch"] if cursor else 0) + 1
                    c.execute(
                        "INSERT INTO audit_entries(id,bid_id,event_type,payload,created_at) VALUES(%s,%s,'task.notification_scan_cursor',%s,clock_timestamp())",
                        (
                            uid(),
                            row["id"],
                            Jsonb({"next_after": value["next_after"], "epoch": epoch}),
                        ),
                    )

            processed += 1
        except DomainError:
            errors += 1
    return {
        "processed_projects": processed,
        "blocked_projects": errors,
        "next_after": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        "provider_calls": 0,
        "consumer_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--after", type=UUID)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60:
        parser.error("interval must be between 1 and 60 seconds")
    assert_role()
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    after = args.after
    while not STOP.is_set():
        try:
            result = cycle(after)
        except Exception as error:
            print(json.dumps({"status": "failed", "code": type(error).__name__}), flush=True)
            raise SystemExit(1) from None
        print(json.dumps(result), flush=True)
        after = result["next_after"]
        if args.once:
            break
        STOP.wait(args.interval)


if __name__ == "__main__":
    main()
