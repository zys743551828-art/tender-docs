"""API-only stable tree/plan commands; append history and consume reviewed changes once."""

import json
from copy import deepcopy
from datetime import timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring import format_bindings, snapshot_structure, templates
from packages.authoring.content import empty_content
from packages.authoring.repository import (
    append_section,
    assert_sources,
    freeze,
    latest_sections,
    lock_draft,
    source_authority_manifest,
)
from packages.authoring.structure_model import mutate, ordered
from packages.domain.runtime import (
    DomainError,
    clean,
    fetch,
    match,
    now,
    require_read_committed,
    sha,
    uid,
)
from packages.identity.repository import snapshot as identity_snapshot


def text_content(value):
    return value.get("text", "") + "".join(text_content(v) for v in value.get("content", []))


def load(c, bid, draft):
    templates.require_schema(c)
    fetch(c, "SELECT id FROM drafts WHERE id=%s AND bid_id=%s", (draft, bid))
    rows = latest_sections(c, bid, draft, include_deleted=True)
    state = snapshot_structure.reconcile(snapshot_structure.latest(c, bid, draft), rows)
    contents = {}
    for item in state["nodes"]:
        row = fetch(
            c,
            "SELECT content FROM section_revisions WHERE bid_id=%s AND draft_id=%s AND section_id=%s AND revision=%s",
            (bid, draft, UUID(item["section_id"]), item["section_revision"]),
        )
        contents[item["section_id"]] = row["content"]
    return state, contents


def context(c, bid):
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    decisions = c.execute(
        "SELECT object_id,revision FROM decision_heads WHERE bid_id=%s ORDER BY object_id", (bid,)
    ).fetchall()
    facts = c.execute("SELECT * FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
    return clean(
        {
            "profile": profile,
            "identity": identity_snapshot(c, bid),
            "fact_head": facts,
            "decision_heads": decisions,
        }
    )


def validate_use(c, bid, state, contents):
    refs = []
    for item in ordered(state["nodes"]):
        content = contents[item["section_id"]]
        assert_sources(c, bid, content)
        refs.extend(content["attrs"]["source_refs"])
        if item["plan_version_id"]:
            plan = snapshot_structure.plan(c, UUID(item["plan_version_id"]))
            from packages.authoring.planning import assert_budget

            assert_budget(c, bid, plan)
            check = empty_content("计划依据", 0)
            check["attrs"]["source_refs"] = plan["payload"]["source_refs"]
            check["attrs"]["rule_ids"] = plan["payload"]["rule_ids"]
            assert_sources(c, bid, check)
            refs.extend(check["attrs"]["source_refs"])
    style = templates.view(c, UUID(state["style_template_version_id"]), "layout")
    report = format_bindings.evaluate(
        c, bid, style["config"], state["metadata"].get("format_bindings", [])
    )
    for binding in state["metadata"].get("format_bindings", []):
        refs.extend(binding["source_refs"])
    return {"sources": source_authority_manifest(c, refs), "format": report}


def view(c, bid, draft):
    state, contents = load(c, bid, draft)
    head = fetch(c, "SELECT revision FROM drafts WHERE id=%s AND bid_id=%s", (draft, bid))
    nodes, pages, missing = [], 0, []
    for item in state["nodes"]:
        plan = (
            snapshot_structure.plan(c, UUID(item["plan_version_id"]))
            if item["plan_version_id"]
            else None
        )
        content = contents[item["section_id"]]
        nodes.append(
            item
            | {
                "title": content["attrs"]["title"],
                "rule_ids": content["attrs"]["rule_ids"],
                "source_refs": content["attrs"]["source_refs"],
                "plan": plan,
                "plan_content_basis_state": "none"
                if plan is None
                else (
                    "current"
                    if plan["base_section_revision"] == item["section_revision"]
                    else "section_changed"
                ),
                "actual_page_range": None,
            }
        )
        if not item["deleted"]:
            if plan and plan["planned_pages"] is not None:
                pages += plan["planned_pages"]
            else:
                missing.append(item["section_id"])
    return clean(
        {
            "draft_id": draft,
            "draft_revision": head["revision"],
            "snapshot_id": fetch(
                c,
                "SELECT id FROM draft_snapshots WHERE draft_id=%s AND revision=%s",
                (draft, head["revision"]),
            )["id"],
            "structure_version_id": (snapshot_structure.latest(c, bid, draft) or {}).get("id"),
            "nodes": nodes,
            "directory_template_version_id": state["directory_template_version_id"],
            "style_template_version_id": state["style_template_version_id"],
            "metadata": state["metadata"],
            "page_budget": {
                "planned_total": pages,
                "unknown_section_ids": missing,
                "actual_total": None,
                "actual_ranges_state": "unknown",
            },
        }
    )


def preview(c, bid, draft, actor, body, condition, *, prospective=None):
    row = lock_draft(c, bid, draft)
    match(condition, "draft", draft, row["revision"])
    if body["base_draft_revision"] != row["revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草稿已更新，请重新预览", 412)
    templates.ensure_defaults(c, actor)
    state, contents = load(c, bid, draft)
    if prospective is None:
        result = mutate(state["nodes"], contents, body["changes"])
        after = deepcopy(state) | {"nodes": result["nodes"]}
        texts = result["contents"]
        created = result["created_ids"]
    else:
        after, texts, created = prospective
    authority = validate_use(c, bid, after, texts)
    payload = clean(
        {
            "kind": "authoring_structure",
            "draft_id": draft,
            "before_digest": sha({"state": state, "contents": contents}),
            "context": context(c, bid),
            "after": after,
            "contents": texts,
            "authority": authority,
            "created_ids": created,
        }
    )
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 16 * 1024 * 1024:
        raise DomainError("AUTHORING_STRUCTURE_TOO_LARGE", "结构预览完整内容超过16MiB，未截断", 422)
    deleted = [
        {
            "section_id": n["section_id"],
            "title": contents[n["section_id"]]["attrs"]["title"],
            "text_characters": len(text_content(contents[n["section_id"]])),
            "rule_ids": contents[n["section_id"]]["attrs"]["rule_ids"],
            "source_count": len(contents[n["section_id"]]["attrs"]["source_refs"]),
            "plan_version_id": n["plan_version_id"],
        }
        for n in after["nodes"]
        if n["deleted"]
        and any(
            old["section_id"] == n["section_id"] and not old["deleted"] for old in state["nodes"]
        )
    ]
    action = uid()
    c.execute(
        "INSERT INTO proposed_actions(id,bid_id,base_revision,request_digest,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s)",
        (action, bid, row["revision"], sha(payload), now() + timedelta(minutes=30), Jsonb(payload)),
    )
    return {
        "action_id": str(action),
        "base_draft_revision": row["revision"],
        "created_ids": created,
        "plan_suggestions": after.get("template_plan_suggestions", {}),
        "before": state["nodes"],
        "after": after["nodes"],
        "before_sections": contents,
        "after_sections": texts,
        "directory_template_version_id": after["directory_template_version_id"],
        "style_template_version_id": after["style_template_version_id"],
        "deletion_impact": deleted,
        "format_report": authority["format"],
        "expires_in_seconds": 1800,
    }


def commit(c, bid, draft, actor, state, contents, revision):
    current = {
        str(r["section_id"]): r for r in latest_sections(c, bid, draft, include_deleted=True)
    }
    ordinals = {n["section_id"]: i for i, n in enumerate(ordered(state["nodes"]))}
    for item in state["nodes"]:
        identity = item["section_id"]
        content = deepcopy(contents[identity])
        if identity in ordinals:
            content["attrs"]["ordinal"] = ordinals[identity]
        old = current.get(identity)
        if old is None:
            c.execute(
                "INSERT INTO sections(id,bid_id,draft_id,head_revision) VALUES(%s,%s,%s,1)",
                (UUID(identity), bid, draft),
            )
            append_section(c, bid, draft, UUID(identity), 1, content, actor)
            item["section_revision"] = 1
        elif sha(content) != old["digest"]:
            append_section(c, bid, draft, UUID(identity), old["revision"] + 1, content, actor)
            item["section_revision"] = old["revision"] + 1
        else:
            item["section_revision"] = old["revision"]
    from packages.authoring.planning import insert_template_plan

    for identity, suggestion in state.pop("template_plan_suggestions", {}).items():
        item = next(n for n in state["nodes"] if n["section_id"] == identity and not n["deleted"])
        if item["plan_version_id"] is not None:
            raise DomainError("AUTHORING_PLAN_CONFLICT", "目录建议不能覆盖已有人工计划", 409)
        directory = templates.view(c, UUID(suggestion["template_version_id"]), "outline")
        specification = next(
            n for n in directory["config"]["nodes"] if n["key"] == suggestion["template_key"]
        )
        item["plan_version_id"] = insert_template_plan(
            c, bid, draft, UUID(identity), item["section_revision"], actor, directory, specification
        )
    c.execute("UPDATE drafts SET revision=revision+1 WHERE id=%s", (draft,))
    return freeze(c, bid, draft, revision + 1, actor=actor, structure_state=state)


def resolve(c, bid, action, actor, command_id, decision, condition):
    require_read_committed(c)
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    row = fetch(
        c, "SELECT * FROM proposed_actions WHERE id=%s AND bid_id=%s FOR UPDATE", (action, bid)
    )
    p = row["payload"]
    if p.get("kind") != "authoring_structure" or sha(p) != row["request_digest"]:
        raise DomainError("ACTION_KIND_INVALID", "结构候选身份不匹配", 409)
    receipt = c.execute(
        "SELECT result FROM action_consumptions WHERE action_id=%s", (action,)
    ).fetchone()
    if receipt:
        return receipt["result"]
    if decision == "reject":
        result = {"action_id": str(action), "state": "rejected"}
    else:
        if row["expires_at"] <= now():
            raise DomainError("ACTION_EXPIRED", "结构候选已过期，请重新预览", 409)
        draft = UUID(p["draft_id"])
        head = lock_draft(c, bid, draft)
        match(condition, "draft", draft, head["revision"])
        state, contents = load(c, bid, draft)
        if (
            head["revision"] != row["base_revision"]
            or sha({"state": state, "contents": contents}) != p["before_digest"]
        ):
            raise DomainError("DRAFT_REVISION_CONFLICT", "当前正文或结构已变化，请重新预览", 412)
        if (
            context(c, bid) != p["context"]
            or validate_use(c, bid, p["after"], p["contents"]) != p["authority"]
        ):
            raise DomainError(
                "AUTHORING_CONTEXT_CHANGED", "当前事实、要求或来源效力已变化，请重新预览", 409
            )
        result = commit(c, bid, draft, actor, p["after"], p["contents"], head["revision"]) | {
            "action_id": str(action),
            "state": "applied",
        }
    c.execute(
        "INSERT INTO action_consumptions(action_id,command_id,consumed_by,result) VALUES(%s,%s,%s,%s)",
        (action, command_id, actor, Jsonb(result)),
    )
    return clean(result)


def save_plan(c, bid, draft, section, actor, body, condition):
    head = lock_draft(c, bid, draft)
    templates.ensure_defaults(c, actor)
    match(condition, "draft", draft, head["revision"])
    if head["revision"] != body["base_draft_revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草稿版本已变化", 412)
    state, contents = load(c, bid, draft)
    item = next(
        (n for n in state["nodes"] if n["section_id"] == str(section) and not n["deleted"]), None
    )
    if item is None:
        raise DomainError("AUTHORING_SECTION_DELETED", "计划目标不是当前活动章节", 409)
    previous = (
        snapshot_structure.plan(c, UUID(item["plan_version_id"]))
        if item["plan_version_id"]
        else None
    )
    if body["base_plan_revision"] != (previous["revision"] if previous else 0):
        raise DomainError("AUTHORING_PLAN_CONFLICT", "计划版本已变化", 412)
    check = empty_content("计划依据", 0)
    check["attrs"]["rule_ids"], check["attrs"]["source_refs"] = (
        body["rule_ids"],
        body["source_refs"],
    )
    assert_sources(c, bid, check)
    from packages.authoring.planning import requirement_basis

    proof = requirement_basis(c, bid, body)
    revision = c.execute(
        "SELECT coalesce(max(revision),0)+1 AS n FROM authoring_plan_versions WHERE section_id=%s",
        (section,),
    ).fetchone()["n"]
    value = {
        "id": uid(),
        "bid_id": bid,
        "draft_id": draft,
        "section_id": section,
        "revision": revision,
        "base_section_revision": item["section_revision"],
        "state": body["state"],
        "planned_pages": body["planned_pages"],
        "payload": {
            k: v
            for k, v in body.items()
            if k not in {"base_draft_revision", "base_plan_revision", "state", "planned_pages"}
        },
    }
    value["payload"]["budget_proof"] = proof
    if body["budget_basis"] == "template":
        directory = templates.view(c, UUID(state["directory_template_version_id"]), "outline")
        specification = next(
            (n for n in directory["config"]["nodes"] if n["key"] == item["template_key"]), None
        )
        if (
            item["template_family_id"] != directory["family_id"]
            or specification is None
            or specification.get("planned_pages") != body["planned_pages"]
            or body["planned_pages"] is None
        ):
            raise DomainError(
                "AUTHORING_BUDGET_BASIS_REQUIRED", "模板页预算须与当前匹配目录项的明确页数一致", 422
            )
        value["payload"]["template_budget_basis"] = {
            "template_version_id": directory["id"],
            "template_key": item["template_key"],
            "planned_pages": body["planned_pages"],
        }
    value["digest"] = snapshot_structure.plan_digest(value)
    c.execute(
        "INSERT INTO authoring_plan_versions(id,bid_id,draft_id,section_id,revision,base_section_revision,state,planned_pages,payload,digest,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            value["id"],
            bid,
            draft,
            section,
            revision,
            item["section_revision"],
            value["state"],
            value["planned_pages"],
            Jsonb(value["payload"]),
            value["digest"],
            actor,
        ),
    )
    item["plan_version_id"] = str(value["id"])
    c.execute("UPDATE drafts SET revision=revision+1 WHERE id=%s", (draft,))
    return freeze(c, bid, draft, head["revision"] + 1, actor=actor, structure_state=state) | {
        "plan": snapshot_structure.plan(c, value["id"])
    }


def directory_preview(c, bid, draft, actor, body, condition):
    from packages.authoring.structure_model import node, validate_tree

    # Acquire bid/draft before building the prospective mapping.
    lock_draft(c, bid, draft)
    state, contents = load(c, bid, draft)
    target = templates.view(c, UUID(body["directory_template_version_id"]), "outline")
    original = templates.view(c, UUID(state["directory_template_version_id"]), "outline")
    after = deepcopy(state)
    if body["delete_section_ids"]:
        removed = mutate(
            after["nodes"],
            contents,
            [{"op": "delete", "section_id": identity} for identity in body["delete_section_ids"]],
        )
        after["nodes"], contents = removed["nodes"], removed["contents"]
    active = {n["section_id"]: n for n in after["nodes"] if not n["deleted"]}
    keys = {n["key"] for n in target["config"]["nodes"]}
    explicit: dict[str, str] = {}
    for match_row in body["matches"]:
        key, identity = match_row["template_key"], match_row["section_id"]
        if (
            key not in keys
            or identity not in active
            or key in explicit
            or identity in explicit.values()
        ):
            raise DomainError(
                "AUTHORING_DIRECTORY_MATCH_INVALID", "目录匹配须唯一且目标为当前活动章节", 422
            )
        explicit[key] = identity
    existing = {
        n["template_key"]: n["section_id"]
        for n in active.values()
        if n["template_family_id"] == target["family_id"]
    }
    mapping = {key: explicit.get(key, existing.get(key, str(uid()))) for key in keys}
    if len(set(mapping.values())) != len(mapping):
        raise DomainError("AUTHORING_DIRECTORY_MATCH_INVALID", "两个目录项不能指向同一章节", 422)
    before_titles = (
        {n["key"]: n["title"] for n in original["config"]["nodes"]}
        if original["family_id"] == target["family_id"]
        else {}
    )
    created = {}
    priorities = {}
    for item in target["config"]["nodes"]:
        identity = mapping[item["key"]]
        current = active.get(identity)
        if current is None:
            current = node(identity)
            after["nodes"].append(current)
            contents[identity] = empty_content(item["title"], 0)
            created[item["key"]] = identity
        elif contents[identity]["attrs"]["title"] == before_titles.get(item["key"]):
            contents[identity]["attrs"]["title"] = item["title"]
        current.update(
            parent_id=mapping[item["parent_key"]] if item["parent_key"] else None,
            node_type=item["node_type"],
            template_family_id=target["family_id"],
            template_key=item["key"],
        )
        priorities[identity] = item["ordinal"]
        if current["plan_version_id"] is None and (
            item.get("goal") or item.get("planned_pages") is not None
        ):
            after.setdefault("template_plan_suggestions", {})[identity] = {
                "template_version_id": target["id"],
                "template_key": item["key"],
                "goal": item.get("goal", ""),
                "planned_pages": item.get("planned_pages"),
            }
    groups: dict[str | None, list] = {}
    for n in after["nodes"]:
        if not n["deleted"]:
            groups.setdefault(n["parent_id"], []).append(n)
            if (
                n["section_id"] not in mapping.values()
                and n["template_family_id"] == target["family_id"]
            ):
                n["template_family_id"], n["template_key"] = None, None
    for siblings in groups.values():
        siblings.sort(
            key=lambda n: (
                n["section_id"] not in priorities,
                priorities.get(n["section_id"], n["ordinal"]),
                n["section_id"],
            )
        )
        for i, n in enumerate(siblings):
            n["ordinal"] = i
    validate_tree(after["nodes"])
    after["nodes"] = ordered(after["nodes"], include_deleted=True)
    after["directory_template_version_id"] = target["id"]
    return preview(c, bid, draft, actor, body, condition, prospective=(after, contents, created))


def style_preview(c, bid, draft, actor, body, condition):
    head = lock_draft(c, bid, draft)
    templates.ensure_defaults(c, actor)
    match(condition, "draft", draft, head["revision"])
    if body["base_draft_revision"] != head["revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草稿已更新，请重新预览", 412)
    state, contents = load(c, bid, draft)
    style = templates.view(c, UUID(body["style_template_version_id"]), "layout")
    report = format_bindings.evaluate(c, bid, style["config"], body["format_bindings"])
    if report["requires_ack"] and body["format_review_ack"] != report["review_digest"]:
        return {"action_id": None, "state": "requires_format_review", "format_report": report}
    state["style_template_version_id"] = style["id"]
    state["metadata"].update(
        format_bindings=body["format_bindings"],
        format_binding_manifest=report["binding_manifest"],
        format_review_ack=body["format_review_ack"],
        effective_style=report["effective_config"],
    )
    return preview(c, bid, draft, actor, body, condition, prospective=(state, contents, {}))


def restore_preview(c, bid, draft, actor, body, condition):
    lock_draft(c, bid, draft)
    current, contents = load(c, bid, draft)
    saved = fetch(
        c,
        "SELECT s.manifest FROM authoring_structure_versions v JOIN draft_snapshots s ON s.id=v.snapshot_id WHERE v.id=%s AND v.bid_id=%s AND v.draft_id=%s",
        (UUID(body["structure_version_id"]), bid, draft),
    )
    old = snapshot_structure.snapshot_extension(saved["manifest"])
    desired = snapshot_structure.reconcile(old, [])
    # Restore old pinned contents. Later chapters remain as recoverable tombstones.
    for item in desired["nodes"]:
        content = fetch(
            c,
            "SELECT content FROM section_revisions WHERE bid_id=%s AND draft_id=%s AND section_id=%s AND revision=%s",
            (bid, draft, UUID(item["section_id"]), item["section_revision"]),
        )
        contents[item["section_id"]] = content["content"]
    known = {n["section_id"] for n in desired["nodes"]}
    for item in current["nodes"]:
        if item["section_id"] not in known:
            desired["nodes"].append(deepcopy(item) | {"deleted": True})
    return preview(c, bid, draft, actor, body, condition, prospective=(desired, contents, {}))


def history(c, bid, draft, before=None, limit=50):
    fetch(c, "SELECT id FROM drafts WHERE id=%s AND bid_id=%s", (draft, bid))
    rows = c.execute(
        "SELECT id,draft_revision,snapshot_id,digest,created_by,created_at FROM authoring_structure_versions WHERE bid_id=%s AND draft_id=%s AND (%s::bigint IS NULL OR draft_revision<%s) ORDER BY draft_revision DESC LIMIT %s",
        (bid, draft, before, before, limit + 1),
    ).fetchall()
    return clean(
        {
            "items": rows[:limit],
            "next_cursor": rows[limit - 1]["draft_revision"] if len(rows) > limit else None,
        }
    )


def create_from_directory(c, bid, actor, title, directory_id):
    """Create a new draft from immutable structure only; never import project facts."""
    from packages.authoring.structure_model import node
    from packages.lifecycle.service import assert_work_writable

    require_read_committed(c)
    assert_work_writable(c, bid)
    templates.ensure_defaults(c, actor)
    directory = templates.view(c, directory_id, "outline")
    draft = uid()
    c.execute("INSERT INTO drafts(id,bid_id,revision) VALUES(%s,%s,1)", (draft, bid))
    mapping = {n["key"]: uid() for n in directory["config"]["nodes"]}
    nodes = []
    by_key = {n["key"]: n for n in directory["config"]["nodes"]}
    for item in directory["config"]["nodes"]:
        identity = mapping[item["key"]]
        n = node(
            identity,
            parent=str(mapping[item["parent_key"]]) if item["parent_key"] else None,
            ordinal=item["ordinal"],
            kind=item["node_type"],
        )
        n.update(template_family_id=directory["family_id"], template_key=item["key"])
        nodes.append(n)
    for index, n in enumerate(ordered(nodes)):
        identity = UUID(n["section_id"])
        item = by_key[n["template_key"]]
        c.execute(
            "INSERT INTO sections(id,bid_id,draft_id,head_revision) VALUES(%s,%s,%s,1)",
            (identity, bid, draft),
        )
        append_section(c, bid, draft, identity, 1, empty_content(item["title"], index), actor)
        if item.get("goal") or item.get("planned_pages") is not None:
            from packages.authoring.planning import insert_template_plan

            n["plan_version_id"] = insert_template_plan(
                c, bid, draft, identity, 1, actor, directory, item
            )
    state = {
        "nodes": nodes,
        "metadata": {"format_bindings": [], "format_review_ack": None},
        "directory_template_version_id": directory["id"],
        "style_template_version_id": str(templates.DEFAULT_IDS["layout"]),
    }
    saved = freeze(c, bid, draft, 1, title, actor=actor, structure_state=state)
    c.execute("UPDATE bid_profiles SET current_draft_id=%s WHERE bid_id=%s", (draft, bid))
    return draft, saved
