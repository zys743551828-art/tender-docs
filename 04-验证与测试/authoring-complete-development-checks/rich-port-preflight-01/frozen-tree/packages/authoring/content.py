"""A closed ProseMirror subset; no HTML, external assets or executable links."""

from __future__ import annotations

import json
from uuid import UUID

from packages.domain.runtime import DomainError, sha

SCHEMA = {
    "version": 1,
    "nodes": [
        "doc",
        "paragraph",
        "heading",
        "text",
        "hardBreak",
        "bulletList",
        "orderedList",
        "listItem",
    ],
    "marks": ["bold", "italic"],
    "metadata": ["title", "ordinal", "rule_ids", "source_refs"],
}
SCHEMA_DIGEST = sha(SCHEMA)


def invalid(message="章节结构不符合当前编辑格式"):
    raise DomainError("INVALID_SECTION_CONTENT", message, 422)


def validate_text_content(content: dict) -> dict:
    if not isinstance(content, dict):
        invalid()
    try:
        if len(json.dumps(content, ensure_ascii=False).encode()) > 1_000_000:
            invalid()
    except (UnicodeError, ValueError, TypeError):
        invalid("章节包含不可保存的字符或结构")
    if set(content) != {"type", "attrs", "content"} or content["type"] != "doc":
        invalid()
    attrs = content["attrs"]
    if not isinstance(attrs, dict) or set(attrs) != {"title", "ordinal", "rule_ids", "source_refs"}:
        invalid()
    if not isinstance(attrs["title"], str) or not 1 <= len(attrs["title"].strip()) <= 200:
        invalid("请输入章节名称")
    if any(ord(ch) < 32 for ch in attrs["title"]):
        invalid("章节名称包含不可用控制字符")
    if type(attrs["ordinal"]) is not int or not 0 <= attrs["ordinal"] < 500:
        invalid()
    for name in ("rule_ids", "source_refs"):
        if not isinstance(attrs[name], list) or len(attrs[name]) > 500:
            invalid()
    try:
        for value in attrs["rule_ids"]:
            UUID(value)
        for ref in attrs["source_refs"]:
            if set(ref) != {"source_id", "source_revision", "evidence_id", "document_version_id"}:
                invalid()
            for field in ("source_id", "evidence_id", "document_version_id"):
                UUID(ref[field])
            if type(ref["source_revision"]) is not int or ref["source_revision"] < 1:
                invalid()
    except (TypeError, ValueError, AttributeError):
        invalid()
    seen: set[str] = set()
    count = 0

    def node(value, depth, parent):
        nonlocal count
        count += 1
        if count > 10000 or depth > 12 or not isinstance(value, dict):
            invalid()
        kind = value.get("type")
        allowed = {
            "doc": {"paragraph", "heading", "bulletList", "orderedList"},
            "listItem": {"paragraph", "bulletList", "orderedList"},
            "bulletList": {"listItem"},
            "orderedList": {"listItem"},
            "paragraph": {"text", "hardBreak"},
            "heading": {"text", "hardBreak"},
        }
        if not isinstance(kind, str) or kind not in allowed.get(parent, set()):
            invalid()
        if kind == "text":
            if set(value) - {"type", "text", "marks"} or not isinstance(value.get("text"), str):
                invalid()
            if any(ord(ch) < 32 and ch not in "\t\n" for ch in value["text"]):
                invalid()
            marks = value.get("marks", [])
            if not isinstance(marks, list) or any(
                m not in ({"type": "bold"}, {"type": "italic"}) for m in marks
            ):
                invalid()
        elif kind == "hardBreak":
            if set(value) != {"type"}:
                invalid()
        else:
            if set(value) != {"type", "attrs", "content"} or not isinstance(value["attrs"], dict):
                invalid()
            expected = {"id", "level"} if kind == "heading" else {"id"}
            if set(value["attrs"]) != expected:
                invalid()
            key = value["attrs"]["id"]
            if not isinstance(key, str) or not key or len(key) > 128 or key in seen:
                invalid("章节节点ID必须唯一且稳定")
            seen.add(key)
            if kind == "heading" and value["attrs"]["level"] not in (1, 2, 3, 4, 5, 6):
                invalid()
            if not isinstance(value["content"], list):
                invalid()
            for child in value["content"]:
                node(child, depth + 1, kind)

    if not isinstance(content["content"], list):
        invalid()
    for child in content["content"]:
        node(child, 0, "doc")
    return content


def has_rich(content):
    return (
        isinstance(content, dict)
        and isinstance(content.get("content"), list)
        and any(
            isinstance(node, dict) and node.get("type") in {"table", "image"}
            for node in content["content"]
        )
    )


def schema_digest_for(content):
    if has_rich(content):
        from packages.authoring.rich_content import SCHEMA_DIGEST as RICH_DIGEST

        return RICH_DIGEST
    return SCHEMA_DIGEST


def validate_for_schema(content, digest):
    if digest == SCHEMA_DIGEST:
        return validate_text_content(content)
    from packages.authoring.rich_content import SCHEMA_DIGEST as RICH_DIGEST
    from packages.authoring.rich_content import validate_content as validate_rich

    if digest == RICH_DIGEST:
        return validate_rich(content)
    raise DomainError("SECTION_SCHEMA_UNSUPPORTED", "章节格式版本不受支持", 422)


def validate_content(content: dict) -> dict:
    """Internal content guard; HTTP saving must also check its explicit digest."""
    return validate_for_schema(content, schema_digest_for(content))


def empty_content(title: str, ordinal: int) -> dict:
    from uuid import uuid4

    return {
        "type": "doc",
        "attrs": {"title": title, "ordinal": ordinal, "rule_ids": [], "source_refs": []},
        "content": [{"type": "paragraph", "attrs": {"id": str(uuid4())}, "content": []}],
    }
