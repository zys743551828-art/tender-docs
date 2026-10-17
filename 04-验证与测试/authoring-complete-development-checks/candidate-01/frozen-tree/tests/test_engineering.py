"""真实确定性工程核验；无模型、无网络，不当作检测召回质量。"""

import pytest

from packages.domain.engineering import claim, conflict, number
from packages.domain.runtime import DomainError


@pytest.mark.parametrize(
    "text,expected",
    [
        ("九十", "90"),
        ("一百零二", "102"),
        ("二〇二四", "2024"),
        ("壹佰贰拾", "120"),
        ("三万零六十", "30060"),
        ("二亿三千万", "230000000"),
        ("负十二点五", "-12.5"),
        ("1,250.25", "1250.25"),
    ],
)
def test_chinese_and_arabic_values_are_decimal(text, expected):
    assert str(number(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "一十十",
        "一二十",
        "二百二",
        "一亿一亿",
        "三万五",
        "NaN",
        "1e9",
        "2026-01-01",
        "1,2",
        "万",
        "一万三万",
    ],
)
def test_ambiguous_and_non_quantity_values_are_not_guessed(text):
    with pytest.raises(ValueError):
        number(text)


def checked(quote, value, unit="天", relation="equal", identity="body", **overrides):
    data = {
        "evidence_id": identity,
        "quote": quote,
        "subject": "工程A",
        "attribute": "工期",
        "scope": "标段一",
        "value": value,
        "unit": unit,
        "relation": relation,
    } | overrides
    return claim(data, {identity: {"text_content": quote}}, "issues[0].left")


def test_subject_unit_scope_and_relations_prevent_false_contradictions():
    a = checked("工程A标段一工期不少于九十天", "九十", relation="min")
    b = checked("工程A标段一工期100天", "100")
    assert not conflict(a, b)
    c = checked("工程A标段一工期60天以下", "60", relation="max")
    assert conflict(a, c)
    h = checked("工程A标段一工期24小时", "24", "小时")
    minute = checked("工程A标段一工期1440分钟", "1440", "分钟")
    assert not conflict(h, minute)
    workday = checked("工程A标段一工期90工作日", "90", "工作日")
    with pytest.raises(DomainError) as mismatch:
        conflict(a, workday)
    assert mismatch.value.details["diagnostics"][0]["code"] == "unit_mismatch"


@pytest.mark.parametrize(
    "quote,value,unit,relation",
    [
        ("工程A标段一工期90天以上", "90", "天", "equal"),
        ("工程A标段一工期90天左右", "90", "天", "equal"),
        ("参考工程A标段一工期90天", "90", "天", "equal"),
        ("工程A标段一工期90米/天", "90", "米", "equal"),
        ("工程A标段一工期90日历天", "90", "天", "equal"),
        ("工程A标段一日期2026年，工期90天", "2026", "年", "equal"),
        ("工程A标段一工期90天；编号2026", "2026", "天", "equal"),
    ],
)
def test_quantities_cannot_be_lifted_from_other_context_or_approximate_values(
    quote, value, unit, relation
):
    with pytest.raises(DomainError) as invalid:
        checked(quote, value, unit, relation)
    assert invalid.value.code == "ENGINEERING_ASSERTION_REJECTED"


def test_scope_residue_has_two_explicit_project_claims_not_historical_reference():
    old = checked("工程A标段一项目名称：旧项目", "旧项目", None, attribute="项目名称")
    actual = checked(
        "工程A标段一项目名称：新项目", "新项目", None, identity="tender", attribute="项目名称"
    )
    assert conflict(old, actual, scope_residue=True)
    assert not conflict(old, old, scope_residue=True)
    with pytest.raises(DomainError):
        checked("参考历史项目工程A标段一项目名称：旧项目", "旧项目", None, attribute="项目名称")


def test_rejected_quote_diagnostic_does_not_echo_raw_content():
    private = "工程A标段一工期90天"
    value = {
        "evidence_id": "body",
        "quote": "虚构引文",
        "subject": "工程A",
        "attribute": "工期",
        "scope": "标段一",
        "value": "90",
        "unit": "天",
        "relation": "equal",
    }
    with pytest.raises(DomainError) as invalid:
        claim(value, {"body": {"text_content": private}}, "issues[0].left")
    assert invalid.value.details["diagnostics"][0]["field_path"] == "issues[0].left.quote"
    assert private not in str(invalid.value.details) and "虚构引文" not in str(
        invalid.value.details
    )


@pytest.mark.parametrize(
    "quote,attribute,value,unit",
    [
        ("工程A标段一工期90天；工程B标段一工期120天", "工期", "120", "天"),
        ("工程A标段一工期90天，工程B标段一工期120天", "工期", "120", "天"),
        ("工程A标段一工期90天；工程A标段二工期120天", "工期", "120", "天"),
        ("工程A标段一项目名称：旧工程；工程B标段一项目名称：新工程", "项目名称", "新工程", None),
    ],
)
def test_long_quote_cannot_borrow_other_project_or_lot_quantity_or_name(
    quote, attribute, value, unit
):
    with pytest.raises(DomainError) as rejected:
        checked(quote, value, unit, attribute=attribute)
    assert rejected.value.details["diagnostics"][0]["code"] == "scope_mismatch"


def test_local_context_accepts_explicit_connectors_and_overlapping_scope_without_guessing():
    assert checked("工程A，在标段一的总工期为90天", "90")["normalized"] == "90"
    assert checked("工程A标段一工期90天", "90", scope="工程A标段一")["normalized"] == "90"


@pytest.mark.parametrize(
    "quote",
    [
        "该说明并非工程A标段一工期90天，而是工程B标段二工期120天。",
        "工程A标段一工期90天已作废，更正为120天。",
        "工程A标段一不是工期90天。",
    ],
)
def test_explicit_negation_or_correction_cannot_be_a_positive_assertion(quote):
    with pytest.raises(DomainError) as rejected:
        checked(quote, "90")
    assert rejected.value.details["diagnostics"][0]["code"] == "scope_mismatch"
