"""Actual project-scoped retrieval endpoint; no client-provided purpose or allowlist."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import (
    ROOT,
    Command,
    DomainError,
    connection,
    fetch,
    now,
    sha,
    validate,
)
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp, RuntimeUnavailable
from packages.rag.indexing import ensure_profile
from packages.rag.repository import (
    assert_current_sources,
    resolve_project_scope,
    retrieve,
    source_manifest,
    source_scope_digest,
)

router = APIRouter()


@router.post("/api/v1/retrievals")
def retrieval(request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("RetrievalRequest", body)
    if body["bid_id"] is None:
        raise DomainError("PROJECT_REQUIRED", "请先选择要查证的项目", 422)
    bid_id = UUID(body["bid_id"])
    try:
        dense = DenseHttp()
        lexical = LexicalHttp(dense)
        with connection() as c:
            fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
            cmd = Command(
                c,
                actor["member_id"],
                "retrieve-evidence",
                bid_id,
                request.headers.get("idempotency-key"),
                body,
            )
            if cmd.replay:
                # Key-share taken by trace FKs is compatible with NO KEY UPDATE.
                # This serializes material/body heads without holding a bid lock
                # during model work or creating a key-share upgrade deadlock.
                c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid_id,))
                c.execute("SELECT pg_advisory_xact_lock(90127,1)")
                current = resolve_project_scope(c, bid_id, body["context"], now())
                prior = fetch(
                    c,
                    "SELECT excluded_reasons,allowed_manifest_digest FROM retrieval.retrieval_traces WHERE id=%s",
                    (cmd.replay["body"]["trace_id"],),
                )
                prior_sources = prior["excluded_reasons"].get("scope", {}).get("source_ids")
                if prior_sources != [str(value) for value in current.source_ids]:
                    raise DomainError(
                        "RETRIEVAL_SOURCE_CHANGED", "此前检索范围已变化，请重新查证", 409
                    )
                original_scope = prior["excluded_reasons"].get("scope", {})
                if (
                    source_scope_digest(source_manifest(c, current), original_scope)
                    != prior["allowed_manifest_digest"]
                ):
                    raise DomainError(
                        "RETRIEVAL_SOURCE_CHANGED",
                        "此前检索来源的版本或效力已变化，请重新查证",
                        409,
                    )
                selected = {UUID(hit["chunk_id"]) for hit in cmd.replay["body"]["hits"]}
                eligible = c.execute(
                    "SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,false) WHERE id=ANY(%s)",
                    (list(current.source_ids), current.as_of, [], list(selected)),
                ).fetchall()
                if {row["id"] for row in eligible} != selected:
                    raise DomainError(
                        "RETRIEVAL_SOURCE_CHANGED", "此前检索依据已变化，请重新查证", 409
                    )
                return JSONResponse(
                    cmd.replay["body"],
                    status_code=cmd.replay["status"],
                    headers={"Idempotent-Replayed": "true"},
                )
            scope = resolve_project_scope(c, bid_id, body["context"], now())
            profile_id = ensure_profile(c, dense.identity)
            policy_digest = sha(
                {
                    "policy": sha(
                        (ROOT / "packages/contracts/policies/retrieval-policy.json").read_bytes()
                    ),
                    "lexical_dictionary": dense.lexical_dictionary_digest,
                }
            )
            result = retrieve(
                c,
                scope,
                body["query"],
                dense,
                profile_id,
                policy_digest,
                lexical,
                RerankHttp(dense),
            )
            c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid_id,))
            current = resolve_project_scope(c, bid_id, body["context"], scope.as_of)
            if current.source_ids != scope.source_ids:
                raise DomainError(
                    "RETRIEVAL_SOURCE_CHANGED", "查证期间资料版本已变化，请重新查询", 409
                )
            assert_current_sources(c, result)
            evidence = result["evidence"][: min(body["limit"], 8)]
            hits = []
            gaps = []
            for rank, item in enumerate(evidence, start=1):
                original = fetch(
                    c,
                    "SELECT document_version_id FROM retrieval.source_nodes WHERE id=%s",
                    (item.source_id,),
                )
                hits.append(
                    {
                        "chunk_id": str(item.chunk_id),
                        "source_version_id": str(original["document_version_id"]),
                        "parse_run_id": str(item.parse_id),
                        "physical_page": item.physical_page,
                        "citation_id": item.citation_id,
                        "excerpt": item.text,
                        "embedding_profile_id": str(profile_id),
                        "rank": rank,
                    }
                )
                c.execute(
                    """INSERT INTO retrieval.citations
                    (id,bid_id,source_id,source_revision,purpose,chunk_id,target_object_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        UUID(item.citation_id),
                        bid_id,
                        item.source_id,
                        item.source_revision,
                        scope.purpose,
                        item.chunk_id,
                        result["trace_id"],
                    ),
                )
            coverage = "partial" if hits else "unavailable"
            gaps.append("仅检索已建立索引的可读文字；扫描、结构化表格和图页范围以资料读取记录为准")
            if not scope.source_ids:
                gaps.append("当前资料尚无可用索引，请先完成解析和索引任务")
            if "reranker_unavailable" in result["route"]:
                gaps.append("本次重排暂不可用，已使用向量与词法融合顺序")
            response = {
                "trace_id": str(result["trace_id"]),
                "policy_digest": policy_digest,
                "hits": hits,
                "coverage": coverage,
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
