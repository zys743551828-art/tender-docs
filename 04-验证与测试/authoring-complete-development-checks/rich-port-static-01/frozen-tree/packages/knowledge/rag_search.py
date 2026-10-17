"""API-owned five-phase retrieval and one complete guard for all trace new-use paths."""

from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, fetch, sha
from packages.knowledge import bridge
from packages.knowledge.rag_index import CHUNK_POLICY
from packages.knowledge.rag_repository import (
    assert_profile,
    assert_schema,
    clock,
    fragment_from_row,
    stored_manifest,
    unit_provenance,
)
from packages.knowledge.rag_types import assert_unit_envelope, text_digest
from packages.knowledge.sources import authority_manifest, lock_graph, lock_heads
from packages.rag.core import fuse_rankings, normalized_vector

POLICY = {
    "version": 1,
    "name": "knowledge_units_exact_hybrid",
    "dense": 50,
    "lexical": 50,
    "rrf_k": 60,
    "rerank": 20,
    "limit": 8,
    "max_versions": 500,
    "max_units": 5000,
    "chunking": CHUNK_POLICY,
}
POLICY_DIGEST = sha(POLICY)
MAX_VERSIONS = 500
MAX_UNITS = 5000


def scope_identity(scope):
    return {key: value for key, value in scope.items() if key != "evaluated_at"}


def freeze_scope(
    c, bid, purpose, profile_id, dense_identity, dictionary_digest, knowledge_ids=None
):
    """No model calls; current project authority and every unit's own head are frozen."""
    assert_schema(c)
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid,))
    lock_graph(c)
    at = clock(c)

    assert_profile(c, profile_id, dense_identity)
    scope = bridge.eligible_knowledge_sources(c, bid, purpose, at)
    wanted = set(map(str, knowledge_ids)) if knowledge_ids is not None else None
    eligible = [
        item for item in scope["eligible"] if wanted is None or item["knowledge_id"] in wanted
    ]
    if len(eligible) > MAX_VERSIONS:
        raise DomainError("KNOWLEDGE_SCOPE_TOO_LARGE", "请缩小知识条目范围后检索", 422)
    indexes, units = [], []
    for item in eligible:
        ready = c.execute(
            """SELECT i.* FROM retrieval.knowledge_index_heads h JOIN retrieval.knowledge_indexes i ON i.id=h.ready_index_id
            WHERE h.knowledge_version_id=%s AND h.profile_id=%s AND i.state='ready'
            AND i.knowledge_version_id=h.knowledge_version_id AND i.profile_id=h.profile_id""",
            (UUID(item["version_id"]), profile_id),
        ).fetchone()
        if (
            ready is None
            or ready["lexical_dictionary_digest"] != dictionary_digest
            or ready["chunk_policy_digest"] != sha(CHUNK_POLICY)
        ):
            continue
        m = ready["input_manifest"]
        if (
            m["embedding_identity"] != dense_identity
            or ready["input_digest"] != sha(m)
            or m["knowledge_source_id"] != item["knowledge_source_id"]
            or sha(m["content"]) != item["content_digest"]
            or m["source_refs"] != item["original_evidence_allowlist"]
            or m["authority_manifest"] != item["source_manifest"]
            or sha(stored_manifest(c, ready["id"])) != ready["unit_manifest_digest"]
        ):
            raise DomainError("KNOWLEDGE_INDEX_INVALID", "知识索引身份不一致，请重新建立索引", 409)
        rows = c.execute(
            """SELECT u.*,n.kind AS source_kind,n.digest AS source_digest,n.policy_class AS source_policy FROM retrieval.knowledge_units u
            JOIN retrieval.source_nodes n ON n.id=u.source_id WHERE u.index_id=%s ORDER BY u.ordinal""",
            (ready["id"],),
        ).fetchall()
        if len(rows) != ready["expected_unit_count"] or len(rows) != ready["published_unit_count"]:
            raise DomainError("KNOWLEDGE_INDEX_INCOMPLETE", "知识索引尚未完整建立", 409)
        lock_heads(c, [r["source_id"] for r in rows])
        indexes.append(
            clean(
                {
                    "index_id": ready["id"],
                    "version_id": ready["knowledge_version_id"],
                    "input_digest": ready["input_digest"],
                    "unit_manifest_digest": ready["unit_manifest_digest"],
                }
            )
        )
        for row in rows:
            unit = fragment_from_row(row)
            assert_unit_envelope(
                unit, m["content"], tuple(ref["excerpt"] for ref in m["source_refs"])
            )
            expected_parents = {UUID(item["knowledge_source_id"])}
            if unit.unit_kind == "original_quote":
                expected_parents.add(UUID(m["source_refs"][unit.ref_ordinal]["source_id"]))
            parents = {
                p["parent_id"]
                for p in c.execute(
                    "SELECT parent_id FROM retrieval.lineage_edges WHERE child_id=%s",
                    (row["source_id"],),
                ).fetchall()
            }
            expected_policy = (
                "prebid_content"
                if all(a["policy_class"] == "prebid_content" for a in m["authority_manifest"])
                else "derived_mixed"
            )
            if (
                row["source_kind"] != "summary"
                or row["source_id"] != row["id"]
                or row["source_digest"] != unit.digest
                or row["source_policy"] != expected_policy
                or parents != expected_parents
                or row["provenance"] != unit_provenance(unit, m)
            ):
                raise DomainError("KNOWLEDGE_UNIT_INVALID", "知识单元与批准出处不一致", 409)
            try:
                authority = authority_manifest(c, [row["source_id"]], at)
            except DomainError:
                # A ready index never grants authority to a candidate/withdrawn unit.
                continue
            known = {a["ancestor_id"]: datetime.fromisoformat(a["known_at"]) for a in authority}
            if known[str(row["source_id"])] < max(known.values()):
                raise DomainError("KNOWLEDGE_UNIT_INVALID", "知识派生单元的已知时间早于其依据", 409)
            units.append(
                clean(
                    {
                        "unit_id": row["id"],
                        "unit_source_id": row["source_id"],
                        "unit_kind": unit.unit_kind,
                        "knowledge_id": item["knowledge_id"],
                        "knowledge_version_id": item["version_id"],
                        "knowledge_source_id": item["knowledge_source_id"],
                        "index_id": ready["id"],
                        "text": unit.text,
                        "text_digest": unit.digest,
                        "span_start": unit.span_start,
                        "span_end": unit.span_end,
                        "offset_basis": unit.offset_basis,
                        "ref_ordinal": unit.ref_ordinal,
                        "token_count": unit.token_count,
                        "provenance": row["provenance"],
                        "authority_manifest": authority,
                    }
                )
            )
            if len(units) > MAX_UNITS:
                raise DomainError("KNOWLEDGE_SCOPE_TOO_LARGE", "请缩小知识条目范围后检索", 422)
    versions = [
        {
            key: value
            for key, value in item.items()
            if key not in ("evaluated_at", "project_context", "content")
        }
        for item in eligible
    ]
    return clean(
        {
            "schema_version": 1,
            "bid_id": bid,
            "purpose": purpose,
            "profile_id": profile_id,
            "dense_identity": dense_identity,
            "lexical_dictionary_digest": dictionary_digest,
            "policy_digest": POLICY_DIGEST,
            "knowledge_ids": sorted(wanted) if wanted is not None else None,
            "project_context": scope["project_context"],
            "versions": versions,
            "indexes": indexes,
            "units": units,
            "evaluated_at": at,
        }
    )


def assert_trace_scope_current(c, stored, bid, purpose):
    """Single guard: first/replayed search and first/replayed references all use it."""
    if (
        stored["bid_id"] != str(bid)
        or stored["purpose"] != purpose
        or stored["policy_digest"] != POLICY_DIGEST
    ):
        raise DomainError("KNOWLEDGE_SCOPE_CHANGED", "检索项目或策略已变化，请重新查证", 409)
    bridge.assert_project_context(c, bid, stored["project_context"])
    try:
        current = freeze_scope(
            c,
            bid,
            purpose,
            UUID(stored["profile_id"]),
            stored["dense_identity"],
            stored["lexical_dictionary_digest"],
            stored["knowledge_ids"],
        )
    except ValueError as error:
        raise DomainError(
            "KNOWLEDGE_SCOPE_CHANGED", "当前知识索引已变化，请重新查证", 409
        ) from error
    if scope_identity(current) != scope_identity(stored):
        raise DomainError(
            "KNOWLEDGE_SCOPE_CHANGED", "检索依据、索引或项目适用条件已变化，请重新查证", 409
        )
    for item in stored["versions"]:
        bridge.check_reference(
            c,
            bid,
            UUID(item["knowledge_id"]),
            UUID(item["version_id"]),
            purpose,
            expected_context=stored["project_context"],
        )
    return current


def read_candidates(c, scope, vector, lexemes):
    """Short readonly exact SQL. Query model computation has already finished."""
    allowed = [UUID(u["unit_id"]) for u in scope["units"]]
    if not allowed:
        return []
    vector = normalized_vector(list(vector))
    import json

    dense = c.execute(
        """SELECT u.id FROM retrieval.knowledge_units u JOIN retrieval.knowledge_unit_embeddings e ON e.unit_id=u.id AND e.index_id=u.index_id
        WHERE u.id=ANY(%s) AND e.profile_id=%s ORDER BY e.embedding OPERATOR(design.<=>) %s::design.vector,u.id LIMIT 50""",
        (allowed, UUID(scope["profile_id"]), json.dumps(vector)),
    ).fetchall()
    lexical = c.execute(
        """SELECT id FROM retrieval.knowledge_units WHERE id=ANY(%s) AND lex_tsv @@ plainto_tsquery('simple',%s)
        ORDER BY ts_rank_cd(lex_tsv,plainto_tsquery('simple',%s)) DESC,id LIMIT 50""",
        (allowed, lexemes, lexemes),
    ).fetchall()
    fused = fuse_rankings([[r["id"] for r in dense], [r["id"] for r in lexical]], 20)
    by_id = {u["unit_id"]: u for u in scope["units"]}
    return [by_id[str(unit_id)] for unit_id, _ in fused]


def rerank_candidates(query, candidates, reranker):
    """No DB argument/connection. Explicitly mark actual RRF fallback on failure."""
    route = ["dense_exact", "simple_fts", "rrf"]
    if not candidates:
        return [], route, None
    from packages.rag.http_port import RuntimeUnavailable

    try:
        scores = reranker.rank(query, [c["text"] for c in candidates])
        if len(scores) != len(candidates) or any(
            not isinstance(s, (int, float)) or not math.isfinite(s) for s in scores
        ):
            raise ValueError("KNOWLEDGE_RERANK_INVALID")
        identity = getattr(reranker, "last_identity", None)
        if not isinstance(identity, dict) or not identity:
            raise ValueError("KNOWLEDGE_RERANK_IDENTITY_MISSING")
        ranked = sorted(
            zip(candidates, scores, strict=True), key=lambda r: (-r[1], r[0]["unit_id"])
        )
        return [r[0] for r in ranked], route + ["rerank"], identity
    except (RuntimeUnavailable, ValueError):
        return candidates, route + ["reranker_unavailable"], None


def save_trace(c, scope, actor, query, selected, route, reranker_identity, limit):
    assert_trace_scope_current(c, scope, UUID(scope["bid_id"]), scope["purpose"])
    by_id = {u["unit_id"]: u for u in scope["units"]}
    if len({u["unit_id"] for u in selected}) != len(selected) or any(
        u != by_id.get(u["unit_id"]) for u in selected
    ):
        raise DomainError("KNOWLEDGE_SUPPLY_INVALID", "不能提供检索范围以外的内容", 409)
    selected = selected[:limit]
    trace_id = uuid4()
    c.execute(
        """INSERT INTO retrieval.knowledge_retrieval_traces(id,bid_id,actor_id,purpose,query_digest,profile_id,policy_digest,
        frozen_scope,scope_digest,selected_units,route,observed_reranker_identity) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            trace_id,
            UUID(scope["bid_id"]),
            actor,
            scope["purpose"],
            sha(query),
            UUID(scope["profile_id"]),
            POLICY_DIGEST,
            Jsonb(scope),
            sha(scope_identity(scope)),
            Jsonb(selected),
            Jsonb(route),
            Jsonb(reranker_identity) if reranker_identity else None,
        ),
    )
    return trace_result(trace_id, scope, selected, route)


def trace_result(trace_id, scope, selected, route):
    hits = [
        {
            key: value
            for key, value in u.items()
            if key not in ("authority_manifest", "text_digest", "token_count", "ref_ordinal")
        }
        | {"rank": rank, "reference_only": True, "project_fact_created": False}
        for rank, u in enumerate(selected, 1)
    ]
    gaps = ["仅检索已发布且适用的知识与批准摘录；知识参考不自动成为本项目事实"]
    if not scope["units"]:
        gaps.append("当前没有完整、有效且适用的知识索引")
    if "reranker_unavailable" in route:
        gaps.append("本次重排不可用，已明确使用向量与词法融合顺序")
    return {
        "trace_id": str(trace_id),
        "profile_id": scope["profile_id"],
        "policy_digest": POLICY_DIGEST,
        "coverage": "partial" if hits else "unavailable",
        "hits": hits,
        "gaps": gaps,
        "route": route,
    }


def trace_row(c, trace_id):
    assert_schema(c)
    row = fetch(c, "SELECT * FROM retrieval.knowledge_retrieval_traces WHERE id=%s", (trace_id,))
    if sha(scope_identity(row["frozen_scope"])) != row["scope_digest"]:
        raise DomainError("KNOWLEDGE_TRACE_INVALID", "历史检索记录身份不一致", 409)
    assert_trace_selection(row["frozen_scope"], row["selected_units"])
    return row


def assert_trace_selection(scope, selected):
    """Every read/use validates exact supplied fields against immutable frozen units."""
    units = scope.get("units")
    valid = isinstance(units, list) and isinstance(selected, list)
    if valid:
        valid = all(
            isinstance(unit, dict)
            and isinstance(unit.get("unit_id"), str)
            and isinstance(unit.get("text"), str)
            and unit.get("text_digest") == text_digest(unit["text"])
            for unit in units
        )
    if valid:
        allowed = {unit["unit_id"]: unit for unit in units}
        valid = (
            len(allowed) == len(units)
            and len(selected) <= 8
            and all(
                isinstance(unit, dict)
                and isinstance(unit.get("unit_id"), str)
                and unit == allowed.get(unit["unit_id"])
                for unit in selected
            )
        )
    if valid:
        valid = len({unit["unit_id"] for unit in selected}) == len(selected)
    if not valid:
        raise DomainError("KNOWLEDGE_TRACE_INVALID", "历史供给清单不属于当时批准范围", 409)


def trace_history(c, trace_id):
    row = trace_row(c, trace_id)
    try:
        assert_trace_scope_current(c, row["frozen_scope"], row["bid_id"], row["purpose"])
        reasons = []
    except DomainError as error:
        reasons = [error.code]
    return {
        "retrieval": trace_result(
            trace_id, row["frozen_scope"], row["selected_units"], row["route"]
        ),
        "evaluated_at": row["frozen_scope"]["evaluated_at"],
        "current_reasons": reasons,
        "historical_bytes_preserved": True,
    }


def cite_unit(c, trace_id, actor, body, command_id):
    from packages.domain.runtime import require_read_committed
    from packages.lifecycle.service import assert_work_writable

    require_read_committed(c)
    trace = trace_row(c, trace_id)
    assert_work_writable(c, trace["bid_id"])
    scope = trace["frozen_scope"]
    assert_trace_scope_current(c, scope, trace["bid_id"], trace["purpose"])
    selected = next((u for u in trace["selected_units"] if u["unit_id"] == body["unit_id"]), None)
    if selected is None or not body["quote"].strip() or body["quote"] not in selected["text"]:
        raise DomainError("KNOWLEDGE_SUPPLY_INVALID", "引用必须逐字属于这次实际提供的知识单元", 422)
    context = scope["project_context"]
    if context["archived_at"] is not None or context["phase"] not in ("working", "preparation"):
        raise DomainError("PROJECT_NOT_WRITABLE", "已提交或归档项目不能新增引用", 409)
    target = UUID(body["target_object_id"])
    permitted = {
        "project": "bids",
        "section": "sections",
        "operation": "operations",
        "review": "review_runs",
    }
    kind = body["target_kind"]
    if kind == "project":
        if target != trace["bid_id"]:
            raise DomainError("KNOWLEDGE_TARGET_INVALID", "引用目标不属于当前项目", 409)
    else:
        from psycopg import sql

        if kind not in permitted:
            raise DomainError("KNOWLEDGE_TARGET_INVALID", "引用目标类型不支持", 422)
        fetch(
            c,
            sql.SQL("SELECT id FROM {} WHERE id=%s AND bid_id=%s").format(
                sql.Identifier(permitted[kind])
            ),
            (target, trace["bid_id"]),
        )
    provenance = selected["provenance"]
    ref = (
        provenance["reference"]
        if selected["unit_kind"] == "original_quote"
        else provenance["approved_references"][0]
    )
    source = next(
        a for a in selected["authority_manifest"] if a["ancestor_id"] == selected["unit_source_id"]
    )
    citation = uuid4()
    c.execute(
        "INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            citation,
            trace["bid_id"],
            UUID(selected["unit_source_id"]),
            source["revision"],
            trace["purpose"],
            UUID(ref["evidence_id"]),
            target,
        ),
    )
    start = selected["text"].index(body["quote"])
    result = clean(
        {
            "citation_id": citation,
            "trace_id": trace_id,
            "bid_id": trace["bid_id"],
            "unit_id": selected["unit_id"],
            "unit_kind": selected["unit_kind"],
            "knowledge_version_id": selected["knowledge_version_id"],
            "quote": body["quote"],
            "unit_text": selected["text"],
            "unit_span_start": start,
            "unit_span_end": start + len(body["quote"]),
            "target_kind": kind,
            "target_object_id": target,
            "applicability_reason": body["applicability_reason"],
            "provenance": provenance,
            "project_context": context,
            "reference_only": True,
            "project_fact_created": False,
        }
    )
    c.execute(
        "INSERT INTO retrieval.knowledge_unit_references(citation_id,trace_id,bid_id,unit_id,knowledge_version_id,approved_quote,unit_span_start,unit_span_end,reference_manifest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            citation,
            trace_id,
            trace["bid_id"],
            UUID(selected["unit_id"]),
            UUID(selected["knowledge_version_id"]),
            body["quote"],
            start,
            start + len(body["quote"]),
            Jsonb(result),
        ),
    )
    audit(c, actor, trace["bid_id"], "knowledge.unit_cited", result, command_id)
    return result


def reference_history(c, citation_id):
    assert_schema(c)
    row = fetch(
        c, "SELECT * FROM retrieval.knowledge_unit_references WHERE citation_id=%s", (citation_id,)
    )
    trace = trace_row(c, row["trace_id"])
    try:
        assert_trace_scope_current(c, trace["frozen_scope"], row["bid_id"], trace["purpose"])
        reasons = []
    except DomainError as error:
        reasons = [error.code]
    return {
        "reference": row["reference_manifest"],
        "current_reasons": reasons,
        "historical_bytes_preserved": True,
    }
