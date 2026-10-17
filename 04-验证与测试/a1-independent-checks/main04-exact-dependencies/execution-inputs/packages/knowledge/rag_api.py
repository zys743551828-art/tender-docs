"""API control-plane orchestration; no database connection spans model I/O."""

from uuid import UUID

from packages.domain.runtime import Command, DomainError, connection, sha
from packages.knowledge.rag_search import (
    assert_trace_scope_current,
    freeze_scope,
    read_candidates,
    rerank_candidates,
    save_trace,
    trace_row,
)
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp, RuntimeUnavailable
from packages.rag.indexing import ensure_profile


def require_key(key):
    if not key or len(key) > 128:
        raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)


def revalidated_response(c, response, bid, purpose):
    prior = trace_row(c, UUID(response["body"]["trace_id"]))
    assert_trace_scope_current(c, prior["frozen_scope"], bid, purpose)
    allowed = {u["unit_id"]: u for u in prior["frozen_scope"]["units"]}
    if any(u != allowed.get(u["unit_id"]) for u in prior["selected_units"]):
        raise DomainError("KNOWLEDGE_TRACE_INVALID", "历史供给记录不属于当时允许范围", 409)
    return response, True


def retrieve(bid, actor, key, body):
    require_key(key)
    # Read-only preflight: no incomplete Command row or row lock crosses model work.
    with connection() as c:
        old = c.execute(
            "SELECT request_digest,response FROM commands WHERE actor_id=%s AND command_kind='knowledge-retrieve' AND target_id=%s AND idempotency_key=%s",
            (actor, bid, key),
        ).fetchone()
        if old:
            if old["request_digest"] != sha({"body": body, "if_match": None}):
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应不同输入", 409)
            if not old["response"]:
                raise DomainError("COMMAND_INCOMPLETE", "操作尚未完成", 409)
            return revalidated_response(c, old["response"], bid, body["purpose"])
    try:
        dense = DenseHttp()
        lexical = LexicalHttp(dense)
        with connection() as c:
            profile = ensure_profile(c, dense.identity)
            scope = freeze_scope(
                c,
                bid,
                body["purpose"],
                profile,
                dense.identity,
                dense.lexical_dictionary_digest,
                body.get("knowledge_ids"),
            )
        if scope["units"]:
            # All tokenizer/embedding/lexical methods execute with the above connection closed.
            if len(dense.offsets(body["query"])) > 1200:
                raise DomainError("KNOWLEDGE_QUERY_TOO_LONG", "请缩短检索问题后重试", 422)
            vectors = dense.embed([body["query"]])
            if len(vectors) != 1:
                raise ValueError("KNOWLEDGE_QUERY_VECTOR_INCOMPLETE")
            query_lexemes = lexical.lexemes(body["query"])
            with connection() as c:
                c.execute("SET TRANSACTION READ ONLY")
                candidates = read_candidates(c, scope, vectors[0], query_lexemes)
            selected, route, reranker_identity = rerank_candidates(
                body["query"], candidates, RerankHttp(dense)
            )
        else:
            selected, route, reranker_identity = [], ["no_eligible_index"], None
        with connection() as c:
            assert_trace_scope_current(c, scope, bid, body["purpose"])
            command = Command(c, actor, "knowledge-retrieve", bid, key, body)
            if command.replay:
                return revalidated_response(c, command.replay, bid, body["purpose"])
            result = save_trace(
                c, scope, actor, body["query"], selected, route, reranker_identity, body["limit"]
            )
            return command.finish(result), False
    except RuntimeUnavailable:
        raise DomainError(
            "KNOWLEDGE_RUNTIME_UNAVAILABLE",
            "本地知识检索模型暂不可用，请稍后重试",
            503,
            retryable=True,
        ) from None
    except ValueError:
        raise DomainError(
            "KNOWLEDGE_SOURCE_OR_PROFILE_CHANGED", "知识来源或模型身份不符，请重新查证", 409
        ) from None
