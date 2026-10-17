"""API snapshot extension; Worker verifies frozen JSON, never reads new business tables."""

import json
from copy import deepcopy
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring import templates
from packages.authoring.structure_model import node, ordered, validate_tree
from packages.authoring.structure_schema import SCHEMA_DIGEST
from packages.domain.runtime import DomainError, clean, fetch, sha, uid

MAX_EXTENSION_BYTES = 8 * 1024 * 1024


def available(c):
    return (
        c.execute("SELECT to_regclass('design.authoring_structure_versions') AS id").fetchone()[
            "id"
        ]
        is not None
    )


def checked(extension):
    expected = {
        "id",
        "schema_digest",
        "nodes",
        "plans",
        "directory_template",
        "style_template",
        "metadata",
        "digest",
    }
    if (
        not isinstance(extension, dict)
        or set(extension) != expected
        or extension["schema_digest"] != SCHEMA_DIGEST
        or sha({k: v for k, v in extension.items() if k != "digest"}) != extension["digest"]
    ):
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "草稿树/计划/模板完整快照校验失败", 409)
    validate_tree(extension["nodes"])
    for name, kind in [("directory_template", "outline"), ("style_template", "layout")]:
        template = extension[name]
        if (
            template["kind"] != kind
            or sha(template["config"]) != template["config_digest"]
            or template["schema_digest"] != templates.config_schema(kind)
        ):
            raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "快照模板身份或配置摘要不匹配", 409)
        templates.checked_config(kind, template["config"])
    required_plans = {
        n["plan_version_id"]: n["section_id"]
        for n in extension["nodes"]
        if not n["deleted"] and n["plan_version_id"]
    }
    if len(required_plans) != sum(
        not n["deleted"] and n["plan_version_id"] is not None for n in extension["nodes"]
    ) or set(extension["plans"]) != set(required_plans):
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "活动章节计划的完整供给清单不一致", 409)
    for identity, plan in extension["plans"].items():
        if (
            plan["id"] != identity
            or plan["section_id"] != required_plans[identity]
            or plan_digest(plan) != plan["digest"]
        ):
            raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "快照编写计划身份或摘要不匹配", 409)
    return extension


def snapshot_extension(manifest):
    value = manifest.get("authoring_structure")
    return checked(value) if value is not None else None


def latest(c, bid, draft):
    if not available(c):
        return None
    row = c.execute(
        "SELECT s.manifest FROM authoring_structure_versions a JOIN draft_snapshots s ON s.id=a.snapshot_id AND s.bid_id=a.bid_id AND s.draft_id=a.draft_id WHERE a.bid_id=%s AND a.draft_id=%s ORDER BY a.draft_revision DESC LIMIT 1",
        (bid, draft),
    ).fetchone()
    return snapshot_extension(row["manifest"]) if row else None


def reconcile(extension, sections):
    """Preserve tombstones; only genuinely new core IDs become new root nodes."""
    if extension is None:
        nodes = [
            node(row["section_id"], ordinal=i, revision=row["revision"])
            for i, row in enumerate(sections)
        ]
        return {
            "nodes": nodes,
            "metadata": {"format_bindings": [], "format_review_ack": None},
            "directory_template_version_id": str(templates.DEFAULT_IDS["outline"]),
            "style_template_version_id": str(templates.DEFAULT_IDS["layout"]),
        }
    nodes = deepcopy(extension["nodes"])
    known = {n["section_id"] for n in nodes}
    count = sum(not n["deleted"] and n["parent_id"] is None for n in nodes)
    for row in sections:
        if str(row["section_id"]) not in known:
            nodes.append(node(row["section_id"], ordinal=count, revision=row["revision"]))
            count += 1
    return {
        "nodes": ordered(nodes, include_deleted=True),
        "metadata": deepcopy(extension["metadata"]),
        "directory_template_version_id": extension["directory_template"]["id"],
        "style_template_version_id": extension["style_template"]["id"],
    }


def active_sections(c, bid, draft, sections, state=None):
    extension = latest(c, bid, draft) if state is None else None
    if state is None and extension is None:
        return sections
    state = state or reconcile(extension, sections)
    rows = {str(r["section_id"]): r for r in sections}
    nodes = ordered(state["nodes"])
    if any(n["section_id"] not in rows for n in nodes):
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "活动章节在当前草稿中缺失", 409)
    return [rows[n["section_id"]] for n in nodes]


def assert_active(c, bid, draft, section):
    extension = latest(c, bid, draft)
    if extension and any(
        n["section_id"] == str(section) and n["deleted"] for n in extension["nodes"]
    ):
        raise DomainError(
            "AUTHORING_SECTION_DELETED", "该章节已从当前草稿删除，可查看历史或先预览恢复", 409
        )


def plan(c, identity):
    row = fetch(c, "SELECT * FROM authoring_plan_versions WHERE id=%s", (identity,))
    if plan_digest(row) != row["digest"]:
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "编写计划内容摘要不匹配", 409)
    return clean(row)


def plan_digest(row):
    fields = (
        "bid_id",
        "draft_id",
        "section_id",
        "revision",
        "base_section_revision",
        "state",
        "planned_pages",
        "payload",
    )
    return sha(clean({name: row[name] for name in fields}))


def prepare(c, bid, draft, sections, actor, state=None):
    if state is None:
        extension = latest(c, bid, draft)
        if extension is None:
            return None
        state = reconcile(extension, sections)
    if actor is None:
        raise DomainError("AUTHORING_ACTOR_REQUIRED", "追加结构快照需要实际操作者", 503)
    templates.ensure_defaults(c, actor)
    nodes = deepcopy(state["nodes"])
    rows = {str(r["section_id"]): r for r in sections}
    plans = {}
    for item in nodes:
        if not item["deleted"]:
            if item["section_id"] not in rows:
                raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "活动章节未冻结正文修订", 409)
            item["section_revision"] = rows[item["section_id"]]["revision"]
            if item["plan_version_id"]:
                value = plan(c, UUID(item["plan_version_id"]))
                if (
                    value["bid_id"] != str(bid)
                    or value["draft_id"] != str(draft)
                    or value["section_id"] != item["section_id"]
                ):
                    raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "计划不属于本草稿章节", 409)
                plans[value["id"]] = value
    result = {
        "id": str(uid()),
        "schema_digest": SCHEMA_DIGEST,
        "nodes": ordered(nodes, include_deleted=True),
        "plans": plans,
        "directory_template": templates.view(
            c, UUID(state["directory_template_version_id"]), "outline"
        ),
        "style_template": templates.view(c, UUID(state["style_template_version_id"]), "layout"),
        "metadata": deepcopy(state["metadata"]),
    }
    result = clean(result)
    result["digest"] = sha(result)
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_EXTENSION_BYTES:
        raise DomainError(
            "AUTHORING_STRUCTURE_TOO_LARGE", "树/计划完整输入超过单次8MiB范围，未静默截断", 422
        )
    return checked(result)


def persist(c, bid, draft, revision, snapshot, extension, actor):
    if extension is None:
        return
    checked(extension)
    c.execute(
        "INSERT INTO authoring_structure_versions(id,bid_id,draft_id,draft_revision,snapshot_id,directory_template_version_id,style_template_version_id,metadata,digest,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            UUID(extension["id"]),
            bid,
            draft,
            revision,
            snapshot,
            UUID(extension["directory_template"]["id"]),
            UUID(extension["style_template"]["id"]),
            Jsonb(extension["metadata"]),
            extension["digest"],
            actor,
        ),
    )
    for item in extension["nodes"]:
        c.execute(
            "INSERT INTO authoring_structure_nodes(structure_id,bid_id,draft_id,section_id,parent_id,ordinal,node_type,deleted,section_revision,plan_version_id,template_family_id,template_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                UUID(extension["id"]),
                bid,
                draft,
                UUID(item["section_id"]),
                UUID(item["parent_id"]) if item["parent_id"] else None,
                item["ordinal"],
                item["node_type"],
                item["deleted"],
                item["section_revision"],
                UUID(item["plan_version_id"]) if item["plan_version_id"] else None,
                UUID(item["template_family_id"]) if item["template_family_id"] else None,
                item["template_key"],
            ),
        )


def check_snapshot_sections(extension, sections):
    if extension is None:
        return
    checked(extension)
    active = ordered(extension["nodes"])
    if len(active) != len(sections):
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "树活动章节与快照正文数量不一致", 409)
    for item, row in zip(active, sections, strict=True):
        if (
            str(row["section_id"]) != item["section_id"]
            or row["revision"] != item["section_revision"]
        ):
            raise DomainError(
                "AUTHORING_STRUCTURE_CORRUPT", "树活动章节与快照正文顺序/版本不一致", 409
            )


def frozen_authority(c, bid, extension, sections):
    """Same final guard for API freeze and Worker publish; only frozen core JSON is read."""
    from packages.authoring.content import empty_content
    from packages.authoring.format_bindings import assert_snapshot
    from packages.authoring.repository import assert_sources, source_authority_manifest

    checked(extension)
    refs = []
    for content in sections:
        assert_sources(c, bid, content)
        refs.extend(content["attrs"]["source_refs"])
    for plan_row in extension["plans"].values():
        from packages.authoring.planning import assert_budget

        assert_budget(c, bid, plan_row)
        content = empty_content("冻结计划依据", 0)
        content["attrs"]["source_refs"] = plan_row["payload"]["source_refs"]
        content["attrs"]["rule_ids"] = plan_row["payload"]["rule_ids"]
        assert_sources(c, bid, content)
        refs.extend(content["attrs"]["source_refs"])
    report = assert_snapshot(c, bid, extension)
    for binding in extension["metadata"].get("format_bindings", []):
        refs.extend(binding["source_refs"])
    return {"sources": source_authority_manifest(c, refs), "format": report}
