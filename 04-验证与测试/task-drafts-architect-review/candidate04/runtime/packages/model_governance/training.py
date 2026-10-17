"""Frozen family/grid selection; this port accepts training-purpose exports only."""

from packages.model_governance import evaluation, leakage, statistics
from packages.model_governance.common import GovernanceError, digest, finite
from packages.model_governance.features import SCHEMA

REQUIRED = {
    "schema",
    "feature_schema",
    "families",
    "ridge_alphas",
    "shrinkage_values",
    "fold_count",
    "seed",
    "tie_order",
    "refit_with_development",
    "interval_levels",
    "bootstrap_iterations",
    "selection_metric",
}


def validate_protocol(protocol):
    if (
        set(protocol) != REQUIRED
        or protocol["schema"] != "score-training-protocol/1"
        or protocol["feature_schema"] != SCHEMA
    ):
        raise GovernanceError("TRAINING_PROTOCOL_REQUIRED", "Complete frozen protocol required")
    families = protocol["families"]
    if (
        not families
        or len(set(families)) != len(families)
        or not set(families) <= set(statistics.FAMILIES)
        or set(protocol["tie_order"]) != set(families)
        or len(protocol["tie_order"]) != len(families)
    ):
        raise GovernanceError(
            "CANDIDATE_PROTOCOL", "Explicit unique candidates and deterministic ties required"
        )
    if (
        type(protocol["refit_with_development"]) is not bool
        or protocol["selection_metric"] != "project_mae"
    ):
        raise GovernanceError("SELECTION_PROTOCOL", "Selection and refitting policy must be frozen")
    if (
        type(protocol["seed"]) is not int
        or type(protocol["fold_count"]) is not int
        or not 2 <= protocol["fold_count"] <= 20
    ):
        raise GovernanceError("FOLD_PROTOCOL", "Bounded deterministic group folds required")
    if (
        protocol["interval_levels"] != [0.8, 0.9]
        or type(protocol["bootstrap_iterations"]) is not int
        or not 100 <= protocol["bootstrap_iterations"] <= 10000
    ):
        raise GovernanceError(
            "INTERVAL_PROTOCOL", "Versioned interval and resampling protocol required"
        )
    for name, minimum in (("ridge_alphas", 1e-8), ("shrinkage_values", 0)):
        values = protocol[name]
        if (
            not isinstance(values, list)
            or not values
            or len(values) > 30
            or len(set(values)) != len(values)
        ):
            raise GovernanceError(
                "GRID_PROTOCOL", "Explicit unique bounded hyperparameter grid required"
            )
        for value in values:
            finite(value, lower=minimum, upper=1e8 if name == "ridge_alphas" else 1e6)
    return digest(protocol)


def loss(model, rows):
    errors = [
        dict(
            row,
            error=abs(
                row["target"] - statistics.predict(model, row["features"], method=row["method"])
            ),
        )
        for row in rows
    ]
    if not errors:
        raise GovernanceError("VALIDATION_EMPTY", "Independent development data required")
    return evaluation.project_mean(errors, "error")


def run(bundle):
    if (
        set(bundle) != {"schema", "origin", "protocol", "members", "rows", "test_start"}
        or bundle["schema"] != "purpose-bound-training-export/1"
        or bundle["origin"] not in {"synthetic", "real"}
    ):
        raise GovernanceError(
            "EXPORT_SCHEMA", "Only server purpose-bound training exports accepted"
        )
    protocol = bundle["protocol"]
    protocol_digest = validate_protocol(protocol)
    members = bundle["members"]
    leakage.check_members(members, test_start=bundle["test_start"])
    allowed = {
        row["id"]: row["purpose"]
        for row in members
        if row["purpose"] in {"fit", "development", "calibration"}
    }
    if {row["id"] for row in bundle["rows"]} != set(allowed) or len(bundle["rows"]) != len(allowed):
        raise GovernanceError(
            "TRAINING_LABEL_ISOLATION",
            "Final labels or omitted training members cannot enter fitting",
        )
    for row in bundle["rows"]:
        if row["purpose"] != allowed[row["id"]]:
            raise GovernanceError(
                "TRAINING_LABEL_ISOLATION", "Purpose changed after frozen membership"
            )
        member = next(m for m in members if m["id"] == row["id"])
        if row["project_id"] != member["project_id"] or row["group_id"] != member["group_id"]:
            raise GovernanceError(
                "GROUP_IDENTITY_CHANGED", "Statistical weight and folds require frozen identity"
            )
    rows = bundle["rows"]
    statistics.checked_rows(rows)
    proper = [row for row in rows if row["purpose"] == "fit"]
    development = [row for row in rows if row["purpose"] == "development"]
    calibration = [row for row in rows if row["purpose"] == "calibration"]
    assignments = leakage.group_folds(proper, count=protocol["fold_count"], seed=protocol["seed"])
    candidates = []
    for family in protocol["families"]:
        grid = (
            [{"alpha": value} for value in protocol["ridge_alphas"]]
            if family == "ridge"
            else [{"shrinkage": value} for value in protocol["shrinkage_values"]]
            if family == "method_shrinkage"
            else [{}]
        )
        fits = []
        for index, parameters in enumerate(grid):
            out_of_fold: list[dict] = []
            for fold in range(protocol["fold_count"]):
                fit_rows = [r for r in proper if assignments[r["id"]] != fold]
                holdout = [r for r in proper if assignments[r["id"]] == fold]
                fitted = statistics.fit(fit_rows, family, **parameters)
                out_of_fold.extend(
                    dict(
                        r,
                        error=abs(
                            r["target"]
                            - statistics.predict(fitted, r["features"], method=r["method"])
                        ),
                    )
                    for r in holdout
                )
            fits.append(
                {
                    "parameters": parameters,
                    "cross_validated_project_mae": evaluation.project_mean(out_of_fold, "error"),
                    "grid_index": index,
                }
            )
        selected = min(
            fits, key=lambda item: (item["cross_validated_project_mae"], item["grid_index"])
        )
        model = statistics.fit(proper, family, **selected["parameters"])
        candidates.append(
            {
                "family": family,
                "parameters": selected["parameters"],
                "inner_candidates": fits,
                "development_project_mae": loss(model, development),
            }
        )
    winner = min(
        candidates,
        key=lambda item: (
            item["development_project_mae"],
            protocol["tie_order"].index(item["family"]),
        ),
    )
    final_rows = proper + development if protocol["refit_with_development"] else proper
    model = statistics.fit(final_rows, winner["family"], **winner["parameters"])
    intervals = evaluation.conformal(model, calibration, levels=protocol["interval_levels"])
    baselines = {
        family: statistics.fit(
            final_rows,
            family,
            **(
                {"shrinkage": protocol["shrinkage_values"][0]}
                if family == "method_shrinkage"
                else {}
            ),
        )
        for family in ("mean", "median", "method_shrinkage", "raw_band")
    }
    result = {
        "schema": "frozen-training-result/1",
        "origin": bundle["origin"],
        "research_only": bundle["origin"] == "synthetic",
        "formal_eligibility": "unavailable",
        "protocol_digest": protocol_digest,
        "training_export_digest": digest(bundle),
        "model": model,
        "model_digest": digest(model),
        "intervals": intervals,
        "baselines": baselines,
        "selection": {
            "candidates": candidates,
            "winner": winner["family"],
            "fold_assignments": assignments,
            "refit_with_development": protocol["refit_with_development"],
        },
        "reasons": [
            "Independent real-label evaluation, agreed acceptance and observation are required"
        ],
    }
    return result
