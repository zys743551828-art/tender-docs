"""Group conformal intervals and frozen-denominator evaluation; nominal != measured coverage."""

from collections import Counter, defaultdict
from math import ceil, fsum
from random import Random

from packages.model_governance.common import GovernanceError, finite
from packages.model_governance.statistics import predict


def quantile(values, probability):
    if not values or not 0 <= probability <= 1:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(probability * len(ordered)) - 1)]


def conformal(model, rows, *, levels):
    groups = defaultdict(list)
    for row in rows:
        if row["purpose"] != "calibration":
            raise GovernanceError("CALIBRATION_LEAK", "Independent calibration rows only")
        residual = abs(
            finite(row["target"], lower=0, upper=100)
            - predict(model, row["features"], method=row["method"])
        )
        groups[row["group_id"]].append(residual)
    maxima = sorted(max(values) for values in groups.values())
    result = {}
    for level in levels:
        level = finite(level, lower=0.01, upper=0.99)
        rank = ceil((len(maxima) + 1) * level)
        result[str(level)] = {
            "radius": maxima[rank - 1] if maxima and rank <= len(maxima) else None,
            "groups": len(maxima),
            "rank": rank,
            "method": "group_max_absolute_residual_split_conformal/1",
            "unavailable_reason": "insufficient_independent_calibration_groups"
            if not maxima or rank > len(maxima)
            else None,
        }
    return result


def project_mean(rows, value_key):
    by_project = defaultdict(list)
    for row in rows:
        by_project[row["project_id"]].append(row[value_key])
    return (
        fsum(fsum(v) / len(v) for v in by_project.values()) / len(by_project)
        if by_project
        else None
    )


def metrics(
    rows, *, expected_ids, intervals, seed, bootstrap_iterations, baseline_predictions=None
):
    if (
        len(expected_ids) != len(set(expected_ids))
        or len(rows) != len({r["id"] for r in rows})
        or {r["id"] for r in rows} != set(expected_ids)
    ):
        raise GovernanceError(
            "DENOMINATOR_MISMATCH",
            "Every frozen member requires an explicit success or failure record",
        )
    if type(bootstrap_iterations) is not int or not 100 <= bootstrap_iterations <= 10000:
        raise GovernanceError(
            "BOOTSTRAP_PROTOCOL", "Explicit bounded bootstrap iterations required"
        )
    valid = []
    statuses: Counter[str] = Counter()
    for row in rows:
        if row["status"] not in {"succeeded", "failed", "rejected", "unlabeled"}:
            raise GovernanceError("STATUS_INVALID", "Explicit denominator status required")
        statuses[row["status"]] += 1
        if row["status"] != "succeeded":
            continue
        target = finite(row["target"], lower=0, upper=100)
        value = finite(row["prediction"], lower=0, upper=100)
        maximum = finite(row["maximum"], lower=1e-9, upper=1e9)
        error = value - target
        valid.append(
            dict(
                row,
                absolute_error=abs(error),
                error=error,
                within_three=float(abs(error) <= 3),
                original_absolute_error=abs(error) * maximum / 100,
            )
        )
    summary = {
        "denominator": len(rows),
        "counts": {key: statuses[key] for key in ("succeeded", "failed", "rejected", "unlabeled")},
        "success_coverage": len(valid) / len(rows) if rows else None,
        "independent_groups": len({r["group_id"] for r in valid}),
        "projects": len({r["project_id"] for r in valid}),
        "sample_mae": fsum(r["absolute_error"] for r in valid) / len(valid) if valid else None,
        "project_mae": project_mean(valid, "absolute_error"),
        "original_scale_project_mae": project_mean(valid, "original_absolute_error"),
        "p90_absolute_error": quantile([r["absolute_error"] for r in valid], 0.9),
        "project_bias": project_mean(valid, "error"),
        "project_within_three": project_mean(valid, "within_three"),
        "intervals": {},
        "paired_baselines": {},
        "group_bootstrap_project_mae_95": None,
        "stability_range_p90": None,
    }
    for level, specification in intervals.items():
        radius = specification["radius"]
        if radius is None or not valid:
            summary["intervals"][level] = {
                "coverage": None,
                "width": None,
                "reason": "interval_or_labeled_predictions_unavailable",
            }
            continue
        radius = finite(radius, lower=0, upper=100)
        covered = [
            dict(
                r,
                covered=float(
                    max(0, r["prediction"] - radius)
                    <= r["target"]
                    <= min(100, r["prediction"] + radius)
                ),
                width=min(100, r["prediction"] + radius) - max(0, r["prediction"] - radius),
            )
            for r in valid
        ]
        summary["intervals"][level] = {
            "coverage": project_mean(covered, "covered"),
            "width": project_mean(covered, "width"),
        }
    if valid:
        by_group = defaultdict(list)
        for row in valid:
            by_group[row["group_id"]].append(row)
        group_ids = sorted(by_group)
        if len(group_ids) >= 2:
            random = Random(seed)  # nosec B311
            values = []
            for _ in range(bootstrap_iterations):
                drawn: list[dict] = []
                for index in range(len(group_ids)):
                    group = random.choice(group_ids)  # nosec B311
                    # Repeated draws remain distinct project copies, preserving equal project weights.
                    drawn.extend(
                        dict(r, project_id=f"{index}:{r['project_id']}") for r in by_group[group]
                    )
                values.append(project_mean(drawn, "absolute_error"))
            summary["group_bootstrap_project_mae_95"] = [
                quantile(values, 0.025),
                quantile(values, 0.975),
            ]
        for name, predictions in (baseline_predictions or {}).items():
            if any(row["id"] not in predictions for row in valid):
                summary["paired_baselines"][name] = {
                    "gain": None,
                    "reason": "paired_baseline_missing",
                }
                continue
            differences = [
                dict(
                    r,
                    gain=abs(finite(predictions[r["id"]], lower=0, upper=100) - r["target"])
                    - r["absolute_error"],
                )
                for r in valid
            ]
            summary["paired_baselines"][name] = {
                "gain": project_mean(differences, "gain"),
                "paired_count": len(differences),
                "group_bootstrap_gain_95": None,
            }
            if len(group_ids) >= 2:
                paired_groups = defaultdict(list)
                for row in differences:
                    paired_groups[row["group_id"]].append(row)
                random = Random(seed)  # nosec B311
                gains = []
                for _ in range(bootstrap_iterations):
                    paired_draw: list[dict] = []
                    for index in range(len(group_ids)):
                        selected = random.choice(group_ids)  # nosec B311
                        paired_draw.extend(
                            dict(row, project_id=f"{index}:{row['project_id']}")
                            for row in paired_groups[selected]
                        )
                    gains.append(project_mean(paired_draw, "gain"))
                summary["paired_baselines"][name]["group_bootstrap_gain_95"] = [
                    quantile(gains, 0.025),
                    quantile(gains, 0.975),
                ]
    summary["strata"] = {}
    for field in ("method", "maximum", "engineering_type"):
        groups = defaultdict(list)
        for row in valid:
            groups[str(row.get(field, "unavailable"))].append(row)
        summary["strata"][field] = {
            key: {"count": len(values), "project_mae": project_mean(values, "absolute_error")}
            for key, values in sorted(groups.items())
        }
    summary["unavailable_statistics"] = [
        "ranking_without_ranking_labels",
        "shortlist_probability_without_labels",
        "judge_disagreement_without_judge_labels",
        "whole_round_stability_without_independent_repeat_predictions",
    ]
    return summary
