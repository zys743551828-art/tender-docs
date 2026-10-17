"""不接触标签库、聊天或供应商的纯评分工程。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID, uuid5

from packages.domain.runtime import DomainError, sha

GROUP_NAMESPACE = UUID("e3bac72f-949e-4cf5-9bc4-e4fd24f35bed")
FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "outcome",
        "outcomes",
        "label",
        "score",
        "official_score",
        "judges",
        "chat",
        "messages",
        "summary",
        "prediction",
        "post_submission_revision",
    }
)


def assert_clean_structure(value):
    if isinstance(value, dict):
        if FORBIDDEN_INPUT_KEYS.intersection(value):
            raise DomainError(
                "SCORING_INPUT_NOT_CLEAN", "评分输入结构中不能包含标签、聊天或事后衍生物", 422
            )
        for child in value.values():
            assert_clean_structure(child)
    elif isinstance(value, list) or isinstance(value, tuple):
        for child in value:
            assert_clean_structure(child)


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DomainError("INVALID_AMOUNT", "金额或分数必须为有限非负十进制", 422) from None
    if not result.is_finite() or result < 0:
        raise DomainError("INVALID_AMOUNT", "金额或分数必须为有限非负十进制", 422)
    return result


def validate_score(kind, value, maximum):
    if kind not in {"technical_total", "comprehensive_total", "unknown"}:
        raise DomainError("INVALID_SCORE_KIND", "请区分技术总分、综合总分和未知结果", 422)
    if (value is None) != (maximum is None) or (kind == "unknown" and value is not None):
        raise DomainError("INVALID_SCORE", "未知结果不填零；已知分数须同时记录满分", 422)
    if value is not None and (amount(maximum) <= 0 or amount(value) > amount(maximum)):
        raise DomainError("INVALID_SCORE", "得分不能超过正数满分", 422)


def connected_groups(rows):
    """项目、模板、重复正文连通闭包；组身份不能由调用方自由指定。"""
    parents = {str(row["id"]): str(row["id"]) for row in rows}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        key = str(row["id"])
        for kind in ("project_id", "template_group", "body_digest", "near_duplicate_group"):
            value = row.get(kind)
            if not value:
                continue
            identity = (kind, str(value))
            other = seen.setdefault(identity, key)
            one, two = find(key), find(other)
            parents[max(one, two)] = min(one, two)
    components: dict[str, list[str]] = {}
    for key in parents:
        components.setdefault(find(key), []).append(key)
    return {
        member: str(uuid5(GROUP_NAMESPACE, sha(sorted(members))))
        for members in components.values()
        for member in members
    }


def validate_split(rows, split, cutoff):
    """所有标签/派生来源均按全组和成绩取得时间检查，未知时间拒绝。"""
    if set(split) != {str(row["id"]) for row in rows}:
        raise DomainError("SPLIT_SAMPLE_MISMATCH", "划分必须完整覆盖本次且仅覆盖本次样本", 422)
    groups = connected_groups(rows)
    assignments: dict[str, str] = {}
    for row in rows:
        key = str(row["id"])
        side = split[key]
        if side not in {"train", "validation", "test", "excluded"}:
            raise DomainError("INVALID_SPLIT", "样本划分不合法", 422)
        if side == "excluded":
            continue
        group = groups[key]
        if group in assignments and assignments[group] != side:
            raise DomainError(
                "RELATED_GROUP_LEAK", "同项目、模板或重复正文不得跨训练与评测集合", 422
            )
        assignments[group] = side
        known, submitted = row.get("known_at"), row.get("submitted_at")
        if not isinstance(known, datetime) or not isinstance(submitted, datetime):
            raise DomainError("SAMPLE_TIME_UNKNOWN", "正式划分需要可核验的提交与成绩取得时间", 422)
        if side == "train" and (known >= cutoff or submitted >= cutoff):
            raise DomainError(
                "FUTURE_LABEL_LEAK", "训练来源包含测试起点尚未取得的成绩或未来提交", 422
            )
        if side == "test" and submitted < cutoff:
            raise DomainError("TEST_TIME_BOUNDARY", "最终测试必须使用冻结时间边界后的独立项目", 422)
    if not any(side == "train" for side in split.values()) or not any(
        side == "test" for side in split.values()
    ):
        raise DomainError("INSUFFICIENT_SPLIT", "独立实验至少需要训练和测试来源", 422)
    return groups


@dataclass(frozen=True)
class FullSection:
    id: str
    body_version_id: str
    parse_id: str
    physical_pages: tuple[int, ...]
    blocks: tuple[dict, ...]
    gaps: tuple[str, ...] = ()


def chapter_package(*, rule, body_version_id, parse_id, sections, required_ids, method_digest):
    assert_clean_structure(rule)
    if set(rule) != {"id", "statement", "conditions", "exceptions", "bands", "maximum"}:
        raise DomainError("SCORING_INPUT_NOT_CLEAN", "评分规则只接受结构化白名单字段", 422)
    if not required_ids or len(required_ids) != len(set(required_ids)):
        raise DomainError("CHAPTER_RANGE_REQUIRED", "请明确不重复的完整相关章节范围", 422)
    indexed = {section.id: section for section in sections}
    if len(indexed) != len(sections) or set(indexed) != set(required_ids):
        raise DomainError(
            "CHAPTER_COVERAGE_MISMATCH", "完整章节清单与必读范围不一致，不接受top-k片段代替", 422
        )
    frozen = []
    for key in required_ids:
        section = indexed[key]
        if section.body_version_id != body_version_id or section.parse_id != parse_id:
            raise DomainError("CHAPTER_VERSION_MISMATCH", "章节必须来自同一冻结正文和解析版本", 422)
        if section.gaps or not section.physical_pages or not section.blocks:
            raise DomainError(
                "CHAPTER_UNREAD", "存在未读取的章节、表格或图页，不能声称完整评分", 422
            )
        for block in section.blocks:
            if set(block) != {"evidence_id", "physical_page", "text", "digest"}:
                raise DomainError(
                    "SCORING_INPUT_NOT_CLEAN", "章节不能夹带标签、聊天或事后总结", 422
                )
            if (
                block["physical_page"] not in section.physical_pages
                or sha(block["text"].encode()) != block["digest"]
            ):
                raise DomainError("CHAPTER_EVIDENCE_INVALID", "章节证据页码或摘要核验失败", 422)
        frozen.append(
            {"id": key, "pages": list(section.physical_pages), "blocks": list(section.blocks)}
        )
    value = {
        "purpose": "formal_score",
        "rule": rule,
        "body_version_id": body_version_id,
        "parse_id": parse_id,
        "sections": frozen,
        "method_digest": method_digest,
    }
    return value | {"digest": sha(value)}


class JudgmentPort(Protocol):
    def judge(self, *, package: dict, replicate: int, logical_key: str) -> tuple[dict, str]:
        """端口必须用受控研究账本，返回输出和不可复用到其他判断的call ID。"""
        ...


def judge_three(package, port: JudgmentPort):
    """同一冻结输入三次独立请求；不传前次输出，不自动重试或补零。"""
    assert_clean_structure(package)
    expected = {
        "purpose",
        "rule",
        "body_version_id",
        "parse_id",
        "sections",
        "method_digest",
        "digest",
    }
    if (
        set(package) != expected
        or package["purpose"] != "formal_score"
        or sha({k: v for k, v in package.items() if k != "digest"}) != package["digest"]
    ):
        raise DomainError("SCORING_INPUT_NOT_CLEAN", "完整评分包身份或白名单核验失败", 422)
    allowed = {
        str(block["evidence_id"]): block["text"]
        for section in package["sections"]
        for block in section["blocks"]
    }
    bands = {band["id"] for band in package["rule"]["bands"]}
    results, calls = [], set()
    for replicate in range(1, 4):
        # 复制冻结字典，避免端口修改下一次输入；没有共享前次判断上下文。
        import json

        value, call = port.judge(
            package=json.loads(json.dumps(package)),
            replicate=replicate,
            logical_key=f"{package['digest']}:replicate:{replicate}",
        )
        if not call or call in calls:
            raise DomainError("JUDGMENT_CALL_REUSED", "三次判断必须具有三个独立有效调用身份", 409)
        calls.add(call)
        if (
            set(value) != {"band_id", "reason", "evidence", "coverage", "status"}
            or value["status"] != "valid"
            or value["coverage"] != "complete"
            or not value["reason"]
        ):
            raise DomainError(
                "JUDGMENT_INVALID", "判断失败、结构错误或读取范围不足，不能补成有效重复", 409
            )
        if value["band_id"] is not None and value["band_id"] not in bands:
            raise DomainError("JUDGMENT_INVALID_BAND", "档位不属于当前评分规则", 409)
        for citation in value["evidence"]:
            if (
                set(citation) != {"evidence_id", "quote"}
                or not citation["quote"]
                or citation["quote"] not in allowed.get(str(citation["evidence_id"]), "")
            ):
                raise DomainError(
                    "JUDGMENT_INVALID_EVIDENCE", "判断引文不属于实际提供的章节原文", 409
                )
        results.append({"replicate": replicate, "call_id": call, "payload": value})
    return {
        "package_digest": package["digest"],
        "valid_replicates": 3,
        "judgments": results,
        "prediction": {
            "mode": "unavailable",
            "reasons": ["尚无真实正式技术标签的独立精度与发布验收"],
        },
    }
