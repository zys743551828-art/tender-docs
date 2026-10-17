"""Real BGE/PG staged API: no open request DB connection during model HTTP, final CAS."""

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api import rag_routes
from apps.api.main import app
from packages.domain.runtime import connection, secret_hash
from packages.domain.source_effectivity import EVENT
from packages.rag.http_port import DenseHttp, LexicalHttp
from packages.rag.indexing import compute_index, prepare_index, publish_index, read_index_input
from tests.test_rag_real_integration import seed_parse

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RAG_REAL") != "1", reason="Explicit real local model/PG opt-in"
)


def test_staged_real_models_close_connections_and_recheck_heads_effectivity(monkeypatch):
    actor, session, token = uuid4(), uuid4(), str(uuid4())
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'分阶段RAG合成测试')", (actor,))
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (session, actor, secret_hash(token), hashlib.sha256(b"synthetic-csrf").hexdigest()),
        )
    with TestClient(app) as client:
        client.cookies.set("session", token)
        base = {"Origin": os.environ["APP_ORIGIN"], "X-CSRF-Token": "synthetic-csrf"}

        def headers(key=None):
            return base | {"Idempotency-Key": key or str(uuid4())}

        created = client.post(
            "/api/v1/bids", json={"title": "分阶段RAG合成SQL证据夹具"}, headers=headers()
        )
        assert created.status_code == 201
        bid, version, parse = seed_parse(bid=created.json()["id"])
        with connection() as c:
            c.execute("UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (version, bid))
            evidence = c.execute(
                "SELECT id FROM evidence_blocks WHERE parse_id=%s", (parse,)
            ).fetchone()["id"]
        dense = DenseHttp()
        with connection(True) as c:
            raw = read_index_input(c, bid, parse)
        prepared = prepare_index(raw, dense, LexicalHttp(dense))
        computed = compute_index(prepared, dense)
        with connection(True) as c:
            publish_index(c, computed)
        local = threading.local()
        rpc = []
        change = {"path": None, "kind": None}
        original_connection = rag_routes.connection

        @contextmanager
        def tracked_connection():
            with original_connection() as current:
                local.active = True
                local.previous = getattr(local, "previous", []) + [current.info.backend_pid]
                try:
                    yield current
                finally:
                    local.active = False

        class ObservedDense(DenseHttp):
            def _call(self, method, path, body=None):
                assert not getattr(local, "active", False), (
                    "Model RPC retained a request DB connection"
                )
                with psycopg.connect(os.environ["API_DATABASE_DSN"], autocommit=True) as monitor:
                    assert (
                        monitor.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE pid=ANY(%s)",
                            (getattr(local, "previous", []),),
                        ).fetchone()[0]
                        == 0
                    )
                rpc.append(path)
                if change["path"] == path:
                    change["path"] = None
                    with connection() as c:
                        if change["kind"] == "head":
                            c.execute(
                                "UPDATE bids SET material_revision=material_revision+1 WHERE id=%s",
                                (bid,),
                            )
                        else:
                            # Explicit SQL mechanism event, not a user/business effectivity acceptance.
                            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
                            c.execute(
                                "INSERT INTO audit_entries(id,actor_id,bid_id,event_type,payload) VALUES(%s,%s,%s,%s,%s)",
                                (
                                    uuid4(),
                                    actor,
                                    bid,
                                    EVENT,
                                    Jsonb(
                                        {
                                            "ended_rules": [
                                                {
                                                    "rule_id": str(uuid4()),
                                                    "semantic_key": "synthetic",
                                                    "evidence_ids": [str(evidence)],
                                                }
                                            ],
                                            "action": "partial_replace",
                                            "old_document_version_id": str(version),
                                            "new_document_version_id": str(version),
                                            "reason": "合成SQL效力反例",
                                        }
                                    ),
                                ),
                            )
                return super()._call(method, path, body)

        monkeypatch.setattr(rag_routes, "connection", tracked_connection)
        monkeypatch.setattr(rag_routes, "DenseHttp", ObservedDense)
        request = {
            "bid_id": str(bid),
            "context": "body_evidence",
            "query": "围挡高度要求",
            "limit": 4,
        }
        key = str(uuid4())
        first = client.post("/api/v1/retrievals", json=request, headers=headers(key))
        assert first.status_code == 200, first.json()
        assert first.json()["hits"]
        assert {"/identity", "/embed", "/lexical", "/rerank"} <= set(rpc)
        previous_calls = len(rpc)
        same = client.post("/api/v1/retrievals", json=request, headers=headers(key))
        assert same.status_code == 200 and same.json() == first.json()
        assert len(rpc) == previous_calls

        @contextmanager
        def repeatable_connection():
            with original_connection() as current:
                current.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                yield current

        with monkeypatch.context() as patch:
            patch.setattr(rag_routes, "connection", repeatable_connection)
            for selected_key in (str(uuid4()), key):
                denied = client.post(
                    "/api/v1/retrievals", json=request, headers=headers(selected_key)
                )
                assert (
                    denied.status_code == 409
                    and denied.json()["code"] == "RAG_FRESH_AUTHORITY_REQUIRED"
                )
            assert len(rpc) == previous_calls
            with repeatable_connection() as current:
                with pytest.raises(rag_routes.DomainError) as error:
                    rag_routes.replay(current, request, bid, {"body": first.json(), "status": 200})
                assert error.value.code == "RAG_FRESH_AUTHORITY_REQUIRED"

        connection_count = [0]

        @contextmanager
        def changed_final_connection():
            with original_connection() as current:
                connection_count[0] += 1
                if connection_count[0] == 4:
                    current.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                yield current

        with monkeypatch.context() as patch:
            patch.setattr(rag_routes, "connection", changed_final_connection)
            denied = client.post("/api/v1/retrievals", json=request, headers=headers())
            assert connection_count[0] == 4
            assert (
                denied.status_code == 409
                and denied.json()["code"] == "RAG_FRESH_AUTHORITY_REQUIRED"
            )
        concurrent_key = str(uuid4())

        def same_key(_):
            return client.post("/api/v1/retrievals", json=request, headers=headers(concurrent_key))

        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent = list(pool.map(same_key, range(2)))
        assert [response.status_code for response in concurrent] == [200, 200]
        assert concurrent[0].json() == concurrent[1].json()
        change.update(path="/embed", kind="head")
        changed = client.post("/api/v1/retrievals", json=request, headers=headers())
        assert changed.status_code == 409 and changed.json()["code"] == "RETRIEVAL_SOURCE_CHANGED"
        assert (
            client.post("/api/v1/retrievals", json=request, headers=headers(key)).status_code == 409
        )
        change.update(path="/rerank", kind="effect")
        affected = client.post("/api/v1/retrievals", json=request, headers=headers())
        assert affected.status_code == 409
        current = client.post("/api/v1/retrievals", json=request, headers=headers())
        assert current.status_code == 200 and current.json()["hits"] == []
        assert any("候选阶段排除" in gap for gap in current.json()["gaps"])
        historical = client.post(
            "/api/v1/retrievals", json=request | {"context": "history"}, headers=headers()
        )
        assert historical.status_code == 200 and historical.json()["hits"]
        assert any("历史原文" in gap for gap in historical.json()["gaps"])
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s", (bid,)
                ).fetchone()["n"]
                == 4
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='retrieve-evidence'",
                    (bid,),
                ).fetchone()["n"]
                == 4
            )
        before = len(rpc)
        blank = client.post(
            "/api/v1/retrievals", json=request | {"query": "   "}, headers=headers()
        )
        assert blank.status_code == 422 and len(rpc) == before
