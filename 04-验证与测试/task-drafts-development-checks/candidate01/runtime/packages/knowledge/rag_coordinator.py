"""API-role recoverable knowledge indexing intents; no global Kafka consumer or relay."""

from __future__ import annotations

import argparse
import signal
import threading
from uuid import UUID

from packages.domain.runtime import Command, DomainError, assert_role, connection, sha
from packages.knowledge.rag_index import CHUNK_POLICY
from packages.knowledge.rag_repository import assert_schema, clock, schedule_index
from packages.knowledge.repository import lifecycle, version_row
from packages.rag.http_port import DenseHttp, RuntimeUnavailable
from packages.rag.indexing import ensure_profile

STOP = threading.Event()


def schedule_once(knowledge_ids: list[UUID] | None = None):
    dense = DenseHttp()  # Private runtime identity I/O before DB work.
    with connection() as c:
        assert_schema(c)
        profile = ensure_profile(c, dense.identity)
        rows = c.execute(
            """SELECT i.id,i.published_version_id,v.created_by FROM retrieval.knowledge_items i
            JOIN retrieval.knowledge_versions v ON v.id=i.published_version_id
            WHERE (%s::uuid[] IS NULL OR i.id=ANY(%s::uuid[])) AND NOT EXISTS(SELECT 1 FROM retrieval.knowledge_index_heads h
              JOIN retrieval.knowledge_indexes ki ON ki.id=coalesce(h.ready_index_id,h.desired_index_id)
              JOIN operations op ON op.id=ki.operation_id
              WHERE h.knowledge_version_id=v.id AND h.profile_id=%s
                AND ki.lexical_dictionary_digest=%s AND ki.chunk_policy_digest=%s
                AND (ki.state='ready' OR ki.state='queued' AND op.state IN('queued','running')))
            ORDER BY i.id LIMIT 100""",
            (
                knowledge_ids,
                knowledge_ids,
                profile,
                dense.lexical_dictionary_digest,
                sha(CHUNK_POLICY),
            ),
        ).fetchall()
    count = 0
    for row in rows:
        with connection() as c:
            version = version_row(c, row["id"], row["published_version_id"])
            if lifecycle(c, version, clock(c)) != "published":
                continue
            body = {
                "knowledge_version_id": str(row["published_version_id"]),
                "profile_id": str(profile),
                "dictionary_digest": dense.lexical_dictionary_digest,
                "chunk_policy_digest": sha(CHUNK_POLICY),
            }
            cmd = Command(c, row["created_by"], "auto-index-knowledge", row["id"], sha(body), body)
            if cmd.replay:
                continue
            result = schedule_index(
                c,
                row["id"],
                row["created_by"],
                cmd.id,
                dense,
                expected_version_id=UUID(body["knowledge_version_id"]),
            )
            cmd.finish(result, 202)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--knowledge-id", type=UUID, action="append", dest="knowledge_ids")
    args = parser.parse_args()
    assert_role()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            count = schedule_once(args.knowledge_ids)
            if args.once:
                print({"scheduled": count, "paid_calls": 0})
        except (DomainError, RuntimeUnavailable):
            if args.once:
                raise SystemExit("Knowledge indexing is unavailable; no paid calls") from None
        if args.once:
            break
        STOP.wait(5)


if __name__ == "__main__":
    main()
