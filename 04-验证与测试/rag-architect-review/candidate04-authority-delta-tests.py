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


@pytest.mark.parametrize("mode",["first","final","direct_replay","autocommit"])
def test_architect_fresh_authority_rejects_rr_and_autocommit(monkeypatch,mode):
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
            publish_index(c, computed)

        from packages.domain.runtime import DomainError
        calls=[]
        class ObservedDense(DenseHttp):
            def _call(self,*args,**kw):
                calls.append(args[1])
                return super()._call(*args,**kw)
        monkeypatch.setattr(rag_routes,'DenseHttp',ObservedDense)
        request={'bid_id':str(bid),'context':'body_evidence','query':'围挡高度要求','limit':4}
        key=str(uuid4())
        first=client.post('/api/v1/retrievals',json=request,headers=headers(key))
        assert first.status_code==200 and first.json()['hits'],first.text
        previous=len(calls)
        if mode in ('direct_replay','autocommit'):
            if mode=='direct_replay':
                with pytest.raises(DomainError) as err,connection() as c:
                    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    rag_routes.replay(c,request,bid,{})
            else:
                with psycopg.connect(os.environ['API_DATABASE_DSN'],autocommit=True) as c:
                    with pytest.raises(DomainError) as err:rag_routes.assert_fresh_authority(c)
            assert err.value.code=='RAG_FRESH_AUTHORITY_REQUIRED'
            assert len(calls)==previous
        else:
            original_connection=rag_routes.connection
            count=[0]
            @contextmanager
            def wrong_isolation():
                count[0]+=1
                with original_connection() as c:
                    if count[0]==(1 if mode=='first' else 4):
                        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    yield c
            monkeypatch.setattr(rag_routes,'connection',wrong_isolation)
            rejected=client.post('/api/v1/retrievals',json=request,headers=headers())
            assert rejected.status_code==409 and rejected.json()['code']=='RAG_FRESH_AUTHORITY_REQUIRED',rejected.text
            assert count[0]==(1 if mode=='first' else 4)
            if mode=='first':
                assert len(calls)==previous
                count[0]=0
                old=client.post('/api/v1/retrievals',json=request,headers=headers(key))
                assert old.status_code==409 and len(calls)==previous
            else:assert len(calls)>previous
        with connection() as c:
            assert c.execute('SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s',(bid,)).fetchone()['n']==1
            assert c.execute("SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='retrieve-evidence'",(bid,)).fetchone()['n']==1
