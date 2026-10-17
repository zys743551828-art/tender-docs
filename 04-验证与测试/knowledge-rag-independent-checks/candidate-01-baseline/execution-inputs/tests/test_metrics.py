"""Product denominators and disclosure boundaries with explicit synthetic rows."""

from decimal import Decimal

import pytest

from packages.domain.runtime import DomainError
from packages.metrics.contracts import validate
from packages.metrics.core import STAGES, ledger, online_errors, ratio, snapshot_totals


def stage(value, verification="pending", usable=True, reasons=None):
    return {
        "result": {
            "stage": value,
            "verification": verification,
            "usable": usable,
            "unusable_reasons": reasons or [],
        }
    }


def test_met03_exact_shortlist_and_final_win_denominators():
    rows = [stage(s) for s in STAGES if s != "pending"] + [
        stage("pending"),
        stage("awarded", "conflict", False, ["verification_conflict"]),
    ]
    result = ledger(rows)
    assert (
        result["effective_submissions"],
        result["known_outcomes"],
        result["unknown_outcomes"],
        result["conflicted_outcomes"],
    ) == (10, 8, 1, 1)
    assert (
        result["shortlist_rate"]["numerator"] == 4 and result["shortlist_rate"]["denominator"] == 6
    )
    assert result["win_rate"] == {"numerator": 1, "denominator": 4, "value": "0.25"}
    assert result["stage_counts"]["candidate"] == 1 and result["stage_counts"]["awarded"] == 1


def test_met04_zero_denominator_differs_from_true_zero():
    assert ledger([stage("cancelled"), stage("failed_tender"), {"result": None}])[
        "win_rate"
    ] == ratio(0, 0)
    assert ledger([stage("not_awarded")])["win_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "value": "0",
    }


def test_verification_rejected_is_not_tender_rejected():
    result = ledger([stage("rejected"), stage("awarded", "rejected", False)])
    assert result["stage_counts"]["rejected"] == 1
    assert result["stage_counts"]["awarded"] == 0
    assert result["conflicted_outcomes"] == 1


def test_verified_only_excludes_unverified_without_misnaming_them_official():
    rows = [stage("awarded"), stage("not_awarded", "verified")]
    assert ledger(rows)["win_rate"]["value"] == "0.5"
    narrowed = ledger(rows, verification_scope="verified_only")
    assert narrowed["win_rate"]["value"] == "0"
    assert narrowed["verification_counts"]["pending"] == 1
    assert narrowed["excluded_result_counts"]["not_verified_in_selected_scope"] == 1


def test_met12_twenty_pairs_retraction_and_signed_bias_never_grant_qualification():
    pair = {
        "predicted": Decimal(80),
        "prediction_maximum": Decimal(100),
        "official": Decimal(70),
        "official_maximum": Decimal(100),
    }
    for count in (0, 1, 19):
        value = online_errors([pair] * count)
        assert value["mae"] is None and value["bias"] is None
    enough = online_errors([pair] * 20)
    assert enough["mae"] == "10" and enough["bias"] == "+10"
    assert online_errors([pair] * 19, {"prediction_hidden": 1})["mae"] is None
    negative = {**pair, "predicted": "60"}
    assert online_errors([negative] * 20)["bias"] == "-10"


def test_met08_unknown_snapshots_are_not_zero_or_full_coverage():
    assert snapshot_totals([None, {"basis": "unknown", "open": None}])["open_at_submission"] is None
    mixed = snapshot_totals([{"basis": "reconstructed_from_audit", "open": 1}, None])
    assert (
        mixed["open_at_submission"] == 1
        and mixed["known_snapshot_submissions"] == 1
        and mixed["unknown_snapshot_submissions"] == 1
    )


def test_business_stage_request_cannot_write_technical_score_or_verification():
    body = {
        "submission_id": "00000000-0000-0000-0000-000000000001",
        "stage": "candidate",
        "source_type": "user_reported",
        "source_version_id": None,
        "evidence_refs": [],
        "announced_at": None,
        "known_at": "2026-10-16T12:00:00Z",
        "reason": "合成登记",
    }
    validate("BusinessResultRequest", body)
    for extra in ({"value": 95}, {"verification": "verified"}, {"score_kind": "technical_total"}):
        with pytest.raises(DomainError):
            validate("BusinessResultRequest", body | extra)
