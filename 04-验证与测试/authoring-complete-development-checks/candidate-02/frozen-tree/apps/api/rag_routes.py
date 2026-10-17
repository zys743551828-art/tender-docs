"""Project retrieval: short DB stages, model work outside connections, final authority check."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import (
    ROOT,
    Command,
    DomainError,
    clean,
    connection,
    fetch,
    now,
    sha,
    validate,
)
from packages.domain.source_effectivity import EVENT, evidence_impacts
from packages.rag import search
from packages.rag.core import normalized_vector
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp, RuntimeUnavailable
from packages.rag.indexing import ensure_profile
from packages.rag.repository import (
    assert_embedding_profile,
    resolve_project_scope,
    source_manifest,
    source_scope_digest,
)

router = APIRouter()


def assert_fresh_authority(c):
    if (
        c.autocommit
        or c.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "read committed"
    ):
        raise DomainError(
            "RAG_FRESH_AUTHORITY_REQUIRED",
            "检索采用需使用可读取最新效力的短事务，请检查服务配置后重试",
            409,
        )


def control(c, bid):
    heads = fetch(
        c,
        "SELECT b.material_revision,p.current_body_id,p.current_material_id,p.current_rule_set_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    effects = c.execute(
        "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type=%s ORDER BY id", (bid, EVENT)
    ).fetchall()
    return clean(heads) | {"effectivity_decisions": [str(row["id"]) for row in effects]}


def guard_effectivity(c, bid, purpose, chunk_ids):
    if purpose == "history" or not chunk_ids:
        return
    blocks = c.execute(
        "SELECT DISTINCT unnest(evidence_ids) AS id FROM retrieval.chunks WHERE id=ANY(%s)",
        (list(chunk_ids),),
    ).fetchall()
    impacts = evidence_impacts(c, bid, [row["id"] for row in blocks])
    if impacts:
        raise DomainError(
            "RETRIEVAL_EFFECTIVITY_REVIEW_REQUIRED",
            "检索文字涉及已替代或待核验的要求，请先核对补遗效力；历史原页仍可查看",
            409,
            {"impacts": impacts},
        )


def excluded_evidence(c, bid, purpose):
    if purpose == "history":
        return []
    rows = c.execute(
        "SELECT DISTINCT entry.evidence_id::uuid AS id FROM audit_entries a CROSS JOIN LATERAL jsonb_array_elements(a.payload->'ended_rules') ended(item) CROSS JOIN LATERAL jsonb_array_elements_text(ended.item->'evidence_ids') entry(evidence_id) WHERE a.bid_id=%s AND a.event_type=%s UNION SELECT DISTINCT unnest(r.evidence_ids) AS id FROM rule_items r JOIN bid_profiles p ON p.bid_id=r.bid_id AND p.current_rule_set_id=r.rule_set_id WHERE r.bid_id=%s AND r.provenance_status='needs_revalidation'",
        (bid, EVENT, bid),
    ).fetchall()
    return sorted((row["id"] for row in rows), key=str)


def replay(c, body, bid, saved):
    assert_fresh_authority(c)
    c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid,))
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    current = resolve_project_scope(c, bid, body["context"], now())
    prior = fetch(
        c,
        "SELECT excluded_reasons,allowed_manifest_digest FROM retrieval.retrieval_traces WHERE id=%s AND bid_id=%s",
        (saved["body"]["trace_id"], bid),
    )
    original = prior["excluded_reasons"].get("scope", {})
    if (
        original.get("source_ids") != [str(value) for value in current.source_ids]
        or original.get("control") != control(c, bid)
        or source_scope_digest(source_manifest(c, current), original)
        != prior["allowed_manifest_digest"]
    ):
        raise DomainError(
            "RETRIEVAL_SOURCE_CHANGED", "此前检索范围、资料头或效力已变化，请重新查证", 409
        )
    selected = {UUID(hit["chunk_id"]) for hit in saved["body"]["hits"]}
    eligible = c.execute(
        "SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,false) WHERE id=ANY(%s)",
        (list(current.source_ids), current.as_of, list(current.excluded_groups), list(selected)),
    ).fetchall()
    if {row["id"] for row in eligible} != selected:
        raise DomainError("RETRIEVAL_SOURCE_CHANGED", "此前检索依据已失效，请重新查证", 409)
    guard_effectivity(c, bid, current.purpose, selected)
    return JSONResponse(
        saved["body"], status_code=saved["status"], headers={"Idempotent-Replayed": "true"}
    )


@router.post("/api/v1/retrievals")
def retrieval(request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("RetrievalRequest", body)
    if body["bid_id"] is None:
        raise DomainError("PROJECT_REQUIRED", "请先选择要查证的项目", 422)
    if not body["query"].strip():
        raise DomainError("INVALID_QUERY", "请输入需要查证的内容", 422)
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 128:
        raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)
    bid = UUID(body["bid_id"])
    try:
        # No uncommitted Command/FK/unique-index lock spans local model HTTP.
        with connection() as c:
            assert_fresh_authority(c)
            old = c.execute(
                "SELECT request_digest,response FROM commands WHERE actor_id=%s AND command_kind='retrieve-evidence' AND target_id=%s AND idempotency_key=%s",
                (actor["member_id"], bid, key),
            ).fetchone()
            if old:
                if old["request_digest"] != sha({"body": body, "if_match": None}):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应不同输入", 409)
                if old["response"] is None:
                    raise DomainError("COMMAND_INCOMPLETE", "查证尚未完成，请稍后重试", 409)
                return replay(c, body, bid, old["response"])
            c.commit()
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            c.execute("SET LOCAL search_path=design,public")
            scope = resolve_project_scope(c, bid, body["context"], now())
            frozen_control, manifest = control(c, bid), source_manifest(c, scope)
        dense = DenseHttp()
        lexical, reranker = LexicalHttp(dense), RerankHttp(dense)
        policy = sha(
            {
                "policy": sha(
                    (ROOT / "packages/contracts/policies/retrieval-policy.json").read_bytes()
                ),
                "lexical_dictionary": dense.lexical_dictionary_digest,
            }
        )
        with connection() as c:
            profile = ensure_profile(c, dense.identity)
        vectors = dense.embed([body["query"]])
        if len(vectors) != 1:
            raise ValueError("Incomplete query embedding")
        vector = "[" + ",".join(map(str, normalized_vector(vectors[0]))) + "]"
        lexemes = lexical.lexemes(body["query"])
        with connection() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            assert_embedding_profile(c, profile, dense)
            if source_manifest(c, scope) != manifest or control(c, bid) != frozen_control:
                raise DomainError("RETRIEVAL_SOURCE_CHANGED", "查证期间资料已变化，请重新查询", 409)
            blocked = excluded_evidence(c, bid, scope.purpose)
            rows = search.candidates(c, scope, profile, vector, lexemes, blocked)
        rows, route, observed_reranker = search.rerank(body["query"], rows, reranker)
        rows = rows[: min(body["limit"], 8)]
        with connection() as c:
            assert_fresh_authority(c)
            c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid,))
            c.execute("SELECT pg_advisory_xact_lock(90127,1)")
            current = resolve_project_scope(c, bid, body["context"], now())
            if (
                current.source_ids != scope.source_ids
                or control(c, bid) != frozen_control
                or source_manifest(c, current) != manifest
            ):
                raise DomainError(
                    "RETRIEVAL_SOURCE_CHANGED", "查证期间资料或效力已变化，请重新查询", 409
                )
            selected = {row["id"] for row in rows}
            eligible = c.execute(
                "SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,false) WHERE id=ANY(%s)",
                (
                    list(current.source_ids),
                    current.as_of,
                    list(current.excluded_groups),
                    list(selected),
                ),
            ).fetchall()
            if {row["id"] for row in eligible} != selected:
                raise DomainError("RETRIEVAL_SOURCE_CHANGED", "检索依据已失效，请重新查询", 409)
            guard_effectivity(c, bid, current.purpose, selected)
            cmd = Command(c, actor["member_id"], "retrieve-evidence", bid, key, body)
            if cmd.replay:
                return replay(c, body, bid, cmd.replay)
            trace = search.trace(
                c,
                scope,
                frozen_control,
                manifest,
                body["query"],
                profile,
                policy,
                rows,
                route,
                observed_reranker,
            )
            hits = []
            for rank, row in enumerate(rows, start=1):
                citation = search.citation_id(trace, row["id"])
                c.execute(
                    "INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,chunk_id,target_object_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        citation,
                        bid,
                        row["source_id"],
                        row["source_revision"],
                        scope.purpose,
                        row["id"],
                        trace,
                    ),
                )
                hits.append(
                    {
                        "chunk_id": str(row["id"]),
                        "source_version_id": str(row["document_version_id"]),
                        "parse_run_id": str(row["parse_id"]),
                        "physical_page": row["physical_page"],
                        "citation_id": str(citation),
                        "excerpt": row["text_content"],
                        "embedding_profile_id": str(profile),
                        "rank": rank,
                    }
                )
            gaps = ["仅检索已建立索引的可读文字；扫描、结构化表格和图页范围以资料读取记录为准"]
            if not scope.source_ids:
                gaps.append("当前资料尚无可用索引，请先完成解析和索引任务")
            if "reranker_unavailable" in route:
                gaps.append("本次重排暂不可用，已使用向量与词法融合顺序")
            if blocked:
                gaps.append("涉及已替代或待核验要求的文字已在检索候选阶段排除；可切换历史查证原文")
            if scope.purpose == "history":
                gaps.append("历史原文仅供查证；已替代或待核验的内容不能作为当前义务")
            response = {
                "trace_id": str(trace),
                "policy_digest": policy,
                "hits": hits,
                "coverage": "partial" if hits else "unavailable",
                "gaps": gaps,
            }
            validate("RetrievalResult", response)
            cmd.finish(response)
            return JSONResponse(response)
    except RuntimeUnavailable:
        raise DomainError("RAG_UNAVAILABLE", "检索模型服务暂不可用，请稍后重试", 503) from None
    except ValueError:
        raise DomainError(
            "RAG_SOURCE_OR_PROFILE_CHANGED", "资料效力或检索模型已变化，请重新查证", 409
        ) from None
