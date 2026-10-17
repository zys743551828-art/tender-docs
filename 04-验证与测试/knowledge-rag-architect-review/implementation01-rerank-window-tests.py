"""New six-table real nonowner PG checks; fake vectors explicitly mechanism-only."""

import os
from uuid import UUID, uuid4

import pytest
import test_knowledge_integration as base_knowledge

from packages.domain.runtime import Command, assert_role, connection, sha
from packages.knowledge.rag_index import compute_index
from packages.knowledge.rag_repository import prepare_index, publish_index, schedule_index

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_RAG_INTEGRATION") != "1", reason="需要知识RAG独立新库"
)
publish = base_knowledge.publish


@pytest.fixture
def environment(monkeypatch):
    from packages.domain import storage

    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    return base_knowledge.environment.__wrapped__()


class ExplicitMechanismDense:
    identity = {
        "model_name": "EXPLICIT_KNOWLEDGE_MECHANISM_FIXTURE",
        "model_revision": "no-quality-claim",
        "weights_digest": "1" * 64,
        "preprocessing_digest": "2" * 64,
    }
    lexical_dictionary_digest = "3" * 64

    def offsets(self, text):
        return [(i, i + 1) for i in range(len(text))]

    def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def lexemes(self, text):
        return " ".join(text)


def scheduled(env, dense=None):
    dense = dense or ExplicitMechanismDense()
    with connection() as c:
        command = Command(c, env["actor"], "test-knowledge-index", env["item"], uuid4().hex, {})
        result = schedule_index(c, env["item"], env["actor"], command.id, dense)
        command.finish(result, 202)
    return UUID(result["index_id"])


def computed(index, dense=None):
    dense = dense or ExplicitMechanismDense()
    with connection(True) as c:
        prepared = prepare_index(c, index)
    return compute_index(
        prepared, dense, dense, dense, lexical_dictionary_digest=dense.lexical_dictionary_digest
    )


def indexed(env, dense=None):
    index = scheduled(env, dense)
    outcome = computed(index, dense)
    with connection(True) as c:
        result = publish_index(c, outcome)
    assert result["state"] == "ready"
    return index, outcome


def test_six_table_index_true_worker_permissions_and_float32_readback(environment):
    env = environment
    publish(env)
    assert_role(True)
    index, outcome = indexed(env)
    with connection(True) as c:
        row = c.execute(
            "SELECT * FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
        ).fetchone()
        assert row["expected_unit_count"] == row["published_unit_count"] == len(outcome.units) == 2
        assert row["unit_manifest_digest"] == outcome.manifest_digest
        assert publish_index(c, outcome)["state"] == "ready"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_units WHERE index_id=%s", (index,)
            ).fetchone()["n"]
            == 2
        )


def frozen(env, index):
    from packages.knowledge.rag_search import freeze_scope

    with connection() as c:
        row = c.execute(
            "SELECT profile_id FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
        ).fetchone()
        return freeze_scope(
            c,
            env["target"],
            "authoring",
            row["profile_id"],
            ExplicitMechanismDense.identity,
            ExplicitMechanismDense.lexical_dictionary_digest,
            [str(env["item"])],
        )


def traced(env, index, limit=8):
    from packages.knowledge.rag_search import read_candidates, save_trace

    scope = frozen(env, index)
    with connection() as c:
        candidates = read_candidates(c, scope, [1.0] + [0.0] * 1023, " ".join("原件参考"))
    with connection() as c:
        result = save_trace(
            c,
            scope,
            env["actor"],
            "原件参考",
            candidates,
            ["explicit_mechanism_fixture"],
            None,
            limit,
        )
    return scope, result


def cite_body(hit):
    return {
        "unit_id": hit["unit_id"],
        "quote": hit["text"],
        "target_kind": "project",
        "target_object_id": "",
        "applicability_reason": "合成机制逐项核对，仅作参考不自动成事实",
    }


def test_real_sql_hybrid_trace_reference_exact_original_and_unsupplied_unit_denial(environment):
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_search import cite_unit, reference_history, trace_history

    env = environment
    publish(env)
    index, _ = indexed(env)
    scope, result = traced(env, index, limit=1)
    assert len(result["hits"]) == 1 and len(scope["units"]) == 2
    body = cite_body(result["hits"][0]) | {"target_object_id": str(env["target"])}
    with connection() as c:
        reference = cite_unit(c, UUID(result["trace_id"]), env["actor"], body, None)
        assert not reference["project_fact_created"]
        assert reference_history(c, UUID(reference["citation_id"]))["reference"] == reference
        assert trace_history(c, UUID(result["trace_id"]))["retrieval"] == result
        other = next(u for u in scope["units"] if u["unit_id"] != body["unit_id"])
        with pytest.raises(DomainError) as exc:
            cite_unit(
                c,
                UUID(result["trace_id"]),
                env["actor"],
                body | {"unit_id": other["unit_id"], "quote": other["text"]},
                None,
            )
        assert exc.value.code == "KNOWLEDGE_SUPPLY_INVALID"
        row = c.execute(
            "SELECT ci.*,u.knowledge_version_id FROM retrieval.citations ci JOIN retrieval.knowledge_units u ON u.source_id=ci.source_id WHERE ci.id=%s",
            (UUID(reference["citation_id"]),),
        ).fetchone()
        assert row["bid_id"] == env["target"] and row["chunk_id"] is None
        assert str(row["knowledge_version_id"]) == reference["knowledge_version_id"]
        assert row["evidence_id"] == UUID(env["body"]["source_refs"][0]["evidence_id"])


@pytest.mark.parametrize(
    "change", ["reindex", "candidate_unit", "withdrawn_unit", "expired_unit", "withdrawn_parent"]
)
def test_all_trace_new_use_paths_reject_changed_index_or_unit_authority(environment, change):
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_search import assert_trace_scope_current, cite_unit, trace_history

    env = environment
    active = publish(env)
    index, _ = indexed(env)
    scope, result = traced(env, index)
    if change == "reindex":
        second, _ = indexed(env)
        assert second != index
    elif change == "withdrawn_parent":
        from test_knowledge_integration import action

        with connection() as c:
            action(c, env, "withdraw", active["revision"])
    else:
        with connection(True) as c:
            source = UUID(scope["units"][0]["unit_source_id"])
            if change == "expired_unit":
                c.execute(
                    "UPDATE retrieval.source_heads SET revision=revision+1,valid_from=clock_timestamp()-interval '2 day',valid_until=clock_timestamp()-interval '1 day' WHERE source_id=%s",
                    (source,),
                )
            else:
                c.execute(
                    "UPDATE retrieval.source_heads SET revision=revision+1,status=%s WHERE source_id=%s",
                    ("candidate" if change == "candidate_unit" else "withdrawn", source),
                )
    with connection() as c:
        with pytest.raises(DomainError):
            assert_trace_scope_current(c, scope, env["target"], "authoring")
        with pytest.raises(DomainError):
            cite_unit(
                c,
                UUID(result["trace_id"]),
                env["actor"],
                cite_body(result["hits"][0]) | {"target_object_id": str(env["target"])},
                None,
            )
        history = trace_history(c, UUID(result["trace_id"]))
        assert history["current_reasons"] and history["retrieval"] == result
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (env["target"],)
            ).fetchone()["n"]
            == 0
        )


def test_new_table_column_capabilities_are_real_and_not_owner_workarounds(environment):
    import psycopg

    for worker, statements in [
        (
            False,
            [
                "UPDATE retrieval.knowledge_indexes SET state='ready' WHERE false",
                "UPDATE retrieval.knowledge_index_heads SET ready_index_id=NULL WHERE false",
                "UPDATE retrieval.knowledge_units SET text_content='forged' WHERE false",
            ],
        ),
        (
            True,
            [
                "UPDATE retrieval.knowledge_indexes SET input_manifest='{}' WHERE false",
                "UPDATE retrieval.knowledge_index_heads SET revision=revision+1 WHERE false",
                "SELECT * FROM engineering_projects LIMIT 0",
                "SELECT * FROM bidders LIMIT 0",
                "SELECT * FROM commands LIMIT 0",
                "SELECT * FROM retrieval.knowledge_retrieval_traces LIMIT 0",
            ],
        ),
    ]:
        with connection(worker) as c:
            for statement in statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                    c.execute(statement)


class ExplicitMechanismReranker:
    last_identity = {
        "model_name": "EXPLICIT_TEST_RERANKER",
        "model_revision": "mechanism-only",
        "weights_digest": "4" * 64,
        "preprocessing_digest": "5" * 64,
    }

    def rank(self, query, texts):
        return [float(len(text)) for text in texts]


@pytest.fixture
def mechanism_http(monkeypatch):
    from packages.knowledge import rag_api, rag_tasks
    from packages.rag import http_port

    monkeypatch.setattr(http_port, "DenseHttp", ExplicitMechanismDense)
    monkeypatch.setattr(rag_api, "DenseHttp", ExplicitMechanismDense)
    monkeypatch.setattr(rag_api, "LexicalHttp", lambda dense: dense)
    monkeypatch.setattr(rag_api, "RerankHttp", lambda dense: ExplicitMechanismReranker())
    monkeypatch.setattr(rag_tasks, "DenseHttp", ExplicitMechanismDense)
    monkeypatch.setattr(rag_tasks, "LexicalHttp", lambda dense: dense)


def login_client(env):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.knowledge_routes import router
    from apps.api.main import app
    from packages.domain.runtime import required

    login, password = "knowledge-rag-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
    if not any(
        getattr(r, "path", "") == "/api/v1/bids/{bid_id}/knowledge-retrievals" for r in app.routes
    ):
        app.include_router(router)
    client = TestClient(app)
    origin = required("APP_ORIGIN")
    signed = client.post(
        "/api/v1/auth/login",
        json={"login_name": login, "password": password},
        headers={"Origin": origin},
    )
    assert signed.status_code == 200
    headers = {
        "Origin": origin,
        "X-CSRF-Token": signed.json()["csrf_token"],
        "Idempotency-Key": uuid4().hex,
    }
    return client, headers


def test_authenticated_http_actual_contract_replay_and_reindex_invalidate_old_trace_and_reference(
    environment, mechanism_http
):
    from packages.knowledge.rag_contracts import validate_actual

    env = environment
    active = publish(env)
    index, _ = indexed(env)
    client, headers = login_client(env)
    with client:
        index_path = f"/api/v1/knowledge/{env['item']}/indexes"
        states = client.get(index_path)
        assert states.status_code == 200
        validate_actual("KnowledgeIndexPage", states.json())
        path = f"/api/v1/bids/{env['target']}/knowledge-retrievals"
        body = {
            "query": "核实原件技术措施和工期",
            "purpose": "authoring",
            "limit": 2,
            "knowledge_ids": [str(env["item"])],
        }
        assert client.post(path, json=body).status_code == 403
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200, first.json()
        validate_actual("KnowledgeRetrievalResult", first.json())
        assert set(first.json()["route"]) == {"dense_exact", "simple_fts", "rrf", "rerank"}
        replay = client.post(path, json=body, headers=headers)
        assert (
            replay.status_code == 200
            and replay.headers["Idempotent-Replayed"] == "true"
            and replay.json() == first.json()
        )
        hit = next(h for h in first.json()["hits"] if h["unit_kind"] == "original_quote")
        assert hit["text"] in env["body"]["source_refs"][0]["excerpt"]
        ref_path = f"/api/v1/knowledge-retrievals/{first.json()['trace_id']}/references"
        ref_headers = headers | {"Idempotency-Key": uuid4().hex}
        reference = client.post(
            ref_path,
            json=cite_body(hit) | {"target_object_id": str(env["target"])},
            headers=ref_headers,
        )
        assert reference.status_code == 201, reference.json()
        validate_actual("KnowledgeUnitReference", reference.json())
        repeated_reference = client.post(
            ref_path,
            json=cite_body(hit) | {"target_object_id": str(env["target"])},
            headers=ref_headers,
        )
        assert (
            repeated_reference.status_code == 201 and repeated_reference.json() == reference.json()
        )
        bad = client.post(
            path,
            json=body | {"purpose": "formal_score"},
            headers=headers | {"Idempotency-Key": uuid4().hex},
        )
        assert bad.status_code == 422
        assert (
            client.post(
                path,
                json=body | {"allowed_source_ids": []},
                headers=headers | {"Idempotency-Key": uuid4().hex},
            ).status_code
            == 422
        )
        second = client.post(
            index_path,
            json={"knowledge_version_id": active["version_id"]},
            headers=headers | {"Idempotency-Key": uuid4().hex},
        )
        assert second.status_code == 202, second.json()
        validate_actual("KnowledgeIndexAccepted", second.json())
        outcome = computed(UUID(second.json()["index_id"]))
        with connection(True) as c:
            assert publish_index(c, outcome)["state"] == "ready"
        assert UUID(second.json()["index_id"]) != index
        assert client.post(path, json=body, headers=headers).status_code == 409
        assert (
            client.post(
                ref_path,
                json=cite_body(hit) | {"target_object_id": str(env["target"])},
                headers=ref_headers,
            ).status_code
            == 409
        )
        fresh = client.post(path, json=body, headers=headers | {"Idempotency-Key": uuid4().hex})
        assert fresh.status_code == 200 and fresh.json()["trace_id"] != first.json()["trace_id"]
        history = client.get(f"/api/v1/knowledge-unit-references/{reference.json()['citation_id']}")
        assert history.status_code == 200
        validate_actual("KnowledgeUnitReferenceHistory", history.json())
        assert history.json()["reference"] == reference.json() and history.json()["current_reasons"]
        trace_history = client.get(f"/api/v1/knowledge-retrievals/{first.json()['trace_id']}")
        validate_actual("KnowledgeRetrievalHistory", trace_history.json())
        assert (
            trace_history.json()["retrieval"] == first.json()
            and trace_history.json()["current_reasons"]
        )


def test_source_withdrawal_during_model_compute_leaves_no_stuck_command_or_trace(
    environment, mechanism_http, monkeypatch
):
    from packages.knowledge import rag_api

    env = environment
    publish(env)
    indexed(env)

    class ChangedDuringModel(ExplicitMechanismDense):
        def embed(self, texts):
            # Explicit external-source event fixture; would block if search retained its graph lock.
            with connection() as other:
                other.execute("SET LOCAL lock_timeout='2s'")
                other.execute("SELECT pg_advisory_xact_lock(90127,1)")
                other.execute(
                    "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
                    (env["root"],),
                )
            return super().embed(texts)

    monkeypatch.setattr(rag_api, "DenseHttp", ChangedDuringModel)
    client, headers = login_client(env)
    with client:
        result = client.post(
            f"/api/v1/bids/{env['target']}/knowledge-retrievals",
            json={"query": "原件工法", "purpose": "authoring", "limit": 2},
            headers=headers,
        )
        assert result.status_code == 409, result.json()
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_retrieval_traces WHERE bid_id=%s",
                (env["target"],),
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM commands WHERE command_kind='knowledge-retrieve' AND target_id=%s",
                (env["target"],),
            ).fetchone()["n"]
            == 0
        )


def test_late_index_generation_and_different_output_replay_cannot_publish_or_rewrite_units(
    environment,
):
    from dataclasses import replace

    env = environment
    publish(env)
    first = scheduled(env)
    first_result = computed(first)
    second = scheduled(env)
    second_result = computed(second)
    with connection(True) as c:
        assert publish_index(c, second_result)["state"] == "ready"
        assert publish_index(c, first_result)["state"] == "superseded"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_units WHERE index_id=%s", (first,)
            ).fetchone()["n"]
            == 0
        )
        bad = replace(
            second_result,
            units=(
                replace(second_result.units[0], text="未批准的原段尾句"),
                *second_result.units[1:],
            ),
        )
        with pytest.raises(ValueError):
            publish_index(c, bad)
        assert (
            c.execute(
                "SELECT published_unit_count FROM retrieval.knowledge_indexes WHERE id=%s",
                (second,),
            ).fetchone()["published_unit_count"]
            == 2
        )


def test_changed_fact_basis_then_restored_same_value_cannot_reuse_old_trace(environment):
    from types import SimpleNamespace

    from packages.domain.fact_sets import current_facts, replace_set
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_search import assert_trace_scope_current, cite_unit

    env = base_knowledge.conditional.__wrapped__(environment, SimpleNamespace())
    fact = base_knowledge.add_confirmed_fact(env)
    index, _ = indexed(env)
    scope, result = traced(env, index)
    for value in ["4", "3"]:
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (env["target"],))
            _, fact = replace_set(
                c,
                env["target"],
                env["actor"],
                "合成事实更正并重新核实",
                kind="replace",
                removed_id=fact,
                new_fact=base_knowledge.fact_input(env, value=value),
            )
    # The current value and current applicability are restored, but confirmation identity is different.
    assert frozen(env, index)["units"]
    with connection() as c:
        with pytest.raises(DomainError):
            assert_trace_scope_current(c, scope, env["target"], "authoring")
        with pytest.raises(DomainError):
            cite_unit(
                c,
                UUID(result["trace_id"]),
                env["actor"],
                cite_body(result["hits"][0]) | {"target_object_id": str(env["target"])},
                None,
            )
        _, facts = current_facts(c, env["target"])
        assert facts[0]["value"] == "3" and facts[0]["id"] == fact


@pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_RAG_MODELS") != "1", reason="需要实际本地BGE/重排运行时"
)
def test_actual_bge_engine_http_and_reranker_have_no_prepare_or_search_connection(
    environment, monkeypatch
):
    import json
    from contextlib import contextmanager
    from pathlib import Path

    from apps.worker.engine import acquire, finish, run_compute
    from packages.knowledge import rag_api, rag_tasks
    from packages.knowledge.rag_contracts import validate_actual
    from packages.knowledge.rag_repository import index_row
    from packages.rag import http_port

    env = environment
    publish(env)
    opened = {"count": 0}
    observations = []
    original_connection = connection
    real_dense = http_port.DenseHttp

    @contextmanager
    def tracked_connection(worker=False):
        with original_connection(worker) as c:
            opened["count"] += 1
            try:
                yield c
            finally:
                opened["count"] -= 1

    class RealObservedDense(real_dense):
        def _call(self, method, path, body=None):
            assert opened["count"] == 0, "Model I/O crossed a prepare/search database transaction"
            texts = []
            if body:
                texts += body.get("texts", [])
                texts += [body[k] for k in ["text", "query"] if k in body]
            allowed = [
                env["body"]["content"],
                env["body"]["source_refs"][0]["excerpt"],
                "具体工期和数量如何核实",
            ]
            assert all(any(text in envelope for envelope in allowed) for text in texts)
            response = super()._call(method, path, body)
            observations.append(
                {
                    "method": method,
                    "path": path,
                    "held_prepare_or_search_connections": opened["count"],
                    "input_digests": [sha(t.encode()) for t in texts],
                    "identity": response.get("identity"),
                }
            )
            return response

    monkeypatch.setattr(rag_api, "connection", tracked_connection)
    monkeypatch.setattr(rag_tasks, "connection", tracked_connection)
    monkeypatch.setattr(rag_api, "DenseHttp", RealObservedDense)
    monkeypatch.setattr(rag_tasks, "DenseHttp", RealObservedDense)
    monkeypatch.setattr(http_port, "DenseHttp", RealObservedDense)
    dense = RealObservedDense()
    index = scheduled(env, dense)
    with connection() as c:
        row = index_row(c, index)
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1'",
            (row["operation_id"],),
        ).fetchone()["payload"]
    lease = acquire(message)
    assert lease
    outcome = run_compute(message, lease)
    assert all(len(v) == 1024 for v in outcome.vectors)
    assert finish(message, lease, outcome)
    assert acquire(message) is None
    client, headers = login_client(env)
    with client:
        body = {
            "query": "具体工期和数量如何核实",
            "purpose": "authoring",
            "limit": 2,
            "knowledge_ids": [str(env["item"])],
        }
        first = client.post(
            f"/api/v1/bids/{env['target']}/knowledge-retrievals", json=body, headers=headers
        )
        assert first.status_code == 200, first.json()
        result = first.json()
        validate_actual("KnowledgeRetrievalResult", result)
        assert "rerank" in result["route"] and len(result["hits"]) == 2
        quote = next(h for h in result["hits"] if h["unit_kind"] == "original_quote")
        assert quote["text"] in env["body"]["source_refs"][0]["excerpt"]
        cited = client.post(
            f"/api/v1/knowledge-retrievals/{result['trace_id']}/references",
            json=cite_body(quote) | {"target_object_id": str(env["target"])},
            headers=headers | {"Idempotency-Key": uuid4().hex},
        )
        assert cited.status_code == 201, cited.json()
        validate_actual("KnowledgeUnitReference", cited.json())
        count = len(observations)
        repeated = client.post(
            f"/api/v1/bids/{env['target']}/knowledge-retrievals", json=body, headers=headers
        )
        assert (
            repeated.status_code == 200 and repeated.json() == result and len(observations) == count
        )
    with connection() as c:
        trace = c.execute(
            "SELECT * FROM retrieval.knowledge_retrieval_traces WHERE id=%s",
            (UUID(result["trace_id"]),),
        ).fetchone()
        assert trace["observed_reranker_identity"] and trace["profile_id"] == UUID(
            result["profile_id"]
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (row["operation_id"],),
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=ANY(%s)",
                ([env["origin"], env["target"]],),
            ).fetchone()["n"]
            == 0
        )
    target = os.environ.get("KNOWLEDGE_RAG_EVIDENCE_DIR")
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "actual-local-model-result.json").write_text(
            json.dumps(
                {
                    "scope": "synthetic domain text actual BGE dense/reranker engine+HTTP; not company quality or Kafka transport",
                    "dense_identity": dense.identity,
                    "profile_id": result["profile_id"],
                    "reranker_identity": trace["observed_reranker_identity"],
                    "vector_dimensions": 1024,
                    "index_id": str(index),
                    "operation_id": str(row["operation_id"]),
                    "trace_id": result["trace_id"],
                    "route": result["route"],
                    "no_original_paragraph_expansion": True,
                    "observed_calls": observations,
                    "paid_model_calls": 0,
                    "object_port": "explicit in-memory; actual synthetic PDF bytes/PG evidence",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


@pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_RAG_KAFKA") != "1", reason="仅PM授权专用topic/group后运行"
)
def test_isolated_kafka_own_outbox_duplicate_delivery_uses_real_engine_fence(environment):
    import json
    import time
    from pathlib import Path

    from confluent_kafka import Consumer, Producer
    from confluent_kafka.admin import AdminClient, NewTopic

    from apps.worker.engine import acquire, finish, run_compute
    from packages.domain.runtime import required
    from packages.knowledge.rag_repository import index_row
    from packages.rag.http_port import DenseHttp

    env = environment
    publish(env)
    dense = DenseHttp()  # Actual existing private BGE runtime, no paid model.
    index = scheduled(env, dense)
    with connection() as c:
        row = index_row(c, index)
        outbox = c.execute(
            "SELECT id,topic,payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1'",
            (row["operation_id"],),
        ).fetchone()
    message = outbox["payload"]
    suffix = uuid4().hex
    topic = "tender.checks.knowledge-rag." + suffix
    group = "knowledge-rag-checks-" + suffix
    bootstrap = required("KAFKA_BOOTSTRAP_SERVERS")
    admin = AdminClient({"bootstrap.servers": bootstrap})
    admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1)],
        operation_timeout=10,
        request_timeout=15,
    )[topic].result(timeout=20)
    consumer = None
    offsets = []
    deliveries = []
    record = {
        "scope": "isolated Kafka transport of exact own committed Outbox protocol + actual BGE Worker engine; not formal global relay deployment",
        "topic": topic,
        "group_id": group,
        "logical_topic": outbox["topic"],
        "source_outbox_id": str(outbox["id"]),
        "event_id": message["event_id"],
        "operation_id": message["operation_id"],
        "payload_digest": sha(message),
        "paid_model_calls": 0,
    }
    try:
        producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})

        def delivered(error, msg):
            assert error is None
            deliveries.append({"partition": msg.partition(), "offset": msg.offset()})

        for _ in range(2):
            producer.produce(
                topic,
                key=message["operation_id"],
                value=json.dumps(message).encode(),
                on_delivery=delivered,
            )
        assert producer.flush(15) == 0 and len(deliveries) == 2
        consumer = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": group,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "allow.auto.create.topics": False,
            }
        )
        consumer.subscribe([topic])
        deadline = time.monotonic() + 30
        executed, duplicates = 0, 0
        while len(offsets) < 2 and time.monotonic() < deadline:
            incoming = consumer.poll(1)
            if incoming is None:
                continue
            assert not incoming.error() and incoming.topic() == topic
            payload = json.loads(incoming.value())
            # Never invoke an engine action for a message outside this exact test scope.
            assert payload == message and payload["operation_id"] == str(row["operation_id"])
            lease = acquire(payload)
            if lease is None:
                duplicates += 1
            else:
                outcome = run_compute(payload, lease)
                assert finish(payload, lease, outcome)
                executed += 1
            consumer.commit(message=incoming, asynchronous=False)
            offsets.append({"partition": incoming.partition(), "offset": incoming.offset()})
        assert len(offsets) == 2 and executed == 1 and duplicates == 1
        with connection() as c:
            effect = c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (row["operation_id"],),
            ).fetchone()["n"]
            assert effect == 1
            assert (
                c.execute(
                    "SELECT state FROM operations WHERE id=%s", (row["operation_id"],)
                ).fetchone()["state"]
                == "succeeded"
            )
            assert index_row(c, index)["state"] == "ready"
        record.update(
            delivery_offsets=deliveries,
            consumed_offsets=offsets,
            executions=executed,
            duplicate_deliveries=duplicates,
            effect_receipts=effect,
            dense_identity=dense.identity,
        )
    finally:
        if consumer is not None:
            consumer.close()
        assert topic.startswith("tender.checks.knowledge-rag.") and suffix in topic
        admin.delete_topics([topic], operation_timeout=10, request_timeout=15)[topic].result(
            timeout=20
        )
        record["only_own_topic_deleted"] = True
        target = os.environ.get("KNOWLEDGE_RAG_EVIDENCE_DIR")
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "isolated-kafka-result.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n"
            )


def test_actual_worker_stale_fence_and_task_scope_cannot_publish(environment, mechanism_http):
    from apps.worker.engine import acquire, finish, run_compute
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_tasks import compute as task_compute

    env = environment
    publish(env)
    index = scheduled(env)
    with connection() as c:
        operation = c.execute(
            "SELECT operation_id FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
        ).fetchone()["operation_id"]
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1'",
            (operation,),
        ).fetchone()["payload"]
    old = acquire(message)
    assert old
    outcome = run_compute(message, old)
    with connection(True) as c:
        c.execute(
            "UPDATE step_receipts SET lease_until=clock_timestamp()-interval '1 second' WHERE operation_id=%s AND step_key='index-knowledge'",
            (operation,),
        )
    new = acquire(message)
    assert new and new["fence"] > old["fence"]
    assert not finish(message, old, outcome)
    with connection() as c:
        assert (
            c.execute(
                "SELECT state FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
            ).fetchone()["state"]
            == "queued"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_units WHERE index_id=%s", (index,)
            ).fetchone()["n"]
            == 0
        )
    assert finish(message, new, outcome)
    with pytest.raises(DomainError):
        task_compute(message | {"bid_id": str(env["foreign"])})
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s", (operation,)
            ).fetchone()["n"]
            == 1
        )


def test_failed_partial_publication_rolls_back_and_new_index_generation_recovers(
    environment, mechanism_http
):
    from dataclasses import replace

    from apps.worker.engine import acquire, finish, run_compute
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_repository import index_history

    env = environment
    publish(env)
    index = scheduled(env)
    with connection() as c:
        row = c.execute(
            "SELECT operation_id FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
        ).fetchone()
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1'",
            (row["operation_id"],),
        ).fetchone()["payload"]
    lease = acquire(message)
    assert lease
    outcome = run_compute(message, lease)
    partial = replace(outcome, vectors=outcome.vectors[:1])
    with pytest.raises(DomainError) as error:
        finish(message, lease, partial)
    assert finish(message, lease, None, error=error.value)
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_units WHERE index_id=%s", (index,)
            ).fetchone()["n"]
            == 0
        )
        state = index_history(c, env["item"])["items"][0]
        assert (
            state["state"] == "failed"
            and state["error_code"] == "KNOWLEDGE_INDEX_PUBLICATION_REJECTED"
        )
    recovered, _ = indexed(env)
    assert recovered != index
    with connection() as c:
        assert index_history(c, env["item"])["items"][0]["current_ready"]


@pytest.mark.parametrize(
    "change", ["extra_direct_parent", "wrong_own_policy", "known_before_parent"]
)
def test_unit_own_lineage_policy_and_derived_known_time_are_independent_of_parent_ready(
    environment, change
):
    import psycopg

    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_search import assert_trace_scope_current

    env = environment
    publish(env)
    index, _ = indexed(env)
    scope, _ = traced(env, index)
    unit = UUID(scope["units"][0]["unit_source_id"])
    with connection(True) as c:
        if change == "extra_direct_parent":
            c.execute(
                "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
                (unit, env["root"]),
            )
        elif change == "wrong_own_policy":
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), c.transaction():
                c.execute(
                    "UPDATE retrieval.source_nodes SET policy_class='official_label' WHERE id=%s",
                    (unit,),
                )
        else:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), c.transaction():
                c.execute(
                    "UPDATE retrieval.source_nodes SET known_at=clock_timestamp()-interval '365 days' WHERE id=%s",
                    (unit,),
                )
    with connection() as c:
        if change != "extra_direct_parent":
            assert_trace_scope_current(c, scope, env["target"], "authoring")
        else:
            with pytest.raises(DomainError) as exc:
                assert_trace_scope_current(c, scope, env["target"], "authoring")
            assert exc.value.code == "KNOWLEDGE_UNIT_INVALID"


def test_database_constraints_reject_null_ready_wrong_vectors_and_cross_profile_or_version(
    environment,
):
    import json

    import psycopg

    env = environment
    active = publish(env)
    index, outcome = indexed(env)
    with connection() as c:
        profile = c.execute(
            "SELECT profile_id FROM retrieval.knowledge_indexes WHERE id=%s", (index,)
        ).fetchone()["profile_id"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("INSERT INTO retrieval.knowledge_indexes(state) VALUES('ready')")
    with connection(True) as c:
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute(
                "UPDATE retrieval.knowledge_indexes SET state='ready',expected_unit_count=NULL,published_unit_count=0,unit_manifest_digest=NULL,completed_at=NULL WHERE id=%s",
                (index,),
            )
        with pytest.raises(psycopg.DataError), c.transaction():
            c.execute(
                "INSERT INTO retrieval.knowledge_unit_embeddings(index_id,unit_id,profile_id,embedding) VALUES(%s,%s,%s,%s::design.vector)",
                (index, outcome.units[0].id, profile, json.dumps([1.0] + [0.0] * 1022)),
            )
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute(
                "INSERT INTO retrieval.knowledge_unit_embeddings(index_id,unit_id,profile_id,embedding) VALUES(%s,%s,%s,%s::design.vector)",
                (index, outcome.units[0].id, profile, json.dumps([1.0, 1.0] + [0.0] * 1022)),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
            c.execute(
                "INSERT INTO retrieval.knowledge_unit_embeddings(index_id,unit_id,profile_id,embedding) VALUES(%s,%s,%s,%s::design.vector)",
                (index, outcome.units[0].id, uuid4(), json.dumps(outcome.vectors[0])),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
            new_source = uuid4()
            c.execute(
                "INSERT INTO retrieval.source_nodes(id,kind,policy_class,known_at,digest) VALUES(%s,'summary','prebid_content',clock_timestamp(),%s)",
                (new_source, outcome.units[0].digest),
            )
            c.execute(
                "INSERT INTO retrieval.knowledge_units(id,index_id,knowledge_version_id,ordinal,source_id,unit_kind,span_start,span_end,text_content,text_digest,token_count,lexemes,provenance) SELECT %s,index_id,%s,99,%s,unit_kind,span_start,span_end,text_content,text_digest,token_count,lexemes,provenance FROM retrieval.knowledge_units WHERE id=%s",
                (uuid4(), UUID(env["candidate"]["version_id"]), new_source, outcome.units[0].id),
            )
        assert str(active["version_id"]) != env["candidate"]["version_id"]


def test_api_coordinator_own_scope_freezes_one_intent_and_replays_without_duplicate(
    environment, monkeypatch
):
    from packages.knowledge import rag_coordinator

    env = environment
    publish(env)
    monkeypatch.setattr(rag_coordinator, "DenseHttp", ExplicitMechanismDense)
    assert rag_coordinator.schedule_once([env["item"]]) == 1
    assert rag_coordinator.schedule_once([env["item"]]) == 0
    with connection() as c:
        indexes = c.execute(
            "SELECT i.* FROM retrieval.knowledge_indexes i JOIN retrieval.knowledge_versions v ON v.id=i.knowledge_version_id WHERE v.item_id=%s",
            (env["item"],),
        ).fetchall()
        assert len(indexes) == 1 and indexes[0]["state"] == "queued"


@pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_RAG_OSS") != "1", reason="仅精确自有合成原件的原生OSS验收"
)
def test_native_oss_exact_own_original_page_and_historical_reference_survive_withdrawal():
    import json
    from pathlib import Path

    import pypdfium2

    from packages.domain import storage
    from packages.knowledge.rag_search import cite_unit, reference_history, trace_history

    assert os.environ.get("OBJECT_STORE_BACKEND") == "oss"
    env = base_knowledge.environment.__wrapped__()  # Native OSS put; no memory adapter.
    active = publish(env)
    index, _ = indexed(env)  # Explicit fixture vectors; this check proves object/page fidelity.
    _, result = traced(env, index)
    hit = next(hit for hit in result["hits"] if hit["unit_kind"] == "original_quote")
    body = cite_body(hit) | {"target_object_id": str(env["target"])}
    with connection() as c:
        reference = cite_unit(c, UUID(result["trace_id"]), env["actor"], body, None)
    raw = storage.get(env["blob_key"])  # One exact known key; no bucket listing.
    assert raw == env["original"]
    pdf = pypdfium2.PdfDocument(raw)
    page = pdf[hit["provenance"]["reference"]["physical_page"] - 1]
    textpage = page.get_textpage()
    assert hit["text"] in textpage.get_text_bounded()
    textpage.close()
    page.close()
    pdf.close()
    with connection(True) as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
            (env["root"],),
        )
    with connection() as c:
        prior = reference_history(c, UUID(reference["citation_id"]))
        assert prior["reference"] == reference and prior["current_reasons"]
        assert trace_history(c, UUID(result["trace_id"]))["retrieval"] == result
    restored = storage.get(env["blob_key"])
    assert restored == raw and sha(restored) == sha(env["original"])
    client, headers = login_client(env)
    with client:
        old = client.get("/api/v1/knowledge-unit-references/" + reference["citation_id"])
        assert old.status_code == 200 and old.json()["reference"] == reference
        grant = client.get(
            "/api/v1/artifacts/"
            + hit["provenance"]["reference"]["document_version_id"]
            + "/download"
        )
        assert grant.status_code == 200 and grant.json()["download_url"]
        assert (
            client.post(
                "/api/v1/knowledge-retrievals/" + result["trace_id"] + "/references",
                json=body,
                headers=headers,
            ).status_code
            == 409
        )
    target = os.environ.get("KNOWLEDGE_RAG_EVIDENCE_DIR")
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "native-oss-history-result.json").write_text(
            json.dumps(
                {
                    "scope": "native OSS exact own synthetic key put/get plus actual PDFium page and authenticated history/grant creation; signed URL never logged or externally fetched",
                    "knowledge_version_id": active["version_id"],
                    "index_id": str(index),
                    "trace_id": result["trace_id"],
                    "citation_id": reference["citation_id"],
                    "original_sha256": sha(raw),
                    "historical_sha256": sha(restored),
                    "physical_page": hit["provenance"]["reference"]["physical_page"],
                    "quoted_text_sha256": sha(hit["text"]),
                    "source_withdrawn_new_reference_denied": True,
                    "native_get_count": 2,
                    "object_listing_or_configuration_changes": 0,
                    "object_deleted": False,
                    "model_adapter": "explicit mechanism vectors; no quality claim",
                    "paid_model_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


def test_runtime_unavailable_never_fakes_hits_or_leaves_incomplete_command(
    environment, mechanism_http, monkeypatch
):
    from packages.domain.runtime import DomainError
    from packages.knowledge import rag_api
    from packages.rag.http_port import RuntimeUnavailable

    env = environment
    publish(env)
    indexed(env)
    body = {
        "query": "原件措施",
        "purpose": "authoring",
        "limit": 2,
        "knowledge_ids": [str(env["item"])],
    }
    key = uuid4().hex

    def unavailable():
        raise RuntimeUnavailable("explicit_local_runtime_failure")

    monkeypatch.setattr(rag_api, "DenseHttp", unavailable)
    with pytest.raises(DomainError) as error:
        rag_api.retrieve(env["target"], env["actor"], key, body)
    assert error.value.status == 503 and error.value.retryable
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM commands WHERE actor_id=%s AND idempotency_key=%s",
                (env["actor"], key),
            ).fetchone()["n"]
            == 0
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_retrieval_traces WHERE bid_id=%s",
                (env["target"],),
            ).fetchone()["n"]
            == 0
        )
    monkeypatch.setattr(rag_api, "DenseHttp", ExplicitMechanismDense)
    result, replay = rag_api.retrieve(env["target"], env["actor"], key, body)
    assert not replay and result["body"]["hits"]


def test_foreign_subject_is_filtered_and_reference_target_cannot_cross_projects(
    environment, mechanism_http
):
    from packages.domain.runtime import DomainError
    from packages.knowledge import rag_api
    from packages.knowledge.rag_search import cite_unit

    env = environment
    publish(env)
    index, _ = indexed(env)
    body = {
        "query": "道路措施",
        "purpose": "authoring",
        "limit": 2,
        "knowledge_ids": [str(env["item"])],
    }
    foreign, replay = rag_api.retrieve(env["foreign"], env["actor"], uuid4().hex, body)
    assert (
        not replay
        and foreign["body"]["hits"] == []
        and foreign["body"]["coverage"] == "unavailable"
    )
    _, result = traced(env, index)
    with connection() as c:
        with pytest.raises(DomainError) as error:
            cite_unit(
                c,
                UUID(result["trace_id"]),
                env["actor"],
                cite_body(result["hits"][0]) | {"target_object_id": str(env["foreign"])},
                None,
            )
        assert error.value.code == "KNOWLEDGE_TARGET_INVALID"


def test_schema_guard_refuses_real_worker_as_api_without_granting_trace_capability():
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_repository import assert_schema

    assert_role(True)
    with connection(True) as c:
        assert_schema(c, worker=True)
        with pytest.raises(DomainError) as error:
            assert_schema(c)
        assert error.value.code == "KNOWLEDGE_RAG_NOT_READY" and error.value.status == 503



def test_architect_final_search_RR_refuses_before_trace_and_command(environment,mechanism_http,monkeypatch):
    from contextlib import contextmanager
    from packages.knowledge import rag_api
    from packages.domain.runtime import DomainError
    env=environment
    publish(env);indexed(env)
    original=rag_api.connection
    opened=[0]
    @contextmanager
    def final_rr():
        opened[0]+=1
        with original() as c:
            if opened[0]==4:c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            yield c
    monkeypatch.setattr(rag_api,'connection',final_rr)
    with pytest.raises(DomainError) as error:
        rag_api.retrieve(env['target'],env['actor'],uuid4().hex,{'query':'原件参考','purpose':'authoring','knowledge_ids':[str(env['item'])],'limit':8})
    assert error.value.code=='KNOWLEDGE_SOURCE_OR_PROFILE_CHANGED'
    assert opened[0]==4
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_retrieval_traces WHERE bid_id=%s',(env['target'],)).fetchone()['n']==0
        assert c.execute("SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='knowledge-retrieve'",(env['target'],)).fetchone()['n']==0


def test_architect_first_reference_RR_and_replay_RR_refuse(environment,mechanism_http):
    from packages.knowledge import rag_api
    from packages.knowledge.rag_search import cite_unit
    from packages.domain.runtime import DomainError
    from contextlib import contextmanager
    from unittest.mock import patch
    env=environment
    publish(env);indexed(env)
    body={'query':'原件参考','purpose':'authoring','knowledge_ids':[str(env['item'])],'limit':8}
    key=uuid4().hex
    response,_=rag_api.retrieve(env['target'],env['actor'],key,body)
    hit=response['body']['hits'][0]
    with pytest.raises(ValueError,match='fresh read-committed') as error,connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cite_unit(c,UUID(response['body']['trace_id']),env['actor'],cite_body(hit)|{'target_object_id':str(env['target'])},None)
    original=rag_api.connection
    @contextmanager
    def rr():
        with original() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            yield c
    with patch.object(rag_api,'connection',rr),pytest.raises(ValueError,match='fresh read-committed') as error:
        rag_api.retrieve(env['target'],env['actor'],key,body)
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_unit_references WHERE bid_id=%s',(env['target'],)).fetchone()['n']==0



@pytest.mark.parametrize('change',['source','identity'])
def test_architect_rerank_window_source_or_identity_change_prevents_trace(environment,mechanism_http,monkeypatch,change):
    from packages.knowledge import rag_api
    from packages.identity.repository import record_binding
    from packages.domain.runtime import DomainError
    from contextlib import contextmanager
    env=environment
    publish(env);indexed(env)
    original=rag_api.connection
    active=[]
    pids=[]
    @contextmanager
    def tracked():
        with original() as c:
            active.append(c);pids.append(c.info.backend_pid)
            try:yield c
            finally:active.remove(c)
    monkeypatch.setattr(rag_api,'connection',tracked)
    class ChangedReranker(ExplicitMechanismReranker):
        def rank(self,query,texts):
            assert not active
            with connection() as c:
                assert c.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE pid=ANY(%s)',(pids,)).fetchone()['n']==0
                c.execute("SET LOCAL lock_timeout='2s'")
                if change=='source':
                    c.execute('SELECT pg_advisory_xact_lock(90127,1)')
                    c.execute("UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",(env['root'],))
                else:
                    c.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(env['target'],))
                    record_binding(c,env['target'],env['actor'],None,reason='架构独立重排期间同UUID重新确认',confirmed=True)
            return super().rank(query,texts)
    monkeypatch.setattr(rag_api,'RerankHttp',lambda dense:ChangedReranker())
    with pytest.raises(DomainError):
        rag_api.retrieve(env['target'],env['actor'],uuid4().hex,{'query':'原件参考','purpose':'authoring','knowledge_ids':[str(env['item'])],'limit':8})
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_retrieval_traces WHERE bid_id=%s',(env['target'],)).fetchone()['n']==0
        assert c.execute("SELECT count(*) AS n FROM commands WHERE target_id=%s AND command_kind='knowledge-retrieve'",(env['target'],)).fetchone()['n']==0
