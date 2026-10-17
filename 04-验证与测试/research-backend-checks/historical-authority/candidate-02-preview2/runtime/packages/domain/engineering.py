"""有界工程断言校验：逐字关系、主体范围与Decimal量纲，不作模糊语义替代。"""

import re
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from packages.domain.runtime import DomainError

DIGITS = dict(
    zip("零〇一二两三四五六七八九壹贰叁肆伍陆柒捌玖", "001223456789123456789", strict=True)
)
SMALL = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
LARGE = {"万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}
UNITS = {
    "米": ("length", "1"),
    "m": ("length", "1"),
    "厘米": ("length", ".01"),
    "毫米": ("length", ".001"),
    "千米": ("length", "1000"),
    "公里": ("length", "1000"),
    "平方米": ("area", "1"),
    "㎡": ("area", "1"),
    "m²": ("area", "1"),
    "平方厘米": ("area", ".0001"),
    "立方米": ("volume", "1"),
    "m³": ("volume", "1"),
    "人": ("persons", "1"),
    "台": ("machines", "1"),
    "套": ("sets", "1"),
    "天": ("unspecified_days", "1"),
    "日": ("unspecified_days", "1"),
    "日历天": ("calendar_days", "1"),
    "自然日": ("calendar_days", "1"),
    "自然天": ("calendar_days", "1"),
    "工作日": ("working_days", "1"),
    "小时": ("clock_duration", "3600"),
    "分钟": ("clock_duration", "60"),
    "秒": ("clock_duration", "1"),
    "月": ("months", "1"),
    "年": ("years", "1"),
    "吨": ("mass", "1000"),
    "千克": ("mass", "1"),
    "kg": ("mass", "1"),
    "%": ("ratio", ".01"),
    "％": ("ratio", ".01"),
}
OPERATORS = {
    "equal": ("", "为", "是", ":", "：", "=", "设定为"),
    "min": ("不少于", "不小于", "不低于", "至少", ">=", "≥"),
    "max": ("不超过", "不大于", "不高于", "至多", "<=", "≤"),
}
SCOPE_ATTRIBUTES = {"工程名称", "项目名称", "建设地点", "投标主体", "标段名称", "招标人"}
CONTEXT_LIMITS = re.compile(
    "若|如果|假设|除非|除外|不适用|可选|示例|例如|参考|曾承建|类似工程|历史项目"
)


def rejected(code, path, evidence_ids=()) -> NoReturn:
    diagnostic = {
        "code": code,
        "field_path": path,
        "evidence_ids": list(evidence_ids),
        "action": "needs_review",
        "message": "该工程断言未通过逐字关系、主体范围或单位核验，请查看对应原页",
    }
    raise DomainError(
        "ENGINEERING_ASSERTION_REJECTED", diagnostic["message"], 409, {"diagnostics": [diagnostic]}
    )


def number(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("unsupported number")
    try:
        if re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", value):
            result = Decimal(value.replace(",", ""))
        else:
            negative = value.startswith("负")
            raw = value[1:] if negative else value
            if raw.count("点") > 1:
                raise ValueError("unsupported number")
            integer, _, fraction = raw.partition("点")
            if not integer or any(ch not in DIGITS for ch in fraction):
                raise ValueError("unsupported number")
            if all(ch in DIGITS for ch in integer):
                total = int("".join(DIGITS[ch] for ch in integer))
            else:
                total, section, digit = 0, 0, 0
                last_small, last_kind, zero_after_small = 10000, "", False
                seen_yi, seen_wan = False, False
                for ch in integer:
                    if ch in DIGITS:
                        if last_kind == "digit" and digit != 0:
                            raise ValueError("unsupported number")
                        digit = int(DIGITS[ch])
                        zero_after_small |= digit == 0
                        last_kind = "digit"
                    elif ch in SMALL:
                        if SMALL[ch] >= last_small:
                            raise ValueError("unsupported number")
                        section += (digit or 1) * SMALL[ch]
                        digit = 0
                        last_small, last_kind, zero_after_small = SMALL[ch], "small", False
                    elif ch in LARGE:
                        if not section and not digit:
                            raise ValueError("unsupported number")
                        section += digit
                        digit = 0
                        if LARGE[ch] == 100000000:
                            if seen_yi:
                                raise ValueError("unsupported number")
                            total = (total + section) * LARGE[ch]
                            section = 0
                            seen_yi, seen_wan = True, False
                        else:
                            if seen_wan:
                                raise ValueError("unsupported number")
                            section = (section or 1) * LARGE[ch]
                            seen_wan = True
                        last_small, last_kind, zero_after_small = 10000, "large", False
                    else:
                        raise ValueError("unsupported number")
                if digit and last_small >= 100 and last_kind == "digit" and not zero_after_small:
                    # “二百二/三千五”具有口语歧义，不能擅自解释成202/3005。
                    raise ValueError("unsupported number")
                total += section + digit
            result = Decimal(
                str(total) + ("." + "".join(DIGITS[ch] for ch in fraction) if fraction else "")
            )
            if negative:
                result = -result
        if not result.is_finite() or abs(result) > Decimal("1e15"):
            raise ValueError("unsupported number")
        return result
    except (InvalidOperation, OverflowError) as error:
        raise ValueError("unsupported number") from error


def context_prefix(subject, scope):
    # 只接受逐字相邻/明确连接的局部关系，不能从同一长quote别处借主体或标段。
    bridge = r"(?:[\s，,：:、]|的|在|之|总|计划|施工)*"
    if subject in scope or scope in subject:
        context = re.escape(scope if subject in scope else subject)
    else:
        subject, scope = re.escape(subject), re.escape(scope)
        context = "(?:" + subject + bridge + scope + "|" + scope + bridge + subject + ")"
    return context + bridge


def claim(value, blocks, path):
    expected = {
        "evidence_id",
        "quote",
        "subject",
        "attribute",
        "scope",
        "value",
        "unit",
        "relation",
    }
    if not isinstance(value, dict) or set(value) != expected:
        rejected("invalid_structure", path)
    identity = value["evidence_id"]
    block = blocks.get(identity) if isinstance(identity, str) else None
    if not block:
        rejected("source_not_allowed", path + ".evidence_id")
    quote = value["quote"]
    if (
        not isinstance(quote, str)
        or not 1 <= len(quote) <= 20000
        or quote not in block["text_content"]
    ):
        rejected("quote_not_found", path + ".quote", [identity])
    if any(
        not isinstance(value[name], str) or not 1 <= len(value[name]) <= 200
        for name in ("subject", "attribute", "scope", "value")
    ):
        rejected("invalid_structure", path, [identity])
    if any(value[name] not in quote for name in ("subject", "attribute", "scope")):
        rejected("scope_mismatch", path, [identity])
    if CONTEXT_LIMITS.search(quote):
        rejected("scope_mismatch", path + ".scope", [identity])
    unit, relation = value["unit"], value["relation"]
    if value["attribute"] in SCOPE_ATTRIBUTES:
        if unit is not None or relation != "equal":
            rejected("invalid_structure", path, [identity])
        pattern = (
            re.escape(value["attribute"]) + r"\s*(?:为|是|：|:)?\s*" + re.escape(value["value"])
        )
        if not re.search(context_prefix(value["subject"], value["scope"]) + pattern, quote):
            rejected("scope_mismatch", path + ".value", [identity])
        return value | {"dimension": "scope_text", "normalized": value["value"]}
    if re.search("编号|日期|年份|序号|版本|合同号|页码", value["attribute"]):
        rejected("unsupported_quantity", path + ".attribute", [identity])
    if (
        not isinstance(unit, str)
        or unit not in UNITS
        or not isinstance(relation, str)
        or relation not in OPERATORS
    ):
        rejected("unit_mismatch", path + ".unit", [identity])
    operator = (
        "(?:"
        + "|".join(
            re.escape(token)
            for token in sorted(
                {token for tokens in OPERATORS.values() for token in tokens if token},
                key=lambda token: (-len(token), token),
            )
        )
        + ")?"
    )
    pattern = (
        re.escape(value["attribute"])
        + r"\s*"
        + "(?P<prefix>"
        + operator
        + ")"
        + r"\s*"
        + re.escape(value["value"])
        + r"\s*"
        + re.escape(unit)
        + r"(?P<post>及以上|及以下|以上|以下|以内|左右|上下)?(?![A-Za-z0-9/%％²³／]|每)"
    )
    if not re.search(pattern, quote):
        rejected("unsupported_quantity", path + ".value", [identity])
    matched = re.search(context_prefix(value["subject"], value["scope"]) + pattern, quote)
    if not matched:
        rejected("scope_mismatch", path + ".scope", [identity])
    prefix, post = matched.group("prefix"), matched.group("post")
    actual_relation = next(kind for kind, operators in OPERATORS.items() if prefix in operators)
    if post in ("左右", "上下"):
        rejected("unsupported_quantity", path + ".relation", [identity])
    if post:
        postfix_relation = "min" if post in ("以上", "及以上") else "max"
        if actual_relation != "equal" and actual_relation != postfix_relation:
            rejected("unsupported_quantity", path + ".relation", [identity])
        actual_relation = postfix_relation
    if actual_relation != relation:
        rejected("unsupported_quantity", path + ".relation", [identity])
    try:
        normalized = number(value["value"]) * Decimal(UNITS[unit][1])
    except ValueError:
        rejected("unsupported_quantity", path + ".value", [identity])
    return value | {"dimension": UNITS[unit][0], "normalized": str(normalized)}


def conflict(left, right, *, scope_residue=False):
    if any(left[name] != right[name] for name in ("subject", "attribute", "scope")):
        rejected("scope_mismatch", "assertions", [left["evidence_id"], right["evidence_id"]])
    if left["dimension"] != right["dimension"]:
        rejected("unit_mismatch", "assertions", [left["evidence_id"], right["evidence_id"]])
    if left["dimension"] == "scope_text":
        return scope_residue and left["normalized"] != right["normalized"]
    if scope_residue:
        return False
    a, b = Decimal(left["normalized"]), Decimal(right["normalized"])
    low_a = a if left["relation"] in ("equal", "min") else Decimal("-Infinity")
    high_a = a if left["relation"] in ("equal", "max") else Decimal("Infinity")
    low_b = b if right["relation"] in ("equal", "min") else Decimal("-Infinity")
    high_b = b if right["relation"] in ("equal", "max") else Decimal("Infinity")
    return max(low_a, low_b) > min(high_a, high_b)
