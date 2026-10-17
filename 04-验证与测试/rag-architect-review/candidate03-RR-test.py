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
from packages.rag.indexing import compute_index, prepare_index, publish_index
from tests.test_rag_real_integration import seed_parse

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RAG_REAL") != "1", reason="Explicit real local model/PG opt-in"
)


def test_architect_rr_final_guard_can_publish_withdrawn_source(monkeypatch):
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
            prepared = prepare_index(c, bid, parse, dense, LexicalHttp(dense))
        computed = compute_index(prepared, dense)
        with connection(True) as c:
            indexed=publish_index(c, computed)

        import time,json
        from pathlib import Path
        from packages.rag.http_port import RerankHttp
        ready=threading.Event()
        observed=[]
        failures=[]
        def withdraw():
            try:
                with connection() as c:
                    c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
                    c.execute('SELECT pg_advisory_xact_lock(90127,1)')
                    c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(indexed['original_source_id'],))
                    ready.set()
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        row=c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted AND locktype='advisory' AND classid=90127 AND objid=1) AS waiting").fetchone()
                        if row['waiting']:
                            observed.append('final_waited_for_graph')
                            break
                        time.sleep(.01)
                    else:raise AssertionError('final did not wait for graph')
            except BaseException as error:failures.append(repr(error));ready.set()
        threads=[]
        class RacyRerank(RerankHttp):
            def rank(self,query,texts):
                values=super().rank(query,texts)
                thread=threading.Thread(target=withdraw);threads.append(thread);thread.start()
                assert ready.wait(3)
                return values
        monkeypatch.setattr(rag_routes,'RerankHttp',RacyRerank)
        request={'bid_id':str(bid),'context':'body_evidence','query':'围挡高度要求','limit':4}
        response=client.post('/api/v1/retrievals',json=request,headers=headers())
        for thread in threads:thread.join(6)
        assert not failures,failures
        assert observed==['final_waited_for_graph']
        with connection() as c:
            current=c.execute('SELECT status FROM retrieval.source_heads WHERE source_id=%s',(indexed['original_source_id'],)).fetchone()['status']
            count=c.execute('SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s',(bid,)).fetchone()['n']
        assert current=='withdrawn'
        assert response.status_code==200 and response.json()['hits'] and count==1,response.text
        Path(os.environ['ARCHITECT_RAG_OUT']).joinpath('candidate03-RR-counterexample.json').write_text(json.dumps({'reproduced':True,'default_api_isolation':'repeatable read','final_waited_for_graph':True,'source_current_status':current,'response_status':response.status_code,'hits':len(response.json()['hits']),'published_traces':count,'paid_calls':0},indent=2)+'\n')
