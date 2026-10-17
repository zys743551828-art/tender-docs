"""Authenticated enterprise knowledge management and explicit project reference."""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection, fetch, validate
from packages.knowledge import bridge, repository
from packages.knowledge.content import validate_version

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def respond(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def write_command(c, r, actor, kind, target, body, conditional=True):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match") if conditional else None,
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
        cmd = write_command(c, r, actor, "knowledge-reference", knowledge_id, body, False)
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
