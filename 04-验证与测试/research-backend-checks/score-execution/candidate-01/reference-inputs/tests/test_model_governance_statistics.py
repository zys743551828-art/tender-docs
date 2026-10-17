"""Explicit synthetic mathematics; none of these tests grant business eligibility."""

import pytest

from packages.model_governance import evaluation, leakage, statistics, training
from packages.model_governance.common import GovernanceError
from packages.model_governance.features import SCHEMA


def row(key, x, target, **extra):
    return {
        "id": str(key),
        "features": [x, 1.0, 0.0, 1.0],
        "target": target,
        "project_id": str(key),
        "group_id": str(key),
        "method": "synthetic-ratio",
        "purpose": "fit",
        **extra,
    }


def test_project_equal_mean_and_median_do_not_overweight_repeated_company_rows():
    rows = [
        row("a", 10, 10, project_id="one"),
        row("b", 20, 30, project_id="one"),
        row("c", 90, 80, project_id="two"),
    ]
    mean = statistics.fit(rows, "mean")
    median = statistics.fit(rows, "median")
    assert statistics.predict(mean, [0, 1, 0, 1], method="unknown") == 50
    assert statistics.predict(median, [0, 1, 0, 1], method="unknown") == 55


def test_ridge_independent_hand_solution_and_training_only_scaler():
    rows = [row("a", 10, 20), row("b", 30, 40)]
    model = statistics.fit(rows, "ridge", alpha=2)
    p = model["parameters"]
    # x standardized = [-1,+1], y centered = [-10,+10]: beta=20/(2+2)=5.
    assert p["means"][0] == 20 and p["scales"][0] == 10
    assert p["coefficients"][0] == pytest.approx(5)
    assert statistics.predict(model, [40, 1, 0, 1], method="any") == pytest.approx(40)
    assert statistics.predict(model, [0, 1, 0, 1], method="any") == pytest.approx(20)
    assert p["means"][0] == 20  # Prediction does not fit on future observations.


def test_isotonic_pava_ties_and_interpolation_are_independently_known():
    model = statistics.fit([row("a", 10, 30), row("b", 20, 10), row("c", 30, 50)], "isotonic")
    assert model["parameters"]["knots"] == [[10, 20], [20, 20], [30, 50]]
    assert statistics.predict(model, [25, 1, 0, 1], method="any") == 35
    assert statistics.predict(model, [1, 1, 0, 1], method="any") == 20


def test_shrinkage_fallback_and_raw_band_have_explicit_semantics():
    model = statistics.fit(
        [row("a", 10, 20, method="a"), row("b", 20, 80, method="b")],
        "method_shrinkage",
        shrinkage=1,
    )
    assert statistics.predict(model, [1, 1, 0, 1], method="a") == 35
    assert statistics.predict(model, [1, 1, 0, 1], method="unseen") == 50
    raw = statistics.fit([row("a", 10, 20)], "raw_band")
    assert statistics.predict(raw, [75, 1, 0, 1], method="any") == 75


def test_group_conformal_finite_rank_and_maximum_not_llm_spread():
    model = statistics.fit([row("base", 0, 0)], "mean")
    rows = [row(str(i), 0, i, purpose="calibration") for i in range(1, 10)]
    result = evaluation.conformal(model, rows, levels=[0.8, 0.9])
    assert result["0.8"]["radius"] == 8 and result["0.9"]["radius"] == 9
    insufficient = evaluation.conformal(model, rows[:3], levels=[0.8, 0.9])
    assert insufficient["0.8"]["radius"] is None and insufficient["0.9"]["radius"] is None
    rows.append(row("same-group", 0, 20, group_id="1", purpose="calibration"))
    assert evaluation.conformal(model, rows, levels=[0.9])["0.9"]["radius"] == 20


def member(key, purpose, **extra):
    return {
        "id": str(key),
        "purpose": purpose,
        "project_id": str(key),
        "group_id": str(key),
        "body_digest": f"body-{key}",
        "template_key": None,
        "near_duplicate_key": None,
        "known_at": "2026-01-02T00:00:00Z" if purpose != "final" else "2026-04-02T00:00:00Z",
        "submitted_at": "2026-01-01T00:00:00Z" if purpose != "final" else "2026-04-01T00:00:00Z",
        **extra,
    }


@pytest.mark.parametrize(
    "field", ["project_id", "group_id", "body_digest", "template_key", "near_duplicate_key"]
)
def test_every_connected_link_refuses_fit_final_leak(field):
    members = [
        member("a", "fit", **{field: "shared"}),
        member("b", "development"),
        member("c", "final", **{field: "shared"}),
    ]
    with pytest.raises(GovernanceError, match="Connected source closure"):
        leakage.check_members(members, test_start="2026-03-01T00:00:00Z")


@pytest.mark.parametrize("purpose", ["fit", "development", "calibration"])
def test_future_labels_refused_for_every_fitting_or_selection_purpose(purpose):
    members = [
        member("a", "fit"),
        member("b", "development"),
        member("c", "final"),
        member("d", purpose, known_at="2026-03-01T00:00:00Z"),
    ]
    with pytest.raises(GovernanceError, match="precede test start"):
        leakage.check_members(members, test_start="2026-03-01T00:00:00Z")


def bundle():
    members = [
        member("a", "fit"),
        member("b", "fit"),
        member("c", "development"),
        member("d", "calibration"),
        member("e", "final"),
    ]
    return {
        "schema": "purpose-bound-training-export/1",
        "origin": "synthetic",
        "protocol": {
            "schema": "score-training-protocol/1",
            "feature_schema": SCHEMA,
            "families": ["mean", "raw_band", "ridge"],
            "ridge_alphas": [1, 2],
            "shrinkage_values": [1],
            "fold_count": 2,
            "seed": 5,
            "tie_order": ["raw_band", "mean", "ridge"],
            "refit_with_development": False,
            "interval_levels": [0.8, 0.9],
            "bootstrap_iterations": 100,
            "selection_metric": "project_mae",
        },
        "members": members,
        "rows": [
            row(m["id"], x, x, purpose=m["purpose"])
            for m, x in zip(members[:-1], [20, 80, 50, 60], strict=True)
        ],
        "test_start": "2026-03-01T00:00:00Z",
    }


def test_complete_frozen_training_selection_and_synthetic_ineligibility():
    result = training.run(bundle())
    assert result["selection"]["winner"] == "raw_band"
    assert result["research_only"] is True and result["formal_eligibility"] == "unavailable"
    assert (
        result["model"]["training_digest"]
        == statistics.fit(bundle()["rows"][:2], "raw_band")["training_digest"]
    )
    assert result["intervals"]["0.9"]["radius"] is None
    assert result == training.run(bundle())


def test_final_label_cannot_enter_trainer_or_be_renamed_as_fit():
    value = bundle()
    value["rows"].append(row("e", 50, 99, purpose="fit"))
    with pytest.raises(GovernanceError, match="Final labels"):
        training.run(value)
    value = bundle()
    value["rows"][0]["purpose"] = "calibration"
    with pytest.raises(GovernanceError, match="Purpose changed"):
        training.run(value)


def test_closed_json_loader_rejects_script_nonfinite_and_malformed_knots():
    model = statistics.fit([row("a", 1, 10)], "mean")
    for injected in [
        dict(model, pickle="anything"),
        dict(model, parameters={"constant": float("nan")}),
        dict(model, parameters={"constant": 10, "script": "execute"}),
    ]:
        with pytest.raises(GovernanceError):
            statistics.validate_model(injected)
    iso = statistics.fit([row("a", 1, 10), row("b", 2, 20)], "isotonic")
    iso["parameters"]["knots"] = [[2, 20], [1, 10]]
    with pytest.raises(GovernanceError):
        statistics.validate_model(iso)


def test_metrics_preserve_denominator_zero_and_original_scale():
    rows = [
        {
            "id": "a",
            "project_id": "a",
            "group_id": "a",
            "status": "succeeded",
            "prediction": 0,
            "target": 0,
            "maximum": 30,
        },
        {
            "id": "b",
            "project_id": "b",
            "group_id": "b",
            "status": "succeeded",
            "prediction": 40,
            "target": 50,
            "maximum": 30,
        },
        {"id": "c", "project_id": "c", "group_id": "c", "status": "failed"},
    ]
    report = evaluation.metrics(
        rows, expected_ids=["a", "b", "c"], intervals={}, seed=1, bootstrap_iterations=100
    )
    assert report["denominator"] == 3 and report["counts"]["failed"] == 1
    assert report["project_mae"] == 5 and report["original_scale_project_mae"] == 1.5
    assert report["project_bias"] == -5 and report["project_within_three"] == 0.5
    with pytest.raises(GovernanceError, match="Every frozen member"):
        evaluation.metrics(
            rows[:2], expected_ids=["a", "b", "c"], intervals={}, seed=1, bootstrap_iterations=100
        )


def test_paired_baseline_gain_uses_same_independent_groups_and_interval():
    rows = [
        {
            "id": "a",
            "project_id": "a",
            "group_id": "a",
            "status": "succeeded",
            "prediction": 10,
            "target": 10,
            "maximum": 30,
        },
        {
            "id": "b",
            "project_id": "b",
            "group_id": "b",
            "status": "succeeded",
            "prediction": 20,
            "target": 20,
            "maximum": 30,
        },
    ]
    report = evaluation.metrics(
        rows,
        expected_ids=["a", "b"],
        intervals={},
        seed=1,
        bootstrap_iterations=100,
        baseline_predictions={"baseline": {"a": 12, "b": 24}},
    )
    assert report["paired_baselines"]["baseline"] == {
        "gain": 3,
        "paired_count": 2,
        "group_bootstrap_gain_95": [2, 4],
    }


def test_same_project_cannot_get_two_group_ids_then_cross_inner_folds():
    members = [
        member("a", "fit", project_id="shared"),
        member("b", "fit", project_id="shared"),
        member("c", "development"),
        member("d", "final"),
    ]
    with pytest.raises(GovernanceError, match="one server-frozen group"):
        leakage.check_members(members, test_start="2026-03-01T00:00:00Z")
