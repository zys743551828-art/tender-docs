"""Controlled authoring model ports. The caller owns Kafka, model budget and call ledger.

These functions perform no provider calls, retries or paid network requests. A model may
propose a stable-node replacement; only a later human-confirmed transaction may apply it.
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring.patches import create_proposal, proposal_context
from packages.authoring.repository import assert_sources, lock_draft, source_authority_manifest
from packages.domain.runtime import DomainError, clean, fetch, sha, uid


def freeze_generation_plan(c, bid, body):
    section_id = UUID(body["section_id"])
    original_section = fetch(
        c, "SELECT * FROM sections WHERE id=%s AND bid_id=%s", (section_id, bid)
    )
    draft = lock_draft(c, bid, original_section["draft_id"])
    section = fetch(
        c,
        "SELECT * FROM section_revisions WHERE section_id=%s AND revision=(SELECT head_revision FROM sections WHERE id=%s)",
        (section_id, section_id),
    )
    if section["revision"] != body["base_section_revision"]:
        raise DomainError("SECTION_REVISION_CONFLICT", "章节已更新，请重新生成建议", 412)
    if (
        not isinstance(body["instruction"], str)
        or not 1 <= len(body["instruction"].strip()) <= 8000
    ):
        raise DomainError("INVALID_REQUEST", "请输入不超过8000字的编制要求", 422)
    versions = [UUID(v) for v in body["source_version_ids"]]
    if len(versions) > 30 or len(versions) != len(set(versions)):
        raise DomainError("AUTHORING_SOURCE_LIMIT", "一次最多选择30个不重复资料版本", 422)
    assert_sources(c, bid, section["content"])
    blocks: list[dict[str, Any]] = []
    for version in sorted(versions, key=str):
        rows = c.execute(
            """SELECT e.id,e.text_content,e.physical_page,n.id AS source_id,h.revision
          FROM parse_runs p JOIN evidence_blocks e ON e.parse_id=p.id
          JOIN retrieval.source_nodes n ON n.document_version_id=p.document_version_id AND n.kind='document'
          JOIN retrieval.source_heads h ON h.source_id=n.id
          WHERE p.document_version_id=%s AND p.bid_id=%s AND h.status='published'
            AND p.id=(SELECT q.id FROM parse_runs q WHERE q.document_version_id=p.document_version_id ORDER BY q.created_at DESC,q.id DESC LIMIT 1)
          ORDER BY e.physical_page,e.ordinal,n.id LIMIT 501""",
            (version, bid),
        ).fetchall()
        if not rows or len(rows) > 500:
            raise DomainError(
                "AUTHORING_SOURCE_NOT_READY",
                "所选来源缺少可核验证据或超过单次编制范围，请缩小来源",
                409,
            )
        checked = clean(section["content"])
        checked["attrs"]["source_refs"] = [
            {
                "source_id": str(r["source_id"]),
                "source_revision": r["revision"],
                "evidence_id": str(r["id"]),
                "document_version_id": str(version),
            }
            for r in rows
        ]
        assert_sources(c, bid, checked)
        blocks.extend(
            {
                "evidence_id": str(r["id"]),
                "physical_page": r["physical_page"],
                "text": r["text_content"],
                "source_ref": ref,
            }
            for r, ref in zip(rows, checked["attrs"]["source_refs"], strict=True)
        )
    if sum(len(b["text"]) for b in blocks) > 100_000:
        raise DomainError(
            "AUTHORING_SOURCE_LIMIT", "选中来源超过单次编制范围，请缩小来源；系统未静默截断", 422
        )
    context = proposal_context(c, bid)
    identity = uid()
    plan = {
        "purpose": "authoring",
        "authority_manifest": source_authority_manifest(
            c, section["content"]["attrs"]["source_refs"] + [b["source_ref"] for b in blocks]
        ),
        "base_draft_revision": draft["revision"],
        "section_digest": section["digest"],
        "context": context,
        "instruction": body["instruction"],
        "original_content": section["content"],
        "source_blocks": blocks,
        "output_policy": "propose_only_stable_node_replacement_no_mutation",
    }
    c.execute(
        "INSERT INTO writing_plans(id,bid_id,section_id,base_section_revision,rule_ids,fact_set_id,source_version_ids,plan) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            bid,
            section_id,
            section["revision"],
            [UUID(r) for r in section["content"]["attrs"]["rule_ids"]],
            UUID(context["fact_set_id"]) if context["fact_set_id"] else None,
            versions,
            Jsonb(plan),
        ),
    )
    return {"writing_plan_id": str(identity), "input_digest": sha(plan), "plan": clean(plan)}


def accept_generated_proposal(c, bid, writing_plan_id, generated):
    """After a successfully ledgered structured call; never applies generated text."""
    plan = fetch(c, "SELECT * FROM writing_plans WHERE id=%s AND bid_id=%s", (writing_plan_id, bid))
    section = fetch(
        c, "SELECT * FROM sections WHERE id=%s AND bid_id=%s", (plan["section_id"], bid)
    )
    draft = lock_draft(c, bid, section["draft_id"])
    if (
        draft["revision"] != plan["plan"]["base_draft_revision"]
        or proposal_context(c, bid) != plan["plan"]["context"]
    ):
        raise DomainError(
            "AUTHORING_CONTEXT_CHANGED", "生成期间草稿、要求或事实已变更，请核对后重新生成", 409
        )
    if (
        not isinstance(generated, dict)
        or set(generated) != {"node_id", "before_digest", "replacement", "evidence_ids"}
        or not isinstance(generated["evidence_ids"], list)
    ):
        raise DomainError("MODEL_OUTPUT_INVALID", "模型编制建议格式无效，未应用正文", 422)
    checked = clean(plan["plan"]["original_content"])
    checked["attrs"]["source_refs"] = list(
        {
            sha(r): r
            for r in checked["attrs"]["source_refs"]
            + [b["source_ref"] for b in plan["plan"]["source_blocks"]]
        }.values()
    )
    assert_sources(c, bid, checked)
    if (
        source_authority_manifest(c, checked["attrs"]["source_refs"])
        != plan["plan"]["authority_manifest"]
    ):
        raise DomainError(
            "AUTHORING_AUTHORITY_CHANGED", "生成期间来源或祖先效力已变更，请重新生成", 409
        )
    allowed = {b["evidence_id"]: b["source_ref"] for b in plan["plan"]["source_blocks"]}
    if any(not isinstance(e, str) or e not in allowed for e in generated["evidence_ids"]):
        raise DomainError("MODEL_CITATION_INVALID", "建议包含未提供的引用，未应用正文", 422)
    if not isinstance(generated["replacement"], dict):
        raise DomainError("MODEL_OUTPUT_INVALID", "模型建议节点无效，未应用正文", 422)
    return create_proposal(
        c,
        bid,
        plan["section_id"],
        plan["base_section_revision"],
        generated["node_id"],
        generated["before_digest"],
        generated["replacement"],
        [allowed[e] for e in dict.fromkeys(generated["evidence_ids"])],
    )
