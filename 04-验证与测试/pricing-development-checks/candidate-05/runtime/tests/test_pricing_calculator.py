"""Decimal手算对照与未支持公式、假设、边界的拒绝。"""

from decimal import getcontext

import pytest

from packages.pricing.calculator import simulate


def formula(method="lowest_valid_ratio"):
    return {
        "method": method,
        "maximum": "30",
        "minimum_price": "80",
        "maximum_price": "120",
        "minimum_valid_bids": 2,
        "above_coefficient": "0",
        "below_coefficient": "0",
    }


def request():
    return {
        "candidate_price": "100",
        "candidate_prices": ["100.00", "90", "121"],
        "competitor_price_assumptions": ["90", "110", "70"],
        "rounding_policy": "half_up_2dp",
        "curve_range": {"lower": "80", "upper": "120", "points": 3},
    }


def test_lowest_price_hand_calculation_and_scenario_specific_bases():
    result = simulate(formula(), request())
    assert result["main"]["score"] == "27.00"  # 30 * 90 / 100
    assert [p["score"] for p in result["candidates"]] == ["27.00", "30.00", None]
    assert [p["price"] for p in result["curve"]] == ["80", "100", "120"]
    assert result["curve"][0]["score"] == "30.00"  # own 80 changes lowest valid price
    assert result["curve"][-1]["score"] == "22.50"
    assert result["candidates"][-1]["eligible"] is False


def test_mean_deviation_asymmetric_coefficients_and_zero_clamp():
    f = formula("mean_valid_deviation")
    f.update(above_coefficient="2", below_coefficient="1")
    r = request()
    r.update(
        candidate_price="110",
        candidate_prices=["90"],
        competitor_price_assumptions=["90"],
        curve_range=None,
    )
    result = simulate(f, r)
    assert result["main"]["score"] == "24.00"  # mean100, 10% above, coefficient2
    r["competitor_price_assumptions"] = ["110"]
    r["candidate_price"] = "90"
    assert simulate(f, r)["main"]["score"] == "27.00"  # mean100, 10% below
    f["above_coefficient"] = "100"
    r["candidate_price"] = "120"
    assert simulate(f, r)["main"]["score"] == "0.00"


def test_insufficient_eligible_assumptions_are_unknown_not_zero():
    r = request()
    r["competitor_price_assumptions"] = ["70", "130"]
    result = simulate(formula(), r)
    assert result["main"]["eligible"] is True
    assert result["main"]["score"] is None
    assert result["main"]["notes"]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e10", 1.2, "-1", "1" * 25])
def test_unbounded_or_approximate_numbers_rejected(bad):
    r = request()
    r["candidate_price"] = bad
    with pytest.raises(ValueError):
        simulate(formula(), r)


def test_unsupported_rules_and_invalid_curves_rejected_without_global_decimal_mutation():
    before = getcontext().prec
    f = formula()
    f["method"] = "eval:user_expression"
    with pytest.raises(ValueError):
        simulate(f, request())
    for interval in (
        {"lower": "100", "upper": "100", "points": 3},
        {"lower": "80", "upper": "120", "points": 102},
    ):
        r = request()
        r["curve_range"] = interval
        with pytest.raises(ValueError):
            simulate(formula(), r)
    assert getcontext().prec == before


def test_maximum_must_be_exact_at_score_precision_and_no_clamp_extra_decimals():
    f = formula()
    f["maximum"] = "30.001"
    with pytest.raises(ValueError, match="两位小数"):
        simulate(f, request())
    f["maximum"] = "30.0000"
    assert simulate(f, request())["curve"][0]["score"] == "30.00"
    f["maximum"] = "0.005"
    with pytest.raises(ValueError):
        simulate(f, request())


def test_equal_opponents_are_distinct_and_every_curve_point_exposes_own_base():
    f, r = formula(), request()
    f["minimum_valid_bids"] = 3
    r["competitor_price_assumptions"] = ["90", "90", "70"]
    result = simulate(f, r)
    assert result["main"]["valid_price_count"] == 3
    assert result["curve"][0]["baseline_price"] == "80"
    assert result["curve"][-1]["baseline_price"] == "90"
    assert result["excluded_competitor_assumptions"] == [
        {"index": 2, "price": "70", "reason": "低于公式最低价格"}
    ]


def test_repeating_mean_does_not_bias_an_exact_half_even_score_tie():
    # B=64/3; own=20 gives relative deviation=(4/3)/(64/3)=1/16.
    # Raw score is exactly 30*(15/16)=28.125, not an approximation above the tie.
    formula = {
        "method": "mean_valid_deviation",
        "maximum": "30",
        "minimum_price": None,
        "maximum_price": None,
        "minimum_valid_bids": 1,
        "above_coefficient": "1",
        "below_coefficient": "1",
    }
    request = {
        "candidate_price": "20",
        "competitor_price_assumptions": ["21", "23"],
        "candidate_prices": [],
        "curve_range": None,
        "rounding_policy": "half_even_2dp",
    }
    assert simulate(formula, request)["main"]["score"] == "28.12"
    assert (
        simulate(formula, request | {"rounding_policy": "half_up_2dp"})["main"]["score"] == "28.13"
    )
