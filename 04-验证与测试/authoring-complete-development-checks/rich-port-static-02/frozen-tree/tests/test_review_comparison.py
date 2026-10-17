"""两轮选择的确定性比较：不受第三轮/当前全局lifecycle偷换历史语义。"""

from copy import deepcopy

import pytest

from packages.domain.review_comparison import compare


def pair():
    rule = {
        "id": "left-rule",
        "kind": "mandatory",
        "statement": "工程A工期90天",
        "conditions": [],
        "exceptions": [],
        "max_points": None,
        "bands": [],
        "applicability_scope": {"bid_id": "bid", "description": "工程A"},
        "provenance_status": "effective",
        "evidence_ids": ["required-evi"],
    }
    root = {
        "identity": {"digest": "same-identity", "binding_status": "confirmed"},
        "rules": [rule],
        "confirmed_facts": [],
        "documents": [
            {
                "role": "body",
                "document_version_id": "left-body",
                "digest": "body-v1",
                "parser_digest": "parser",
                "config_digest": "config",
                "blocks": [{"id": "body-evi", "text_content": "工期120天"}],
            },
            {
                "role": "tender",
                "document_version_id": "requirements",
                "digest": "same-required-original",
                "parser_digest": "parser",
                "config_digest": "config",
                "blocks": [{"id": "required-evi", "text_content": "工程A工期90天"}],
            },
        ],
    }
    manifest = {
        "body_version_id": "left-body",
        "method_digest": "method",
        "policy_digest": "policy",
        "source_refs": [
            {"id": "source-required", "kind": "tender", "digest": "same-required-original"}
        ],
        "content_checks": {"consistency": True, "scope_residue": True},
    }
    finding = {
        "id": "left-finding",
        "root_id": "problem",
        "rule_id": "left-rule",
        "statement": "旧稿工期问题",
        "verdict": "violated",
        "freshly_checked": True,
    }
    left = {
        "review_id": "left-review",
        "manifest": manifest,
        "root": root,
        "state": "succeeded",
        "core_coverage": "complete",
        "sources_valid": True,
        "findings": [finding],
        "checks": [{"rule_id": "left-rule", "state": "succeeded", "verdict": "violated"}],
    }
    right = deepcopy(left)
    right.update(
        review_id="right-review",
        findings=[],
        checks=[
            {
                "rule_id": "right-rule",
                "state": "succeeded",
                "verdict": "satisfied",
                "range_eligible": True,
            }
        ],
    )
    right["root"]["rules"][0]["id"] = "right-rule"
    right["manifest"]["body_version_id"] = "right-body"
    right["root"]["documents"][0].update(
        document_version_id="right-body",
        digest="body-v2",
        blocks=[{"id": "right-body-evi", "text_content": "工期90天"}],
    )
    return {"left": left, "right": right, "comparison_method_digest": "0" * 64}


def test_rule_ids_may_change_but_immutable_semantics_and_original_proofs_match():
    result = compare(pair())
    assert result["comparable_for_content_attribution"] and result["break_reasons"] == []
    assert result["findings"][0]["status"] == "resolved_by_recheck"
    assert (
        result["body_changes"]["state"] == "complete" and len(result["body_changes"]["items"]) == 1
    )
    assert result["score_delta"] is None


def test_new_problem_and_old_resolved_are_both_visible_not_net_zero():
    plan = pair()
    plan["right"]["findings"] = [
        {
            "id": "new-issue",
            "root_id": "new-root",
            "rule_id": None,
            "statement": "另一个范围问题",
            "verdict": "violated",
            "freshly_checked": True,
        }
    ]
    states = {item["status"] for item in compare(plan)["findings"]}
    assert states == {"new", "resolved_by_recheck"}


@pytest.mark.parametrize(
    "mutation", ["partial", "same_body", "invalid_sources", "unable", "core_affected"]
)
def test_missing_specific_recheck_basis_is_not_resolved(mutation):
    plan = pair()
    if mutation == "partial":
        plan["right"]["core_coverage"] = "partial"
        plan["right"]["checks"][0]["range_eligible"] = False
    elif mutation == "same_body":
        plan["right"]["root"]["documents"][0]["digest"] = "body-v1"
    elif mutation == "invalid_sources":
        plan["right"]["sources_valid"] = False
    elif mutation == "unable":
        plan["right"]["checks"][0]["verdict"] = "unable_to_judge"
    else:
        plan["right"]["checks"][0]["core_affected"] = True
    assert compare(plan)["findings"][0]["status"] == "not_rechecked"


def test_selected_right_actual_violation_overrides_third_round_lifecycle_label():
    plan = pair()
    actual = deepcopy(plan["left"]["findings"][0])
    actual.update(
        id="selected-right-finding", rule_id="right-rule", lifecycle="resolved_by_recheck"
    )
    plan["right"]["findings"] = [actual]
    assert compare(plan)["findings"][0]["status"] == "persisting"
    actual["freshly_checked"] = False
    plan["right"]["checks"][0]["verdict"] = "unable_to_judge"
    assert compare(plan)["findings"][0]["status"] == "not_rechecked"


def test_method_or_rule_maximum_or_original_changes_block_attribution_without_hiding_raw_diff():
    plan = pair()
    plan["right"]["manifest"]["method_digest"] = "another-method"
    plan["right"]["root"]["rules"][0]["max_points"] = "10"
    result = compare(plan)
    assert not result["comparable_for_content_attribution"]
    assert {"method_changed", "rules_or_maxima_changed"} <= set(result["break_reasons"])
    assert result["body_changes"]["items"] and result["score_delta"] is None


def test_proved_ended_obligation_can_close_but_does_not_claim_same_rule_attribution():
    plan = pair()
    plan["right"]["root"]["documents"][0]["digest"] = "body-v1"
    plan["right"]["root"]["rules"][0]["provenance_status"] = "superseded"
    plan["right"]["checks"][0].update(verdict="not_applicable", obligation_ended=True)
    result = compare(plan)
    assert result["findings"][0]["status"] == "resolved_by_recheck"
    assert "rules_or_maxima_changed" in result["break_reasons"]


def test_complete_specific_scope_is_not_erased_by_unrelated_source_partial():
    plan = pair()
    plan["right"]["core_coverage"] = "partial"
    result = compare(plan)
    assert not result["comparable_for_content_attribution"]
    assert result["findings"][0]["status"] == "resolved_by_recheck"


def test_independent_conflict_is_not_closed_by_core_positive_or_zero_detection():
    plan = pair()
    plan["left"]["findings"][0]["core_recheck_applicable"] = False
    result = compare(plan)
    assert result["findings"][0]["status"] == "not_rechecked"
