"""Stable-node proposals, immutable before identity, final source check and revision append."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring.content import SCHEMA_DIGEST, validate_content
from packages.authoring.repository import (
    assert_sources,
    lock_draft,
    save_draft,
    source_authority_manifest,
)
from packages.domain.runtime import DomainError, fetch, now, sha, uid


def replace_node(content, node_id, before_digest, replacement):
    updated = deepcopy(content)
    matches = []

    def visit(node):
        for index, child in enumerate(node.get("content", [])):
            if child.get("attrs", {}).get("id") == node_id:
                matches.append((node, index, child))
            visit(child)

    visit(updated)
    if len(matches) != 1 or sha(matches[0][2]) != before_digest:
        raise DomainError("PATCH_RANGE_CHANGED", "建议对应选区已修改，请重新生成", 412)
    parent, index, _ = matches[0]
    if not isinstance(replacement.get("attrs"), dict) or replacement["attrs"].get("id") != node_id:
        raise DomainError("PATCH_NODE_ID_CHANGED", "建议必须保留选区节点身份", 422)
    parent["content"][index] = deepcopy(replacement)
    validate_content(updated)
    return updated


def create_proposal(
    c, bid, section, base_revision, node_id, before_digest, replacement, source_refs
):
    """Called only after a budgeted model result or explicit manual preview; no model I/O."""
    s = fetch(c, "SELECT * FROM sections WHERE id=%s AND bid_id=%s", (section, bid))
    draft = lock_draft(c, bid, s["draft_id"])
    from packages.authoring.snapshot_structure import assert_active

    assert_active(c, bid, s["draft_id"], section)
    s = fetch(c, "SELECT * FROM sections WHERE id=%s AND bid_id=%s FOR UPDATE", (section, bid))
    if s["head_revision"] != base_revision:
        raise DomainError("SECTION_REVISION_CONFLICT", "章节已更新，请重新预览", 412)
    original = fetch(
        c,
        "SELECT * FROM section_revisions WHERE section_id=%s AND revision=%s",
        (section, base_revision),
    )
    updated = replace_node(original["content"], node_id, before_digest, replacement)
    combined_refs = {
        sha(ref): ref for ref in original["content"]["attrs"]["source_refs"] + source_refs
    }
    updated["attrs"]["source_refs"] = list(combined_refs.values())
    assert_sources(c, bid, updated)
    identity = uid()
    context = proposal_context(c, bid)
    payload = {
        "context": context,
        "authority_manifest": source_authority_manifest(c, updated["attrs"]["source_refs"]),
        "kind": "authoring_patch",
        "section_id": str(section),
        "draft_id": str(s["draft_id"]),
        "base_section_revision": base_revision,
        "before_section_digest": original["digest"],
        "node_id": node_id,
        "before_digest": before_digest,
        "replacement": replacement,
        "source_refs": updated["attrs"]["source_refs"],
        "schema_digest": SCHEMA_DIGEST,
    }
    c.execute(
        "INSERT INTO proposed_actions(id,bid_id,base_revision,request_digest,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identity,
            bid,
            draft["revision"],
            sha(payload),
            now() + timedelta(minutes=30),
            Jsonb(payload),
        ),
    )
    return {
        "action_id": str(identity),
        "base_draft_revision": draft["revision"],
        "before": original["content"],
        "after": updated,
        "expires_in_seconds": 1800,
    }


def apply_proposal(c, bid, action, actor, command_id, condition):
    # Bid first, then proposed action, then source heads: same order as authoring save.
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    row = fetch(
        c, "SELECT * FROM proposed_actions WHERE id=%s AND bid_id=%s FOR UPDATE", (action, bid)
    )
    if row["expires_at"] <= now():
        raise DomainError("ACTION_EXPIRED", "建议已过期，请重新生成", 409)
    if c.execute("SELECT 1 FROM action_consumptions WHERE action_id=%s", (action,)).fetchone():
        raise DomainError("ACTION_CONSUMED", "该建议已有处理结果", 409)
    p = row["payload"]
    if p.get("kind") != "authoring_patch" or sha(p) != row["request_digest"]:
        raise DomainError("ACTION_KIND_INVALID", "建议类型或内容身份不正确", 409)
    from packages.assistant.actions import assert_linked_generation_context

    assert_linked_generation_context(c, bid, action)
    if proposal_context(c, bid) != p["context"]:
        raise DomainError("AUTHORING_CONTEXT_CHANGED", "项目要求或事实已更新，请重新生成建议", 409)
    original = fetch(
        c,
        "SELECT * FROM section_revisions WHERE section_id=%s AND revision=%s",
        (UUID(p["section_id"]), p["base_section_revision"]),
    )
    if original["digest"] != p["before_section_digest"]:
        raise DomainError("PATCH_SOURCE_CHANGED", "建议的基础章节校验失败", 412)
    updated = replace_node(original["content"], p["node_id"], p["before_digest"], p["replacement"])
    updated["attrs"]["source_refs"] = p["source_refs"]
    assert_sources(c, bid, updated)
    if source_authority_manifest(c, p["source_refs"]) != p["authority_manifest"]:
        raise DomainError(
            "AUTHORING_AUTHORITY_CHANGED", "来源版本或祖先效力已变化，请重新生成建议", 409
        )
    result = save_draft(
        c,
        bid,
        UUID(p["draft_id"]),
        actor,
        {
            "base_draft_revision": row["base_revision"],
            "changes": [
                {
                    "section_id": p["section_id"],
                    "base_section_revision": p["base_section_revision"],
                    "schema_digest": p["schema_digest"],
                    "content": updated,
                }
            ],
        },
        condition,
    )
    c.execute(
        "INSERT INTO action_consumptions(action_id,command_id,consumed_by,result) VALUES(%s,%s,%s,%s)",
        (action, command_id, actor, Jsonb(result)),
    )
    return result


def proposal_context(c, bid):
    row = fetch(c, "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid,))
    facts = c.execute(
        "SELECT fact_set_id,revision FROM fact_heads WHERE bid_id=%s", (bid,)
    ).fetchone()
    return {
        "rule_set_id": str(row["current_rule_set_id"]) if row["current_rule_set_id"] else None,
        "fact_set_id": str(facts["fact_set_id"]) if facts and facts["fact_set_id"] else None,
        "fact_revision": facts["revision"] if facts else 0,
    }
