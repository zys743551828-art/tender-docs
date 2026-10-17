"""Closed actual knowledge-version protocol, without changing frozen target contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from packages.domain.runtime import DomainError

CATEGORIES = {"company_fact", "method", "standard", "retrospective", "chapter_reference"}
PURPOSES = {"assist", "authoring", "compliance", "history"}
APP_FIELDS = {
    "bidder_ids",
    "bid_ids",
    "regions",
    "engineering_types",
    "conditions",
    "purposes",
    "validity_kind",
    "valid_from",
    "valid_until",
    "validity_basis",
    "specificity_note",
}


def invalid(message="请补全知识版本的主体、条件、期限和出处"):
    raise DomainError("INVALID_KNOWLEDGE_VERSION", message, 422)


def text(value, maximum=100000):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        invalid()
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        invalid()
    return value


def instant(value):
    if not isinstance(value, str):
        invalid("有效时间需要带时区的完整日期时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            invalid("有效时间需要明确时区")
        return result.astimezone(timezone.utc)
    except ValueError:
        invalid("有效时间格式无效")


def ids(values, minimum=0, maximum=100):
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        invalid()
    try:
        parsed = [UUID(v) for v in values]
        if len(set(parsed)) != len(parsed):
            invalid("列表中不能重复同一对象")
    except (TypeError, ValueError, AttributeError):
        invalid("对象ID格式无效")
    return parsed


def validate_version(body):
    if not isinstance(body, dict) or set(body) != {
        "title",
        "content",
        "categories",
        "scope",
        "applicability",
        "source_refs",
    }:
        invalid()
    text(body["title"], 200)
    text(body["content"])
    text(body["scope"], 2000)
    cats = body["categories"]
    if (
        not isinstance(cats, list)
        or not cats
        or any(not isinstance(c, str) or c not in CATEGORIES for c in cats)
        or len(set(cats)) != len(cats)
    ):
        invalid("请选择明确的知识分类")
    app = body["applicability"]
    if not isinstance(app, dict) or set(app) != APP_FIELDS:
        invalid()
    app["bidder_ids"] = [str(value) for value in ids(app["bidder_ids"], 1)]
    app["bid_ids"] = [str(value) for value in ids(app["bid_ids"])]
    for field in ["regions", "engineering_types", "purposes"]:
        values = app[field]
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 100
            or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values)
            or len(set(values)) != len(values)
        ):
            invalid("地区、工程类型及用途必须明确，未知不能当作全部适用")
    if not set(app["purposes"]) <= PURPOSES:
        invalid("企业知识不提供正式评分白名单入口")
    if not isinstance(app["conditions"], list) or len(app["conditions"]) > 100:
        invalid()
    for condition in app["conditions"]:
        if not isinstance(condition, dict) or set(condition) != {
            "subject",
            "scope",
            "name",
            "value",
            "unit",
        }:
            invalid("附加工程条件必须对应可核实项目事实")
        for field in ["subject", "scope", "name"]:
            text(condition[field], 200)
        if (
            type(condition["value"]) not in (str, int, float, bool)
            or condition["unit"] is not None
            and not isinstance(condition["unit"], str)
        ):
            invalid()
        if isinstance(condition["value"], float):
            import math

            if not math.isfinite(condition["value"]):
                invalid()
    if app["validity_kind"] not in ("bounded", "no_expiry_confirmed", "unknown"):
        invalid()
    start = instant(app["valid_from"])
    end = instant(app["valid_until"]) if app["valid_until"] is not None else None
    if end is not None and end <= start:
        invalid("有效期结束必须晚于开始")
    if app["validity_kind"] == "bounded" and end is None:
        invalid("有期限的知识必须给出到期时间")
    if app["validity_kind"] != "bounded" and end is not None:
        invalid("无期限或待核实状态不能混入到期时间")
    text(app["validity_basis"], 2000)
    text(app["specificity_note"], 2000)
    refs = body["source_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 100:
        invalid("请至少提供一处原件定位")
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {
            "source_id",
            "source_revision",
            "document_version_id",
            "parse_id",
            "physical_page",
            "evidence_id",
            "excerpt",
        }:
            invalid("知识出处必须包含原件、解析、物理页、证据及逐字摘录")
        for field in ["source_id", "document_version_id", "parse_id", "evidence_id"]:
            ref[field] = str(ids([ref[field]], 1)[0])
        if (
            type(ref["source_revision"]) is not int
            or ref["source_revision"] < 1
            or type(ref["physical_page"]) is not int
            or ref["physical_page"] < 1
        ):
            invalid()
        text(ref["excerpt"], 20000)
        key = (ref["source_id"], ref["evidence_id"])
        if key in seen:
            invalid("知识出处不能重复")
        seen.add(key)
    return body
