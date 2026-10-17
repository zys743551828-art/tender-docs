"""Pure stable chapter tree operations. No SQL, source qualification or model calls."""

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from packages.authoring.content import empty_content, validate_content
from packages.domain.runtime import DomainError, sha

MAX_ACTIVE = 500
MAX_NODES = 2000
MAX_DEPTH = 6


def invalid(message, status=422):
    raise DomainError("AUTHORING_TREE_INVALID", message, status)


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        invalid("章节或父级身份无效")


def validate_tree(nodes):
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        invalid("章节树含历史节点最多2000项")
    by_id = {}
    allowed = {
        "section_id",
        "parent_id",
        "ordinal",
        "node_type",
        "deleted",
        "section_revision",
        "plan_version_id",
        "template_family_id",
        "template_key",
    }
    for node in nodes:
        if not isinstance(node, dict) or set(node) != allowed:
            invalid("目录节点字段不符合实际协议")
        identity = identifier(node["section_id"])
        if identity != node["section_id"] or identity in by_id:
            invalid("章节身份必须规范且唯一")
        if node["parent_id"] is not None and identifier(node["parent_id"]) != node["parent_id"]:
            invalid("父章节身份不规范")
        if type(node["ordinal"]) is not int or not 0 <= node["ordinal"] < MAX_NODES:
            invalid("同级顺序无效")
        if type(node["section_revision"]) is not int or node["section_revision"] < 1:
            invalid("章节修订无效")
        if node["node_type"] not in ("chapter", "appendix") or type(node["deleted"]) is not bool:
            invalid("章节类型或删除状态无效")
        for name in ("plan_version_id", "template_family_id"):
            if node[name] is not None and identifier(node[name]) != node[name]:
                invalid("计划或模板身份不规范")
        if (node["template_family_id"] is None) != (node["template_key"] is None):
            invalid("目录模板族与结构key必须同时给定")
        if node["template_key"] is not None and (
            not isinstance(node["template_key"], str) or not 1 <= len(node["template_key"]) <= 128
        ):
            invalid("目录模板结构key无效")
        by_id[identity] = node
    if sum(not n["deleted"] for n in nodes) > MAX_ACTIVE:
        invalid("当前活动章节最多500项")
    siblings: dict[str | None, list[int]] = {}
    keys: set[tuple[str, str]] = set()
    for node in nodes:
        seen, cursor, depth = set(), node, 0
        while cursor is not None:
            identity = cursor["section_id"]
            if identity in seen:
                invalid("目录树不能成环")
            seen.add(identity)
            depth += 1
            if depth > MAX_DEPTH and not node["deleted"]:
                invalid("目录树最多六层")
            parent = cursor["parent_id"]
            if parent is not None and parent not in by_id:
                invalid("父章节必须属于同一草稿树")
            cursor = by_id[parent] if parent else None
        if not node["deleted"]:
            parent = by_id.get(node["parent_id"])
            if parent and parent["deleted"]:
                invalid("活动章节不能挂在已删除父章下")
            siblings.setdefault(node["parent_id"], []).append(node["ordinal"])
            if node["template_key"] is not None:
                key = (node["template_family_id"], node["template_key"])
                if key in keys:
                    invalid("同一目录模板结构key不能重复使用")
                keys.add(key)
    if any(sorted(values) != list(range(len(values))) for values in siblings.values()):
        invalid("同级顺序必须从0连续且不重复")
    return nodes


def ordered(nodes, *, include_deleted=False):
    validate_tree(nodes)
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for node in nodes:
        if not node["deleted"]:
            groups.setdefault(node["parent_id"], []).append(node)
    result = []

    def visit(parent):
        for node in sorted(groups.get(parent, []), key=lambda n: (n["ordinal"], n["section_id"])):
            result.append(node)
            visit(node["section_id"])

    visit(None)
    return result + (
        sorted((n for n in nodes if n["deleted"]), key=lambda n: n["section_id"])
        if include_deleted
        else []
    )


def node(identity, *, parent=None, ordinal=0, kind="chapter", revision=1):
    return {
        "section_id": str(identity),
        "parent_id": parent,
        "ordinal": ordinal,
        "node_type": kind,
        "deleted": False,
        "section_revision": revision,
        "plan_version_id": None,
        "template_family_id": None,
        "template_key": None,
    }


def clone_content(content):
    copied = deepcopy(content)

    def visit(value):
        if "id" in value.get("attrs", {}):
            value["attrs"]["id"] = str(uuid4())
        for child in value.get("content", []):
            visit(child)

    visit(copied)
    return copied


def mutate(nodes, contents, changes):
    """Returns a complete prospective tree; caller validates refs and commits atomically."""
    validate_tree(nodes)
    result, texts = deepcopy(nodes), deepcopy(contents)
    aliases: dict[str, str] = {}
    copied_from: dict[str, str] = {}

    def get(identity, *, active=None):
        found = next((n for n in result if n["section_id"] == identity), None)
        if (
            found is None
            or (active is True and found["deleted"])
            or (active is False and not found["deleted"])
        ):
            invalid("章节不属于本次可操作范围", 409)
        return found

    def parent(value):
        if value is None:
            return None
        if set(value) == {"section_id"}:
            identity = identifier(value["section_id"])
        elif set(value) == {"client_key"} and value["client_key"] in aliases:
            identity = aliases[value["client_key"]]
        else:
            invalid("请先新增同批父章节，或选择真实已存父章")
        get(identity, active=True)
        return identity

    def siblings(parent_id, excluding=None):
        return sorted(
            (
                n
                for n in result
                if not n["deleted"] and n["parent_id"] == parent_id and n["section_id"] != excluding
            ),
            key=lambda n: n["ordinal"],
        )

    def insert(item, destination, ordinal):
        previous = item["parent_id"]
        old = siblings(previous, item["section_id"])
        for index, sibling in enumerate(old):
            sibling["ordinal"] = index
        group = siblings(destination, item["section_id"])
        if type(ordinal) is not int or not 0 <= ordinal <= len(group):
            invalid("目标同级位置超出范围")
        item["parent_id"], item["deleted"] = destination, False
        group.insert(ordinal, item)
        for index, sibling in enumerate(group):
            sibling["ordinal"] = index

    def descendants(identity, active_only=False):
        values, pending = [], [identity]
        while pending:
            current = pending.pop(0)
            values.append(current)
            pending.extend(
                n["section_id"]
                for n in result
                if n["parent_id"] == current and (not active_only or not n["deleted"])
            )
        return values

    for change in changes:
        op = change["op"]
        if op == "add":
            alias = change["client_key"]
            if alias in aliases:
                invalid("同批新增章节key不能重复")
            identity = str(uuid4())
            aliases[alias] = identity
            item = node(identity, kind=change["node_type"])
            texts[identity] = empty_content(change["title"], 0)
            result.append(item)
            insert(item, parent(change["parent"]), change["ordinal"])
        elif op == "move":
            item = get(identifier(change["section_id"]), active=True)
            destination = parent(change["parent"])
            if destination in descendants(item["section_id"]):
                invalid("不能移动到自身或后代章节")
            insert(item, destination, change["ordinal"])
        elif op == "rename":
            item = get(identifier(change["section_id"]), active=True)
            texts[item["section_id"]]["attrs"]["title"] = change["title"]
        elif op == "set_type":
            get(identifier(change["section_id"]), active=True)["node_type"] = change["node_type"]
        elif op == "delete":
            item = get(identifier(change["section_id"]), active=True)
            for identity in descendants(item["section_id"], active_only=True):
                get(identity)["deleted"] = True
            for index, sibling in enumerate(siblings(item["parent_id"])):
                sibling["ordinal"] = index
        elif op == "restore":
            item = get(identifier(change["section_id"]), active=False)
            restoring = (
                descendants(item["section_id"])
                if change.get("include_descendants", False)
                else [item["section_id"]]
            )
            insert(item, parent(change["parent"]), change["ordinal"])
            if len(restoring) > 1:
                for identity in restoring[1:]:
                    get(identity)["deleted"] = False
                for parent_id in restoring:
                    for index, sibling in enumerate(siblings(parent_id)):
                        sibling["ordinal"] = index
        elif op == "copy":
            source = get(identifier(change["section_id"]), active=True)
            original = [
                deepcopy(get(i)) for i in descendants(source["section_id"], active_only=True)
            ]
            mapping = {n["section_id"]: str(uuid4()) for n in original}
            for old in original:
                identity = mapping[old["section_id"]]
                item = node(
                    identity,
                    parent=mapping.get(old["parent_id"]),
                    ordinal=old["ordinal"],
                    kind=old["node_type"],
                )
                texts[identity] = clone_content(texts[old["section_id"]])
                result.append(item)
                copied_from[identity] = old["section_id"]
            insert(get(mapping[source["section_id"]]), parent(change["parent"]), change["ordinal"])
            if change.get("client_key"):
                if change["client_key"] in aliases:
                    invalid("复制key与已有新增key冲突")
                aliases[change["client_key"]] = mapping[source["section_id"]]
        else:
            invalid("未支持的目录操作")
        validate_tree(result)
    for index, item in enumerate(ordered(result)):
        texts[item["section_id"]]["attrs"]["ordinal"] = index
        validate_content(texts[item["section_id"]])
    return {
        "nodes": ordered(result, include_deleted=True),
        "contents": texts,
        "created_ids": aliases,
        "copied_from": copied_from,
        "digest": sha(ordered(result, include_deleted=True)),
    }
