"""Pure enrichment arithmetic only; SQL authority/fresh checks are separate."""

from copy import deepcopy

import pytest

from packages.model_governance.denominator import derive
from tests.test_model_governance_features import inputs


def case(maximum="30"):
    package, item = inputs()
    binding = {
        "id": "binding",
        "current": True,
        "recorded_status": "usable",
        "recorded_maximum": maximum,
        "scale_description": "未加权原始分",
        "scope": {"rules": [{"id": "rule-a", "maximum": "30"}]},
    }
    result = {
        "schema": "complete-score-judgments/1",
        "execution_purpose": "score",
        "method_digest": "synthetic-method",
        "items": [item],
    }
    return {
        "binding": binding,
        "packages": [package],
        "result": result,
        "source_result_digest": "a" * 64,
        "method_digest": "synthetic-method",
    }


def test_complete_sourced_enrichment_is_separate_and_never_a_formal_prediction():
    value = case()
    old = deepcopy(value)
    derived = derive(**value)
    assert value == old
    assert derived["features"]["status"] == "complete" and derived["coverage"]["fraction"] == "1"
    assert derived["prediction"]["value"] is None and derived["formal_eligibility"] == "unavailable"


def test_partial_coverage_never_scales_raw_points_into_a_total():
    derived = derive(**case("60"))
    assert derived["technical_maximum"] == "60"
    assert derived["coverage"]["fraction"] == "0.5" and derived["coverage"]["status"] == "partial"
    assert derived["features"]["status"] == "unavailable" and derived["prediction"]["value"] is None


@pytest.mark.parametrize("attack", ["withdrawn", "unknown", "weighted", "smaller", "two_judgments"])
def test_invalid_denominator_or_judgment_remains_null_or_partial(attack):
    value = case()
    if attack == "withdrawn":
        value["binding"]["current"] = False
    elif attack == "unknown":
        value["binding"]["recorded_status"] = "unavailable"
        value["binding"]["recorded_maximum"] = None
    elif attack == "weighted":
        value["binding"]["scale_description"] = "已加权技术总分"
    elif attack == "smaller":
        value["binding"]["recorded_maximum"] = "20"
    else:
        value["result"]["items"][0]["judgments"].pop()
    derived = derive(**value)
    assert derived["features"]["values"] is None and derived["prediction"]["value"] is None
    if attack != "two_judgments":
        assert derived["technical_maximum"] is None and derived["coverage"]["fraction"] is None
    else:
        assert (
            derived["coverage"]["valid_rules"] == 0 and derived["coverage"]["status"] == "partial"
        )


@pytest.mark.parametrize(
    "attack", ["rule_omission", "method", "changed_rule_max", "duplicate_result"]
)
def test_frozen_identity_mismatch_rejected(attack):
    from packages.model_governance.common import GovernanceError

    value = case()
    if attack == "rule_omission":
        value["binding"]["scope"]["rules"].append({"id": "necessary-other", "maximum": "1"})
    elif attack == "method":
        value["result"]["method_digest"] = "different"
    elif attack == "changed_rule_max":
        value["binding"]["scope"]["rules"][0]["maximum"] = "29"
    else:
        value["result"]["items"].append(deepcopy(value["result"]["items"][0]))
    with pytest.raises(GovernanceError):
        derive(**value)


def test_actual_nine_operations_are_closed_and_expose_no_client_maximum():
    from fastapi import FastAPI

    from apps.api.denominator_routes import CoverageRequest, DenominatorRequest, router
    from packages.model_governance.denominator_contracts import extend_openapi

    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    operations = [
        item[m] for item in actual["paths"].values() for m in ("get", "post") if m in item
    ]
    assert len(operations) == 9
    assert set(DenominatorRequest.model_fields) == {"reason"}
    assert set(CoverageRequest.model_fields) == {"reason", "job_id"}
    for operation in operations:
        for code, response in operation["responses"].items():
            if code in {"200", "201"}:
                name = response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                assert actual["components"]["schemas"][name]["additionalProperties"] is False


def test_hidden_derived_view_keeps_coverage_but_removes_feature_numbers(monkeypatch):
    from packages.model_governance import denominator_repository as repo

    value = case()
    document = derive(**value)
    stored = {
        "id": "r",
        "binding_id": "b",
        "job_id": "j",
        "document": document,
        "digest": "d",
        "created_at": "2026-10-17T00:00:00Z",
    }
    monkeypatch.setattr(repo, "require", lambda c: None)
    monkeypatch.setattr(
        repo,
        "binding",
        lambda c, i: {"current": False, "recorded_maximum": "30", "scope": {"bid_id": "bid"}},
    )
    monkeypatch.setattr(
        repo,
        "fetch",
        lambda c, q, p: {"settings": {"show_prediction": False}}
        if "settings" in q
        else deepcopy(stored),
    )
    result, _ = repo.result_view(None, "r")
    assert (
        result["technical_maximum"] is None
        and result["recorded_document"]["technical_maximum"] == "30"
    )
    assert (
        "features" not in result["recorded_document"]
        and "prediction" not in result["recorded_document"]
    )
    assert (
        result["recorded_document"]["coverage"]["fraction"] == "1" and result["historical_snapshot"]
    )
