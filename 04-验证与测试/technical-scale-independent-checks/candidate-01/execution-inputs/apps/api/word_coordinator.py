"""API-role controlled Word PDF adoption; no rendering/model calls or Worker API credentials."""

import argparse
import json
import signal
import threading
from uuid import UUID

import psycopg

from packages.domain.parsing_profile import configured_parser
from packages.domain.runtime import DomainError, assert_role, connection, fetch
from packages.domain.word_materials import (
    ADOPTION,
    SELECTION,
    adopt,
    candidate,
    configured,
    selection,
    verify_candidate,
)

STOP = threading.Event()


def tick(bid, source):
    with connection() as c:
        value = candidate(c, bid, source)
        chosen = selection(c, bid, source) if value else None
        if not value or not chosen:
            return {"state": "not_ready"}
    verified = verify_candidate(value)
    parser = configured_parser()
    from packages.word_intake.renderer import configured_profile

    render_profile = configured_profile()
    with connection() as c:
        return adopt(c, bid, source, verified, parser, render_profile=render_profile)


def cycle(source=None):
    with connection() as c:
        if not configured(c):
            return {"state": "not_configured", "processed": 0}
        if source:
            row = fetch(
                c, "SELECT bid_id FROM word_intake_receipts WHERE document_version_id=%s", (source,)
            )
            rows = [{"bid_id": row["bid_id"], "source": source}]
        else:
            rows = c.execute(
                "SELECT w.bid_id,w.document_version_id AS source FROM word_intake_receipts w JOIN bid_profiles p ON p.bid_id=w.bid_id JOIN LATERAL(SELECT id FROM audit_entries WHERE bid_id=w.bid_id AND event_type=%s AND payload->>'source_document_version_id'=w.document_version_id::text ORDER BY (payload->'context'->>'material_revision')::bigint DESC,created_at DESC,id DESC LIMIT 1)s ON true JOIN LATERAL(SELECT operation_id,outcome FROM word_render_runs WHERE bid_id=w.bid_id AND source_document_version_id=w.document_version_id ORDER BY created_at DESC,operation_id DESC LIMIT 1)r ON true WHERE p.archived_at IS NULL AND p.phase NOT IN('closed','terminated') AND r.outcome->>'state'='succeeded' AND r.outcome->>'coverage'='complete' AND NOT EXISTS(SELECT 1 FROM audit_entries a WHERE a.bid_id=w.bid_id AND a.event_type=%s AND a.payload->>'selection_id'=s.id::text AND a.payload->>'conversion_operation_id'=r.operation_id::text) ORDER BY w.created_at,w.document_version_id LIMIT 100",
                (SELECTION, ADOPTION),
            ).fetchall()
    done = 0
    for row in rows:
        try:
            tick(row["bid_id"], row["source"])
            done += 1
        except DomainError as error:
            print(json.dumps({"event": "word_adoption_blocked", "code": error.code}), flush=True)
    return {"processed": done, "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--document-version-id", type=UUID)
    args = parser.parse_args()
    assert_role()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            result = cycle(args.document_version_id)
        except psycopg.Error:
            print(json.dumps({"event": "word_adoption_dependency_failure"}), flush=True)
        else:
            if args.once:
                print(json.dumps(result), flush=True)
        if args.once:
            return
        STOP.wait(2)


if __name__ == "__main__":
    main()
