"""Trusted, project-filtered knowledge scopes and exact original-evidence allowlists.

Knowledge summaries are not parsed original chunks. This bridge never grants a whole
cross-project original to RAG or treats a historical fact as a current project fact.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from packages.domain.runtime import DomainError, audit, clean, fetch, now, sha
from packages.knowledge.content import PURPOSES, text
from packages.knowledge.repository import lifecycle, version_row
from packages.knowledge.sources import authority_manifest, lock_graph, lock_heads


def project_context(c, bid):
    row = fetch(
        c,
        """SELECT p.bidder_id,e.region,e.engineering_type,p.phase,p.archived_at FROM bid_profiles p
      JOIN bid_rounds r ON r.id=p.round_id JOIN engineering_projects e ON e.id=r.engineering_project_id
      WHERE p.bid_id=%s""",
        (bid,),
    )
    facts = c.execute(
        """SELECT f.subject,f.scope,f.name,f.value,f.unit FROM fact_heads h
      JOIN fact_items f ON f.fact_set_id=h.fact_set_id AND f.bid_id=h.bid_id WHERE h.bid_id=%s""",
        (bid,),
    ).fetchall()
    return row, facts


def applicability_reasons(c, row, bid, purpose, at):
    if purpose not in PURPOSES:
        return ["PURPOSE_FORBIDDEN"]
    if row["published_version_id"] != row["id"] or lifecycle(c, row, at) != "published":
        return ["VERSION_NOT_EFFECTIVE"]
    rules = row["applicability"].get("rules")
    if not rules or rules["validity_kind"] == "unknown":
        return ["APPLICABILITY_UNKNOWN"]
    context, facts = project_context(c, bid)
    reasons = []
    if str(context["bidder_id"]) not in rules["bidder_ids"]:
        reasons.append("SUBJECT_MISMATCH")
    if rules["bid_ids"] and str(bid) not in rules["bid_ids"]:
        reasons.append("PROJECT_MISMATCH")
    if context["region"] is None or context["region"] not in rules["regions"]:
        reasons.append("REGION_UNKNOWN_OR_MISMATCH")
    if (
        context["engineering_type"] is None
        or context["engineering_type"] not in rules["engineering_types"]
    ):
        reasons.append("ENGINEERING_TYPE_UNKNOWN_OR_MISMATCH")
    if purpose not in rules["purposes"]:
        reasons.append("PURPOSE_FORBIDDEN")
    if purpose == "compliance" and (
        "standard" not in row["categories"]
        or any(
            a["policy_class"] != "prebid_content" for a in row["applicability"]["publication_basis"]
        )
    ):
        reasons.append("REFERENCE_IS_NOT_CURRENT_REGULATORY_AUTHORITY")
    for required in rules["conditions"]:
        matching = [
            f
            for f in facts
            if all(f[k] == required[k] for k in ["subject", "scope", "name", "unit"])
        ]
        if not matching or any(
            f["value"] != required["value"] or type(f["value"]) is not type(required["value"])
            for f in matching
        ):
            reasons.append("PROJECT_FACT_UNKNOWN_OR_CONFLICT")
    return list(dict.fromkeys(reasons))


def eligible_knowledge_sources(c, bid, purpose, as_of, explicit_version_ids=None, *, worker=False):
    """Trusted server-only input; not a public client-supplied source allowlist."""
    if purpose not in PURPOSES:
        raise DomainError("KNOWLEDGE_PURPOSE_FORBIDDEN", "知识检索不能扩展正式评分输入白名单", 422)
    if worker:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
    else:
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (bid,))
    lock_graph(c)
    items = c.execute(
        "SELECT id,published_version_id FROM retrieval.knowledge_items WHERE published_version_id IS NOT NULL ORDER BY id"
    ).fetchall()
    wanted = set(explicit_version_ids) if explicit_version_ids is not None else None
    accepted = []
    excluded = []
    for item in items:
        if wanted is not None and item["published_version_id"] not in wanted:
            continue
        row = version_row(c, item["id"], item["published_version_id"])
        lock_heads(c, [row["source_id"]])
        reasons = applicability_reasons(c, row, bid, purpose, as_of)
        if reasons:
            excluded.append({"version_id": str(row["id"]), "reasons": reasons})
            continue
        authority = authority_manifest(c, [row["source_id"]], as_of)
        accepted.append(
            clean(
                {
                    "knowledge_id": row["item_id"],
                    "version_id": row["id"],
                    "knowledge_source_id": row["source_id"],
                    "source_revision": row["source_revision"],
                    "content_digest": sha(row["content"]),
                    "content": row["content"],
                    "scope": row["applicability"]["scope"],
                    "applicability": row["applicability"]["rules"],
                    "source_manifest": authority,
                    "original_evidence_allowlist": row["applicability"]["source_refs"],
                    "reference_only": True,
                }
            )
        )
    return {
        "bid_id": str(bid),
        "purpose": purpose,
        "eligible": accepted,
        "excluded": excluded,
        "knowledge_source_ids": [a["knowledge_source_id"] for a in accepted],
        "formal_score_allowed": False,
    }


def check_reference(c, bid, knowledge_id, version, purpose, *, worker=False):
    selected = eligible_knowledge_sources(c, bid, purpose, now(), [version], worker=worker)
    eligible = next(
        (
            v
            for v in selected["eligible"]
            if v["knowledge_id"] == str(knowledge_id) and v["version_id"] == str(version)
        ),
        None,
    )
    if eligible is None:
        raise DomainError(
            "KNOWLEDGE_NOT_APPLICABLE",
            "该知识版本已失效、被替代或不适用于此项目，请重新核对来源和条件",
            409,
            {"excluded": selected["excluded"]},
        )
    return eligible


def cite(c, bid, knowledge_id, version, actor, body, command_id):
    permitted = {
        "project": "bids",
        "section": "sections",
        "operation": "operations",
        "review": "review_runs",
    }
    kind = body["target_kind"]
    target = UUID(body["target_object_id"])
    if kind not in permitted:
        raise DomainError("KNOWLEDGE_TARGET_INVALID", "引用目标类型不支持", 422)
    selected = check_reference(c, bid, knowledge_id, version, body["purpose"])
    context, _ = project_context(c, bid)
    if context["archived_at"] is not None or context["phase"] not in ["preparation", "working"]:
        raise DomainError(
            "PROJECT_NOT_WRITABLE", "已提交或归档项目不能新增选用引用，历史引用保持可读", 409
        )
    if kind == "project":
        if target != bid:
            raise DomainError("KNOWLEDGE_TARGET_INVALID", "引用目标不属于当前项目", 409)
    else:
        # Identifier is selected only from the server-side constant map.
        from psycopg import sql

        fetch(
            c,
            sql.SQL("SELECT id FROM {} WHERE id=%s AND bid_id=%s").format(
                sql.Identifier(permitted[kind])
            ),
            (target, bid),
        )
    text(body["excerpt"], 20000)
    text(body["applicability_reason"], 4000)
    if body["excerpt"] not in selected["content"]:
        raise DomainError("KNOWLEDGE_EXCERPT_INVALID", "引用摘录不属于这个知识版本", 422)
    citations = []
    for ref in selected["original_evidence_allowlist"]:
        ident = uuid4()
        c.execute(
            """INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id)
          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (
                ident,
                bid,
                UUID(selected["knowledge_source_id"]),
                selected["source_revision"],
                body["purpose"],
                UUID(ref["evidence_id"]),
                target,
            ),
        )
        citations.append(str(ident))
    result = {
        "knowledge_id": str(knowledge_id),
        "version_id": str(version),
        "bid_id": str(bid),
        "citation_ids": citations,
        "excerpt": body["excerpt"],
        "purpose": body["purpose"],
        "target_kind": kind,
        "target_object_id": str(target),
        "applicability_reason": body["applicability_reason"],
        "source_manifest": selected["source_manifest"],
        "source_refs": selected["original_evidence_allowlist"],
        "reference_only": True,
        "project_fact_created": False,
    }
    audit(c, actor, bid, "knowledge.cited", result, command_id)
    return result


def reference_history(c, citation_id):
    citation = fetch(c, "SELECT * FROM retrieval.citations WHERE id=%s", (citation_id,))
    knowledge = fetch(
        c,
        "SELECT item_id,id FROM retrieval.knowledge_versions WHERE source_id=%s",
        (citation["source_id"],),
    )
    event = fetch(
        c,
        "SELECT payload FROM audit_entries WHERE event_type='knowledge.cited' AND payload->'citation_ids' ? %s ORDER BY created_at,id LIMIT 1",
        (str(citation_id),),
    )
    row = version_row(c, knowledge["item_id"], knowledge["id"])
    return clean(
        {
            "reference": event["payload"],
            "version_content": row["content"],
            "current_applicability_reasons": applicability_reasons(
                c, row, citation["bid_id"], citation["purpose"], now()
            ),
            "historical_bytes_preserved": True,
        }
    )


def impacts(c, knowledge_id, version=None):
    row = version_row(c, knowledge_id, version)
    records = c.execute(
        """SELECT ci.id AS citation_id,ci.bid_id,ci.target_object_id,ci.purpose,ci.created_at,
       p.phase,p.archived_at,b.title FROM retrieval.source_ancestors a
       JOIN retrieval.citations ci ON ci.source_id=a.source_id JOIN bids b ON b.id=ci.bid_id
       JOIN bid_profiles p ON p.bid_id=ci.bid_id WHERE a.ancestor_id=%s ORDER BY ci.created_at,ci.id""",
        (row["source_id"],),
    ).fetchall()
    derived = c.execute(
        """SELECT n.id,n.kind,n.digest FROM retrieval.source_ancestors a
       JOIN retrieval.source_nodes n ON n.id=a.source_id
       WHERE a.ancestor_id=%s AND n.kind IN ('feature','label','model','summary') ORDER BY n.id""",
        (row["source_id"],),
    ).fetchall()
    groups: dict[str, list[dict]] = {"working": [], "submitted": [], "archived": []}
    for record in records:
        group = (
            "archived"
            if record["archived_at"]
            else "submitted"
            if record["phase"] == "submitted"
            else "working"
        )
        groups[group].append(clean(record))
    return {
        "knowledge_id": str(knowledge_id),
        "version_id": str(row["id"]),
        "workflow": lifecycle(c, row, now()),
        "groups": groups,
        "registered_research_model_derivations": clean(derived),
        "requires_review": bool(records or derived),
        "unknown_ranges": ["未登记来源谱系的实验或模型不能由引用图自动枚举"],
        "historical_reports_rewritten": False,
    }
