"""Same command replay must reject changed ancestor authority even if evidence stays eligible."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.domain.runtime import connection, secret_hash
from packages.rag.http_port import DenseHttp, LexicalHttp
from packages.rag.indexing import compute_index, prepare_index, publish_index
from tests.test_rag_real_integration import seed_parse

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RAG_REAL") != "1", reason="Explicit real model/PG opt-in"
)


def test_changed_but_eligible_ancestor_rejects_same_key_replay():
    member, session, token = uuid4(), uuid4(), str(uuid4())
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'RAG重放回归')", (member,))
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (session, member, secret_hash(token), hashlib.sha256(b"synthetic-csrf").hexdigest()),
        )
    with TestClient(app) as client:
        client.cookies.set("session", token)
        headers = {
            "Origin": os.environ["APP_ORIGIN"],
            "X-CSRF-Token": "synthetic-csrf",
            "Idempotency-Key": str(uuid4()),
        }
        created = client.post(
            "/api/v1/bids", json={"title": "RAG来源head重放回归"}, headers=headers
        )
        assert created.status_code == 201
        bid, version, parse = seed_parse(bid=created.json()["id"])
        with connection() as c:
            c.execute("UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (version, bid))
        dense = DenseHttp()
        lexical = LexicalHttp(dense)
        with connection(True) as c:
            prepared = prepare_index(c, bid, parse, dense, lexical)
        computed = compute_index(prepared, dense)
        with connection(True) as c:
            indexed = publish_index(c, computed)
        headers["Idempotency-Key"] = str(uuid4())
        body = {"bid_id": str(bid), "context": "body_evidence", "query": "围挡高度要求", "limit": 4}
        first = client.post("/api/v1/retrievals", json=body, headers=headers)
        assert first.status_code == 200 and first.json()["hits"]
        replay = client.post("/api/v1/retrievals", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == first.json()
        with connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(90127,1)")
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,valid_until=now()+interval '30 days' WHERE source_id=%s",
                (indexed["original_source_id"],),
            )
        stale = client.post("/api/v1/retrievals", json=body, headers=headers)
        assert stale.status_code == 409
        assert stale.json()["code"] == "RETRIEVAL_SOURCE_CHANGED"
        with connection() as c:
            count = c.execute(
                "SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s", (bid,)
            ).fetchone()["n"]
            assert count == 1  # Revalidation does not silently rerun the model or replace history.
