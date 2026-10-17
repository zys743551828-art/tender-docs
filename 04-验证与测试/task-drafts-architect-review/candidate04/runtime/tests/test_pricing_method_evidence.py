"""A ratio without deviation coefficients is usable; real operands still need evidence."""

import copy
from uuid import uuid4

import pytest

from packages.domain.runtime import DomainError
from packages.pricing.repository import validate_terms
from tests.test_pricing_integration import TEXT, formula_request

RATIO_FIELDS = {
    "method",
    "maximum",
    "minimum_price",
    "maximum_price",
    "minimum_valid_bids",
    "amount_unit",
    "currency",
    "rounding_policy",
    "baseline_set",
    "exclusions",
    "intermediate_rounding",
    "clamping",
}


def ratio_request():
    item = {key: str(uuid4()) for key in ("parse", "doc", "evidence")}
    text = TEXT.replace("上下方向偏离系数为0。", "")
    value = formula_request(item, text)
    value["evidence_quotes"][0]["supported_fields"] = sorted(RATIO_FIELDS)
    return value


def test_ratio_needs_no_quote_for_unused_zero_slots():
    value = ratio_request()
    assert "偏离系数" not in value["evidence_quotes"][0]["quote"]
    validate_terms(value)


@pytest.mark.parametrize("field", sorted(RATIO_FIELDS))
def test_every_real_operand_or_interpretation_still_requires_evidence(field):
    value = ratio_request()
    value["evidence_quotes"][0]["supported_fields"].remove(field)
    with pytest.raises(DomainError) as error:
        validate_terms(value)
    assert error.value.code == "PRICING_FORMULA_INCOMPLETE"
    assert error.value.details["missing_fields"] == [field]


@pytest.mark.parametrize("field", ["above_coefficient", "below_coefficient"])
def test_mean_deviation_requires_its_real_coefficients_even_when_zero(field):
    value = ratio_request()
    value["formula"]["method"] = "mean_valid_deviation"
    value["evidence_quotes"][0]["supported_fields"].append(
        "below_coefficient" if field == "above_coefficient" else "above_coefficient"
    )
    with pytest.raises(DomainError) as error:
        validate_terms(value)
    assert error.value.code == "PRICING_FORMULA_INCOMPLETE"
    assert error.value.details["missing_fields"] == [field]


def test_ratio_still_refuses_nonzero_coefficient_and_unrecognized_support():
    value = ratio_request()
    bad = copy.deepcopy(value)
    bad["formula"]["above_coefficient"] = "1"
    with pytest.raises(DomainError) as error:
        validate_terms(bad)
    assert error.value.code == "PRICING_FORMULA_UNSUPPORTED"
    value["evidence_quotes"][0]["supported_fields"].append("arbitrary_user_formula")
    with pytest.raises(DomainError) as error:
        validate_terms(value)
    assert error.value.code == "PRICING_EVIDENCE_FIELDS_INVALID"


@pytest.mark.parametrize("negative", ["基准价不取整", "基准价不作中间取整", "基准价不进行四舍五入"])
def test_explicit_no_baseline_rounding_is_not_an_unsupported_rounding_rule(negative):
    value = ratio_request()
    value["evidence_quotes"][0]["quote"] += "计算过程与" + negative + "；"
    validate_terms(value)


@pytest.mark.parametrize(
    "positive", ["基准价四舍五入保留2位", "基准价取整", "基准价不取整但保留2位"]
)
def test_positive_or_ambiguous_rounding_still_rejected_after_a_negative_clause(positive):
    value = ratio_request()
    value["evidence_quotes"][0]["quote"] += "计算过程与基准价不取整；" + positive + "。"
    with pytest.raises(DomainError) as error:
        validate_terms(value)
    assert error.value.code == "PRICING_FORMULA_UNSUPPORTED"
