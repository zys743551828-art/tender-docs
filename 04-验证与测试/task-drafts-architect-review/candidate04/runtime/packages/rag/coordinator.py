"""Recoverable API control-plane index scheduling; it never calls a paid generation model."""

from __future__ import annotations

import argparse
import json
import signal
import threading
from uuid import uuid5

from packages.domain.runtime import (
    Command,
    DomainError,
    assert_role,
    audit,
    connection,
    enqueue_plan,
    sha,
)
from packages.rag.http_port import DenseHttp, RuntimeUnavailable
from packages.rag.indexing import INDEX_NAMESPACE, original_source_id
from packages.rag.tasks import task_payload

STOP = threading.Event()


def schedule_once() -> int:
    dense = DenseHttp()
    profile_id = uuid5(INDEX_NAMESPACE, json.dumps(dense.identity, sort_keys=True))
    count = 0
    with connection() as c:
        rows = c.execute(
            """SELECT p.id,p.bid_id,p.document_version_id,v.created_by FROM parse_runs p
               JOIN document_versions v ON v.id=p.document_version_id
               WHERE p.coverage<>'failed' AND EXISTS(SELECT 1 FROM evidence_blocks e
                 WHERE e.parse_id=p.id AND e.kind='text' AND length(trim(e.text_content))>0)
               AND p.id=(SELECT newest.id FROM parse_runs newest
                 WHERE newest.document_version_id=p.document_version_id
                 ORDER BY newest.created_at DESC,newest.id DESC LIMIT 1)
               AND NOT EXISTS(SELECT 1 FROM commands cmd
                 WHERE cmd.command_kind='auto-index-parse' AND cmd.target_id=p.bid_id
                 AND cmd.response->'body'->>'parse_id'=p.id::text
                 AND cmd.response->'body'->>'embedding_profile_id'=%s
                 AND cmd.response->'body'->>'lexical_dictionary_digest'=%s
                 AND cmd.response->'body'->>'chunking'='evidence_block_offsets:800:1200:120:v1')
               ORDER BY p.created_at,p.id LIMIT 100""",
            (str(profile_id), dense.lexical_dictionary_digest),
        ).fetchall()
    for row in rows:
        payload = task_payload(row["id"], dense)
        with connection() as c:
            root = original_source_id(row["document_version_id"])
            head = c.execute(
                "SELECT status FROM retrieval.source_heads WHERE source_id=%s", (root,)
            ).fetchone()
            if head and head["status"] == "withdrawn":
                continue
            cmd = Command(
                c, row["created_by"], "auto-index-parse", row["bid_id"], sha(payload), payload
            )
            if cmd.replay:
                continue
            operation = enqueue_plan(
                c,
                row["bid_id"],
                cmd.id,
                "index-parse",
                payload,
                stages=[{"key": "index-parse", "depends_on": [], "required": True}],
                handler_version="rag-index/1",
                topic="tender.task.document.v1",
            )
            cmd.finish(
                {
                    "operation": operation,
                    "parse_id": str(row["id"]),
                    "embedding_profile_id": payload["embedding_profile_id"],
                    "lexical_dictionary_digest": payload["lexical_dictionary_digest"],
                    "chunking": payload["chunking"],
                },
                202,
            )
            audit(
                c,
                None,
                row["bid_id"],
                "rag.index_scheduled",
                {"parse_id": row["id"], "operation_id": operation["id"]},
                cmd.id,
            )
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    assert_role()
    for signal_value in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_value, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            scheduled = schedule_once()
            if arguments.once:
                print({"scheduled": scheduled, "paid_generation_calls": 0})
        except (RuntimeUnavailable, DomainError):
            if arguments.once:
                raise SystemExit("RAG scheduling unavailable; no paid generation calls") from None
        if arguments.once:
            break
        STOP.wait(5)


if __name__ == "__main__":
    main()
