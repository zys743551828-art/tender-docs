"""Real model/PG index task boundaries, with immutable source withdrawal during CPU work."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest

from packages.domain.runtime import DomainError, connection, sha
from packages.rag import tasks
from packages.rag.http_port import DenseHttp
from packages.rag.indexing import original_source_id, withdraw_source
from tests.test_rag_real_integration import seed_parse

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RAG_REAL") != "1", reason="Explicit real local model/PG opt-in"
)


@pytest.mark.parametrize("withdraw", [False, True])
def test_index_task_closes_read_connection_before_all_model_work(monkeypatch, withdraw):
    bid, version, parse = seed_parse()
    dense = DenseHttp()
    message = {
        "bid_id": str(bid),
        "payload_ref": str(parse),
        "step_key": "index-parse",
        "input_digest": sha(tasks.task_payload(parse, dense)),
    }
    active = []
    pids = []
    calls = []
    withdrawn: list[bool] = []

    @contextmanager
    def tracked_connection(worker=False):
        with connection(worker) as c:
            active.append(c)
            pids.append(c.info.backend_pid)
            try:
                yield c
            finally:
                active.remove(c)

    class ObservedDense(DenseHttp):
        def _call(self, method, path, body=None):
            assert not active, "Index task retained a database connection over model RPC"
            with psycopg.connect(os.environ["API_DATABASE_DSN"], autocommit=True) as monitor:
                observed = monitor.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE pid=ANY(%s)", (pids,)
                ).fetchone()
                assert observed is not None and observed[0] == 0
            calls.append(path)
            if withdraw and path == "/embed" and not withdrawn:
                # A source may be withdrawn after the read; fenced publication must reject it.
                from packages.rag.indexing import ensure_parse_sources

                with connection(True) as c:
                    ensure_parse_sources(c, bid, parse, publish_readable=True)
                with connection() as c:
                    root = original_source_id(version)
                    revision = c.execute(
                        "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (root,)
                    ).fetchone()["revision"]
                    withdraw_source(c, bid, root, revision)
                withdrawn.append(True)
            return super()._call(method, path, body)

    monkeypatch.setattr(tasks, "connection", tracked_connection)
    monkeypatch.setattr(tasks, "DenseHttp", ObservedDense)
    computed = tasks.compute(message)
    assert pids and {"/identity", "/tokenize", "/lexical", "/embed"} <= set(calls)
    assert computed.prepared.parse_id == parse
    before = len(calls)
    if withdraw:
        with pytest.raises(DomainError) as rejection:
            with connection(True) as c:
                tasks.publish(c, message, computed)
        assert rejection.value.code == "RAG_INDEX_PUBLICATION_REJECTED"
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM retrieval.chunks WHERE parse_id=%s", (parse,)
                ).fetchone()["n"]
                == 0
            )
    else:
        with connection(True) as c:
            published = tasks.publish(c, message, computed)
            assert published["source_id"] and published["embedded_count"] == len(computed.vectors)
    assert len(calls) == before, "Publication invoked a model"

    # Method mismatch must fail before opening any input-read connection.
    pids.clear()
    with pytest.raises(DomainError) as error:
        tasks.compute(message | {"input_digest": sha(str(uuid4()))})
    assert error.value.code == "RAG_TASK_PROFILE_CHANGED" and pids == []
