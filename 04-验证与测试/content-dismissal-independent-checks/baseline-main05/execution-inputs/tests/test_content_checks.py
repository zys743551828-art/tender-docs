"""独立检测的范围和双侧断言协议，不宣称模型召回/误报质量。"""

import pytest

from packages.domain.content_checks import prepare, validate_output
from packages.domain.runtime import DomainError


def scope(kind="consistency", *, coverage="complete"):
    left = {"id": "body-1", "text_content": "工程A标段一工期90天"}
    right = {"id": "body-2", "text_content": "工程A标段一工期120天"}
    source = {"id": "required-1", "text_content": "工程A标段一项目名称：新工程"}
    body = {
        "document_version_id": "body",
        "role": "body",
        "coverage": coverage,
        "gaps": [],
        "page_count": 1,
        "pages": [{"physical_page": 1, "quality": "usable", "gaps": []}],
        "blocks": [left, right],
    }
    tender = {
        "document_version_id": "tender",
        "role": "tender",
        "coverage": "complete",
        "gaps": [],
        "page_count": 1,
        "pages": [{"physical_page": 1, "quality": "usable", "gaps": []}],
        "blocks": [source],
    }
    row = {"manifest": {"body_version_id": "body"}}
    evidence = {
        "rules": [
            {
                "id": "rule",
                "kind": "mandatory",
                "statement": "本工程工期必须保持一致",
                "provenance_status": "effective",
            }
        ],
        "confirmed_facts": [],
    }
    return prepare(kind, row, evidence, [body, tender]), evidence


def assertion(identity, quote, value, *, attribute="工期", unit="天"):
    return {
        "evidence_id": identity,
        "quote": quote,
        "subject": "工程A",
        "scope": "标段一",
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "relation": "equal",
    }


def test_independent_numeric_conflict_has_grounded_two_sides_and_safe_server_statement():
    prepared, evidence = scope()
    candidate = {
        "rule_id": "rule",
        "severity": "critical",
        "statement": "任意模型文字包含不支持的新工程量99999吨",
        "left": assertion("body-1", "工程A标段一工期90天", "90"),
        "right": assertion("body-2", "工程A标段一工期120天", "120"),
    }
    result = validate_output(
        "consistency",
        {"read_evidence_ids": ["body-1", "body-2"], "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    assert result["check_state"] == "complete" and len(result["issues"]) == 1
    assert "99999" not in result["issues"][0]["statement"]
    assert result["issues"][0]["left"]["normalized"] == "90"
    assert result["read_evidence_count"] == result["planned_evidence_count"] == 2


def test_failed_engineering_claim_is_retained_as_partial_diagnostic_and_marks_core_impact():
    prepared, evidence = scope()
    candidate = {
        "rule_id": "rule",
        "severity": "critical",
        "statement": "错误引文",
        "left": assertion("body-1", "并不存在的90天", "90"),
        "right": assertion("body-2", "工程A标段一工期120天", "120"),
    }
    result = validate_output(
        "consistency",
        {"read_evidence_ids": ["body-1", "body-2"], "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    assert result["check_state"] == "partial" and result["issues"] == []
    assert result["core_affected_rule_ids"] == ["rule"] and result["gaps"]
    assert result["diagnostics"][0]["code"] == "quote_not_found"


def test_zero_findings_on_partial_parse_is_not_full_coverage_and_does_not_close_any_issue():
    prepared, evidence = scope(coverage="partial")
    result = validate_output(
        "consistency",
        {"read_evidence_ids": ["body-1", "body-2"], "issues": [], "gaps": []},
        prepared,
        evidence,
    )
    assert (
        result["check_state"] == "partial" and result["issues"] == [] and "resolved" not in result
    )


def test_missing_or_extra_read_ids_fail_range_not_fake_zero_findings():
    prepared, evidence = scope()
    with pytest.raises(DomainError) as error:
        validate_output(
            "consistency",
            {"read_evidence_ids": ["body-1"], "issues": [], "gaps": []},
            prepared,
            evidence,
        )
    assert error.value.details["diagnostics"][0]["code"] == "incomplete_range"


def test_full_input_limit_preflights_without_truncating_pages():
    row = {"manifest": {"body_version_id": "body"}}
    doc = {
        "document_version_id": "body",
        "role": "body",
        "coverage": "complete",
        "gaps": [],
        "page_count": 1,
        "pages": [{"physical_page": 1, "quality": "usable", "gaps": []}],
        "blocks": [{"id": "body-1", "text_content": "原件" * 150000}],
    }
    with pytest.raises(DomainError) as error:
        prepare("consistency", row, {"rules": [], "confirmed_facts": []}, [doc])
    assert error.value.code == "DETECTOR_INPUT_RANGE_LIMIT"


def test_scope_residue_compares_full_body_with_explicit_current_required_source():
    prepared, evidence = scope("scope_residue")
    body = "工程A标段一项目名称：旧工程"
    prepared["blocks"]["body-1"]["text_content"] = body
    candidate = {
        "rule_id": None,
        "severity": "major",
        "statement": "项目名残留",
        "left": assertion("body-1", body, "旧工程", attribute="项目名称", unit=None),
        "right": assertion(
            "required-1", "工程A标段一项目名称：新工程", "新工程", attribute="项目名称", unit=None
        ),
    }
    result = validate_output(
        "scope_residue",
        {"read_evidence_ids": list(prepared["blocks"]), "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    assert result["check_state"] == "complete" and len(result["issues"]) == 1
    candidate["right"] = candidate["left"]
    invalid = validate_output(
        "scope_residue",
        {"read_evidence_ids": list(prepared["blocks"]), "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    assert (
        invalid["check_state"] == "partial"
        and invalid["diagnostics"][0]["code"] == "source_not_allowed"
    )


def test_duplicate_conflict_groups_are_not_silently_discarded_as_complete():
    prepared, evidence = scope()
    candidate = {
        "rule_id": "rule",
        "severity": "critical",
        "statement": "重复组",
        "left": assertion("body-1", "工程A标段一工期90天", "90"),
        "right": assertion("body-2", "工程A标段一工期120天", "120"),
    }
    result = validate_output(
        "consistency",
        {
            "read_evidence_ids": list(prepared["blocks"]),
            "issues": [candidate, candidate],
            "gaps": [],
        },
        prepared,
        evidence,
    )
    assert len(result["issues"]) == 1 and result["check_state"] == "partial"
    assert result["diagnostics"][0]["code"] == "invalid_structure"


def test_empty_gap_array_does_not_prove_physical_page_range_complete():
    prepared, evidence = scope()
    doc = prepared["documents"][0]
    doc["page_count"] = 2
    new = prepare("consistency", {"manifest": {"body_version_id": "body"}}, evidence, [doc])
    result = validate_output(
        "consistency",
        {"read_evidence_ids": list(new["blocks"]), "issues": [], "gaps": []},
        new,
        evidence,
    )
    assert result["check_state"] == "partial" and result["gaps"]
