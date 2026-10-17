"""Bounded JSON-only statistical candidates; no labels or preprocessing from final test."""

from collections import Counter, defaultdict
from math import fsum, sqrt

from packages.model_governance.common import GovernanceError, digest, finite
from packages.model_governance.features import FEATURES, SCHEMA

FAMILIES = ("mean", "median", "method_shrinkage", "raw_band", "ridge", "isotonic")


def checked_rows(rows):
    if not rows or len(rows) > 10000:
        raise GovernanceError("ROW_COUNT", "Between 1 and 10000 observations required")
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise GovernanceError("DUPLICATE_OBSERVATION", "Duplicate observation")
        seen.add(row["id"])
        if len(row["features"]) != len(FEATURES):
            raise GovernanceError("FEATURE_SCHEMA", "Fixed complete feature vector required")
        for value, upper in zip(row["features"], (100, 1, 100, 1), strict=True):
            finite(value, lower=0, upper=upper)
        finite(row["target"], lower=0, upper=100)
        if not row["project_id"] or not row["group_id"] or not row["method"]:
            raise GovernanceError("IDENTITY_REQUIRED", "Frozen grouping and method required")
    counts = Counter(row["project_id"] for row in rows)
    return [1 / counts[row["project_id"]] for row in rows]


def weighted_mean(values, weights):
    return fsum(v * w for v, w in zip(values, weights, strict=True)) / fsum(weights)


def solve(matrix, vector):
    a = [list(row) + [value] for row, value in zip(matrix, vector, strict=True)]
    n = len(a)
    for index in range(n):
        pivot = max(range(index, n), key=lambda k: abs(a[k][index]))
        a[index], a[pivot] = a[pivot], a[index]
        if abs(a[index][index]) < 1e-12:
            raise GovernanceError("SINGULAR_FIT", "Numerically unidentified model")
        divisor = a[index][index]
        a[index] = [v / divisor for v in a[index]]
        for other in range(n):
            if other == index:
                continue
            factor = a[other][index]
            a[other] = [x - factor * y for x, y in zip(a[other], a[index], strict=True)]
    return [row[-1] for row in a]


def fit(rows, family, *, alpha=None, shrinkage=None):
    weights = checked_rows(rows)
    if family not in FAMILIES:
        raise GovernanceError("UNSUPPORTED_FAMILY", "Candidate family not in frozen protocol")
    targets = [finite(row["target"]) for row in rows]
    mean = weighted_mean(targets, weights)
    model = {
        "schema": "bounded-score-model/1",
        "feature_schema": SCHEMA,
        "feature_names": list(FEATURES),
        "family": family,
        "parameters": {},
        "training_digest": digest(rows),
    }
    parameters = model["parameters"]
    if family == "mean":
        parameters["constant"] = mean
    elif family == "median":
        pairs = sorted(zip(targets, weights, strict=True))
        half, cumulative = fsum(weights) / 2, 0.0
        for index, (target, weight) in enumerate(pairs):
            cumulative += weight
            if cumulative >= half:
                parameters["constant"] = (
                    (target + pairs[index + 1][0]) / 2
                    if abs(cumulative - half) < 1e-12 and index + 1 < len(pairs)
                    else target
                )
                break
    elif family == "method_shrinkage":
        strength = finite(shrinkage, lower=0, upper=1e6)
        groups = defaultdict(list)
        for row, weight in zip(rows, weights, strict=True):
            groups[row["method"]].append((row["target"], weight))
        parameters.update(
            constant=mean,
            shrinkage=strength,
            methods={
                key: (fsum(t * w for t, w in observations) + strength * mean)
                / (fsum(w for _, w in observations) + strength)
                for key, observations in sorted(groups.items())
            },
        )
    elif family == "raw_band":
        pass
    elif family == "ridge":
        regularization = finite(alpha, lower=1e-8, upper=1e8)
        dimensions = len(FEATURES)
        means = [
            weighted_mean([r["features"][j] for r in rows], weights) for j in range(dimensions)
        ]
        scales = [
            sqrt(weighted_mean([(r["features"][j] - means[j]) ** 2 for r in rows], weights)) or 1.0
            for j in range(dimensions)
        ]
        x = [
            [(row["features"][j] - means[j]) / scales[j] for j in range(dimensions)] for row in rows
        ]
        matrix = [
            [
                fsum(w * vector[i] * vector[j] for vector, w in zip(x, weights, strict=True))
                + (regularization if i == j else 0)
                for j in range(dimensions)
            ]
            for i in range(dimensions)
        ]
        rhs = [
            fsum(
                w * vector[i] * (target - mean)
                for vector, target, w in zip(x, targets, weights, strict=True)
            )
            for i in range(dimensions)
        ]
        parameters.update(
            alpha=regularization,
            means=means,
            scales=scales,
            coefficients=solve(matrix, rhs),
            intercept=mean,
        )
    elif family == "isotonic":
        points = defaultdict(list)
        for row, weight in zip(rows, weights, strict=True):
            points[row["features"][0]].append((row["target"], weight))
        blocks = []
        for x, pairs in sorted(points.items()):
            block = {
                "left": x,
                "right": x,
                "weight": fsum(w for _, w in pairs),
                "total": fsum(y * w for y, w in pairs),
            }
            blocks.append(block)
            while (
                len(blocks) >= 2
                and blocks[-2]["total"] / blocks[-2]["weight"]
                > blocks[-1]["total"] / blocks[-1]["weight"]
            ):
                last, previous = blocks.pop(), blocks.pop()
                blocks.append(
                    {
                        "left": previous["left"],
                        "right": last["right"],
                        "weight": previous["weight"] + last["weight"],
                        "total": previous["total"] + last["total"],
                    }
                )
        # PAVA block endpoints; piecewise linear interpolation is part of this method version.
        parameters["knots"] = []
        for block in blocks:
            y = block["total"] / block["weight"]
            parameters["knots"].append([block["left"], y])
            if block["right"] != block["left"]:
                parameters["knots"].append([block["right"], y])
    validate_model(model)
    return model


def validate_model(model):
    if (
        set(model)
        != {"schema", "feature_schema", "feature_names", "family", "parameters", "training_digest"}
        or model["schema"] != "bounded-score-model/1"
        or model["feature_schema"] != SCHEMA
        or model["feature_names"] != list(FEATURES)
        or model["family"] not in FAMILIES
    ):
        raise GovernanceError("MODEL_SCHEMA", "Only supported bounded JSON models are loadable")
    p, family = model["parameters"], model["family"]
    fields = {
        "mean": {"constant"},
        "median": {"constant"},
        "method_shrinkage": {"constant", "shrinkage", "methods"},
        "raw_band": set(),
        "ridge": {"alpha", "means", "scales", "coefficients", "intercept"},
        "isotonic": {"knots"},
    }[family]
    if set(p) != fields:
        raise GovernanceError("MODEL_SCHEMA", "Unexpected or executable model parameter")
    if family in {"mean", "median", "method_shrinkage"}:
        finite(p["constant"], lower=0, upper=100)
    if family == "method_shrinkage":
        finite(p["shrinkage"], lower=0, upper=1e6)
        if not isinstance(p["methods"], dict) or len(p["methods"]) > 1000:
            raise GovernanceError("MODEL_SCHEMA", "Bounded method vocabulary required")
        for value in p["methods"].values():
            finite(value, lower=0, upper=100)
    if family == "ridge":
        finite(p["alpha"], lower=1e-8, upper=1e8)
        for key in ("means", "scales", "coefficients"):
            if len(p[key]) != len(FEATURES):
                raise GovernanceError("MODEL_SCHEMA", "Wrong model dimensions")
            for value in p[key]:
                finite(value, lower=-1e12, upper=1e12)
        if any(v <= 0 for v in p["scales"]):
            raise GovernanceError("MODEL_SCHEMA", "Positive training scales required")
        finite(p["intercept"], lower=0, upper=100)
    if family == "isotonic":
        knots = p["knots"]
        if not 1 <= len(knots) <= 10000:
            raise GovernanceError("MODEL_SCHEMA", "Bounded monotone knots required")
        for pair in knots:
            if len(pair) != 2:
                raise GovernanceError("MODEL_SCHEMA", "Invalid monotone knot")
            for number in pair:
                finite(number, lower=0, upper=100)
        if any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(knots, knots[1:], strict=False)):
            raise GovernanceError("MODEL_SCHEMA", "Knots must be increasing and monotone")
    digest(model)  # Reject non-JSON and non-finite nested values.


def predict(model, features, *, method):
    validate_model(model)
    if len(features) != len(FEATURES):
        raise GovernanceError("FEATURE_SCHEMA", "Wrong prediction vector dimensions")
    features = [
        finite(v, lower=0, upper=upper) for v, upper in zip(features, (100, 1, 100, 1), strict=True)
    ]
    p, family = model["parameters"], model["family"]
    if family in {"mean", "median"}:
        result = p["constant"]
    elif family == "method_shrinkage":
        result = p["methods"].get(method, p["constant"])
    elif family == "raw_band":
        result = features[0]
    elif family == "ridge":
        result = p["intercept"] + fsum(
            coefficient * (value - mean) / scale
            for coefficient, value, mean, scale in zip(
                p["coefficients"], features, p["means"], p["scales"], strict=True
            )
        )
    else:
        knots, x = p["knots"], features[0]
        result = knots[-1][1]
        if x <= knots[0][0]:
            result = knots[0][1]
        else:
            for left, right in zip(knots, knots[1:], strict=False):
                if x <= right[0]:
                    result = left[1] + (right[1] - left[1]) * (x - left[0]) / (right[0] - left[0])
                    break
    return min(100.0, max(0.0, finite(result)))
