"""Authenticated enterprise knowledge management and explicit project reference."""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import (
    Command,
    DomainError,
    connection,
    fetch,
    require_read_committed,
    validate,
)
from packages.domain.work_access import begin
from packages.knowledge import bridge, repository
from packages.knowledge.content import validate_version

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def respond(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def write_command(c, r, actor, kind, target, body, conditional=True, *, bid=None):
    require_read_committed(c)
    factory = Command if bid is None else begin
    return factory(
        c,
        actor["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match") if conditional else None,
        **({"bid": bid} if bid is not None else {}),
    )


@router.post("/knowledge")
def create(r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("KnowledgeCandidate", body)
    # Stable collection identity; no synthetic bid is invented for global knowledge.
    from uuid import NAMESPACE_URL, uuid5

    collection = uuid5(NAMESPACE_URL, "tender-workbench/knowledge")
    with connection() as c:
        cmd = write_command(c, r, actor, "knowledge-create", collection, body, False)
        if cmd.replay:
            return respond(cmd.replay, True)
        result = repository.create_candidate(c, actor["member_id"], body)
        return respond(cmd.finish(result, 201, {"ETag": repository.knowledge_etag(result)}))


@router.get("/knowledge")
def search(
    r: Request,
    q: str | None = None,
    bid_id: UUID | None = None,
    purpose: str = "assist",
    state: str = "published",
    after: UUID | None = None,
    limit: int = Query(30, ge=1, le=100),
):
    auth(r)
    with connection() as c:
        return repository.search(c, q, after, limit, bid_id, purpose, state)


@router.get("/knowledge/{knowledge_id}")
def get(knowledge_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = repository.view(c, knowledge_id)
        return JSONResponse(result, headers={"ETag": repository.knowledge_etag(result)})


@router.post("/knowledge/{knowledge_id}/versions")
def version(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate_version(body)
    with connection() as c:
        cmd = write_command(c, r, actor, "knowledge-version", knowledge_id, body)
        if cmd.replay:
            return respond(cmd.replay, True)
        result = repository.add_version(
            c, knowledge_id, actor["member_id"], body, r.headers.get("if-match")
        )
        return respond(cmd.finish(result, 201, {"ETag": repository.knowledge_etag(result)}))


def transition(knowledge_id, r, body, action):
    actor = auth(r, True)
    validate("DecisionRequest", body)
    with connection() as c:
        cmd = write_command(c, r, actor, "knowledge-" + action, knowledge_id, body)
        if cmd.replay:
            return respond(cmd.replay, True)
        result = repository.transition(
            c, knowledge_id, actor["member_id"], body, r.headers.get("if-match"), action
        )
        return respond(
            cmd.finish(
                result,
                headers={"ETag": repository.knowledge_etag(result)},
            )
        )


@router.post("/knowledge/{knowledge_id}/submit")
def submit(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    return transition(knowledge_id, r, body, "submit")


@router.post("/knowledge/{knowledge_id}/publish")
def publish(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    return transition(knowledge_id, r, body, "publish")


@router.post("/knowledge/{knowledge_id}/withdraw")
def withdraw(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    return transition(knowledge_id, r, body, "withdraw")


@router.get("/knowledge/{knowledge_id}/versions")
def versions(
    knowledge_id: UUID,
    r: Request,
    before: int | None = Query(None, ge=1),
    limit: int = Query(30, ge=1, le=100),
):
    auth(r)
    with connection() as c:
        fetch(c, "SELECT id FROM retrieval.knowledge_items WHERE id=%s", (knowledge_id,))
        rows = c.execute(
            "SELECT id,version_no FROM retrieval.knowledge_versions WHERE item_id=%s AND (%s::bigint IS NULL OR version_no<%s::bigint) ORDER BY version_no DESC LIMIT %s",
            (knowledge_id, before, before, limit + 1),
        ).fetchall()
        return {
            "items": [repository.view(c, knowledge_id, row["id"]) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["version_no"] if len(rows) > limit else None,
            "has_more": len(rows) > limit,
        }


@router.get("/knowledge/{knowledge_id}/versions/{version_id}")
def historical_version(knowledge_id: UUID, version_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        return repository.view(c, knowledge_id, version_id)


@router.get("/knowledge/{knowledge_id}/impacts")
def impacts(knowledge_id: UUID, r: Request, version_id: UUID | None = None):
    auth(r)
    with connection() as c:
        return bridge.impacts(c, knowledge_id, version_id)


@router.post("/knowledge/{knowledge_id}/references")
def reference(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    from packages.knowledge.contracts import validate_actual

    actor = auth(r, True)
    validate_actual("KnowledgeReferenceRequest", body)
    bid, version = UUID(body["bid_id"]), UUID(body["version_id"])
    with connection() as c:
        cmd = write_command(c, r, actor, "knowledge-reference", knowledge_id, body, False, bid=bid)
        if cmd.replay:
            context = cmd.replay["body"].get("project_context")
            if context is None:
                raise DomainError(
                    "KNOWLEDGE_PROJECT_CONTEXT_MISSING",
                    "历史引用缺少当时主体依据，请重新查证后选用",
                    409,
                )
            bridge.check_reference(
                c, bid, knowledge_id, version, body["purpose"], expected_context=context
            )
            return respond(cmd.replay, True)
        result = bridge.cite(c, bid, knowledge_id, version, actor["member_id"], body, cmd.id)
        return respond(cmd.finish(result, 201))


@router.get("/knowledge-references/{citation_id}")
def historical_reference(citation_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        return bridge.reference_history(c, citation_id)


@router.get("/knowledge/{knowledge_id}/source-positions")
def source_positions(
    knowledge_id: UUID,
    r: Request,
    document_version_id: UUID,
    physical_page: int = Query(1, ge=1),
    after_ordinal: int = Query(-1, ge=-1),
    limit: int = Query(30, ge=1, le=100),
    parse_id: UUID | None = None,
):
    auth(r)
    with connection() as c:
        return repository.source_positions(
            c, knowledge_id, document_version_id, physical_page, after_ordinal, limit, parse_id
        )


@router.post("/bids/{bid_id}/knowledge-retrievals")
def retrieve_knowledge(bid_id: UUID, r: Request, body: dict = Body(...)):
    from packages.knowledge.rag_api import retrieve
    from packages.knowledge.rag_contracts import validate_actual as validate_rag

    actor = auth(r, True)
    validate_rag("KnowledgeRetrievalRequest", body)
    result, replay = retrieve(bid_id, actor["member_id"], r.headers.get("idempotency-key"), body)
    return respond(result, replay)


@router.get("/knowledge-retrievals/{trace_id}")
def get_knowledge_trace(trace_id: UUID, r: Request):
    from packages.knowledge.rag_search import trace_history

    auth(r)
    with connection() as c:
        return trace_history(c, trace_id)


@router.post("/knowledge-retrievals/{trace_id}/references")
def reference_knowledge_unit(trace_id: UUID, r: Request, body: dict = Body(...)):
    from packages.knowledge.rag_contracts import validate_actual as validate_rag
    from packages.knowledge.rag_search import assert_trace_scope_current, cite_unit, trace_row

    actor = auth(r, True)
    validate_rag("KnowledgeUnitReferenceRequest", body)
    with connection() as c:
        trace = trace_row(c, trace_id)
        assert_trace_scope_current(c, trace["frozen_scope"], trace["bid_id"], trace["purpose"])
        cmd = write_command(
            c, r, actor, "knowledge-unit-reference", trace_id, body, False, bid=trace["bid_id"]
        )
        if cmd.replay:
            return respond(cmd.replay, True)
        result = cite_unit(c, trace_id, actor["member_id"], body, cmd.id)
        return respond(cmd.finish(result, 201))


@router.get("/knowledge-unit-references/{citation_id}")
def get_knowledge_unit_reference(citation_id: UUID, r: Request):
    from packages.knowledge.rag_search import reference_history as unit_history

    auth(r)
    with connection() as c:
        return unit_history(c, citation_id)


@router.get("/knowledge/{knowledge_id}/indexes")
def get_knowledge_indexes(knowledge_id: UUID, r: Request):
    from packages.knowledge.rag_repository import index_history

    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return index_history(c, knowledge_id)


@router.post("/knowledge/{knowledge_id}/indexes")
def index_knowledge(knowledge_id: UUID, r: Request, body: dict = Body(...)):
    from packages.domain.runtime import sha
    from packages.knowledge.rag_api import require_key
    from packages.knowledge.rag_contracts import validate_actual as validate_rag
    from packages.knowledge.rag_repository import schedule_index
    from packages.rag.http_port import DenseHttp, RuntimeUnavailable

    actor = auth(r, True)
    validate_rag("KnowledgeIndexRequest", body)
    key = r.headers.get("idempotency-key")
    require_key(key)
    with connection() as c:
        old = c.execute(
            "SELECT request_digest,response FROM commands WHERE actor_id=%s AND command_kind='knowledge-index' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], knowledge_id, key),
        ).fetchone()
        if old and old["response"]:
            if old["request_digest"] != sha({"body": body, "if_match": None}):
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应不同输入", 409)
            return respond(old["response"], True)
    try:
        dense = DenseHttp()  # Metadata I/O occurs outside DB transactions too.
    except RuntimeUnavailable:
        raise DomainError(
            "KNOWLEDGE_RUNTIME_UNAVAILABLE", "知识检索模型暂不可用", 503, retryable=True
        ) from None
    with connection() as c:
        cmd = write_command(c, r, actor, "knowledge-index", knowledge_id, body, False)
        if cmd.replay:
            return respond(cmd.replay, True)
        result = schedule_index(
            c,
            knowledge_id,
            actor["member_id"],
            cmd.id,
            dense,
            expected_version_id=UUID(body["knowledge_version_id"]),
        )
        return respond(cmd.finish(result, 202))
