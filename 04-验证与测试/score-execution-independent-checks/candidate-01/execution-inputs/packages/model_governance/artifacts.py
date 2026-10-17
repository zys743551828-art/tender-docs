"""Bounded inert JSON model artifacts and same-input, no-fee shadow calculations.

This module has no runtime/provider/storage imports. A caller must obtain the document
and current qualification from trusted server authority; a JSON document is not authority.
"""

import hashlib
import json
from decimal import Decimal
from uuid import UUID

from packages.model_governance.common import GovernanceError, decimal, digest, finite
from packages.model_governance.features import FEATURES, SCHEMA
from packages.model_governance.statistics import predict, validate_model

MEDIA = "application/vnd.tender.score-model+json"
MAX_BYTES = 2 * 1024 * 1024
KEYS = {
    "schema",
    "model_id",
    "training_job_id",
    "training_result_digest",
    "cohort_digest",
    "protocol_digest",
    "judgment_method_digest",
    "feature_schema",
    "origin",
    "research_only",
    "formal_eligibility",
    "scope",
    "model",
    "model_digest",
    "intervals",
    "baselines",
}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GovernanceError("ARTIFACT_DUPLICATE_KEY", "Duplicate JSON object member")
        result[key] = value
    return result


def _constant(value):
    raise GovernanceError("ARTIFACT_NONFINITE", "Nonfinite JSON constants are not supported")


def _sha(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise GovernanceError("ARTIFACT_IDENTITY", "Exact SHA256 identity required")


def validate(document):
    try:
        if set(document) != KEYS or document["schema"] != "registered-score-artifact/1":
            raise GovernanceError("ARTIFACT_SCHEMA", "Closed inert model artifact required")
        UUID(document["model_id"])
        UUID(document["training_job_id"])
        for key in (
            "training_result_digest",
            "cohort_digest",
            "protocol_digest",
            "judgment_method_digest",
            "model_digest",
        ):
            _sha(document[key])
        if (
            document["feature_schema"] != SCHEMA
            or document["origin"] not in {"synthetic", "real"}
            or type(document["research_only"]) is not bool
            or document["research_only"] != (document["origin"] == "synthetic")
            or document["formal_eligibility"] != "unavailable"
        ):
            raise GovernanceError(
                "ARTIFACT_PURPOSE", "Registration cannot grant business qualification"
            )
        scope = document["scope"]
        if (
            set(scope)
            != {"judgment_method_digest", "feature_schema", "engineering_types", "maximums"}
            or scope["judgment_method_digest"] != document["judgment_method_digest"]
            or scope["feature_schema"] != SCHEMA
            or not isinstance(scope["engineering_types"], list)
            or not scope["engineering_types"]
            or len(scope["engineering_types"]) > 10000
            or any(
                v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 500)
                for v in scope["engineering_types"]
            )
            or len(set(scope["engineering_types"])) != len(scope["engineering_types"])
            or not isinstance(scope["maximums"], list)
            or not scope["maximums"]
            or len(scope["maximums"]) > 10000
        ):
            raise GovernanceError("ARTIFACT_SCOPE", "Server-derived finite scope required")
        maxima = [decimal(value) for value in scope["maximums"]]
        if any(value <= 0 for value in maxima) or len(set(maxima)) != len(maxima):
            raise GovernanceError("ARTIFACT_SCOPE", "Unique positive verified maxima required")
        validate_model(document["model"])
        if digest(document["model"]) != document["model_digest"]:
            raise GovernanceError("ARTIFACT_MODEL_DIGEST", "Statistical model identity mismatch")
        if set(document["baselines"]) != {"mean", "median", "method_shrinkage", "raw_band"}:
            raise GovernanceError("ARTIFACT_BASELINES", "Complete frozen baseline family required")
        for name, baseline in document["baselines"].items():
            validate_model(baseline)
            if baseline["family"] != name:
                raise GovernanceError("ARTIFACT_BASELINES", "Frozen baseline family mismatch")
        if set(document["intervals"]) != {"0.8", "0.9"}:
            raise GovernanceError(
                "ARTIFACT_INTERVALS", "Frozen 80/90 interval specification required"
            )
        for specification in document["intervals"].values():
            if set(specification) != {"radius", "groups", "rank", "method", "unavailable_reason"}:
                raise GovernanceError(
                    "ARTIFACT_INTERVALS", "Closed group interval specification required"
                )
            if (
                type(specification["groups"]) is not int
                or specification["groups"] < 0
                or type(specification["rank"]) is not int
                or specification["rank"] < 1
                or specification["method"] != "group_max_absolute_residual_split_conformal/1"
            ):
                raise GovernanceError(
                    "ARTIFACT_INTERVALS", "Supported group interval identity required"
                )
            if specification["radius"] is None:
                if (
                    specification["unavailable_reason"]
                    != "insufficient_independent_calibration_groups"
                ):
                    raise GovernanceError(
                        "ARTIFACT_INTERVALS", "Unknown interval requires an explicit reason"
                    )
            else:
                finite(specification["radius"], lower=0, upper=100)
                if (
                    specification["rank"] > specification["groups"]
                    or specification["unavailable_reason"] is not None
                ):
                    raise GovernanceError(
                        "ARTIFACT_INTERVALS", "Unsupported finite-sample interval"
                    )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, GovernanceError):
            raise
        raise GovernanceError("ARTIFACT_SCHEMA", "Malformed inert model artifact") from None
    return document


def encode(document):
    validate(document)
    raw = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > MAX_BYTES:
        raise GovernanceError("ARTIFACT_TOO_LARGE", "Artifact exceeds bounded JSON capacity")
    return raw


def load(raw, *, expected_digest, expected_size, expected_document):
    _sha(expected_digest)
    if (
        not isinstance(raw, bytes)
        or type(expected_size) is not int
        or not 0 < expected_size <= MAX_BYTES
        or len(raw) != expected_size
        or hashlib.sha256(raw).hexdigest() != expected_digest
    ):
        raise GovernanceError(
            "ARTIFACT_BYTES", "Actual artifact bytes differ from immutable registry"
        )
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_constant
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, GovernanceError):
            raise
        raise GovernanceError("ARTIFACT_JSON", "Bounded UTF-8 JSON artifact required") from None
    validate(document)
    if digest(document) != digest(expected_document):
        raise GovernanceError(
            "ARTIFACT_REGISTRY_MISMATCH", "Artifact is not the server-registered document"
        )
    return document


def infer(document, features, *, method_digest, engineering_type, technical_maximum, current):
    """Research inference only. Formal mode requires a separate DB-certified producer."""
    validate(document)
    maximum = decimal(technical_maximum)
    if not current:
        raise GovernanceError(
            "MODEL_SOURCE_STALE", "Invalidated training dependencies prohibit new use"
        )
    if (
        method_digest != document["judgment_method_digest"]
        or engineering_type not in document["scope"]["engineering_types"]
        or maximum not in [decimal(v) for v in document["scope"]["maximums"]]
    ):
        raise GovernanceError("MODEL_OUT_OF_SCOPE", "Frozen method, type and scale must match")
    if (
        features.get("schema") != SCHEMA
        or features.get("status") != "complete"
        or features.get("feature_names") != list(FEATURES)
        or features.get("reasons") != []
        or decimal(features.get("technical_maximum")) != maximum
    ):
        return {
            "mode": "unavailable",
            "value": None,
            "maximum": str(maximum),
            "intervals": None,
            "reasons": ["complete_three_judgments_required"],
            "formal_eligibility": "unavailable",
        }
    percentage = predict(document["model"], features["values"], method=method_digest)
    value = Decimal(str(percentage)) * maximum / 100
    intervals = {}
    for level, spec in document["intervals"].items():
        radius = spec["radius"]
        intervals[level] = (
            None
            if radius is None
            else {
                "lower": str(Decimal(str(max(0, percentage - radius))) * maximum / 100),
                "upper": str(Decimal(str(min(100, percentage + radius))) * maximum / 100),
            }
        )
    return {
        "mode": "research_candidate",
        "value": str(value),
        "maximum": str(maximum),
        "intervals": intervals,
        "reasons": ["candidate_not_business_qualified"],
        "formal_eligibility": "unavailable",
    }


def compare(candidate, baseline, *, show_prediction):
    """Do not serialize hidden values, intervals, differences or raw feature vectors."""
    if not show_prediction:
        return {
            "prediction_hidden": True,
            "comparison_status": "hidden",
            "formal_eligibility": "unavailable",
        }
    candidate_ok = candidate.get("value") is not None
    baseline_ok = baseline is not None and baseline.get("value") is not None
    if baseline_ok and decimal(baseline["maximum"]) != decimal(candidate["maximum"]):
        raise GovernanceError(
            "SHADOW_SCALE_MISMATCH", "Same frozen scale required for paired comparison"
        )
    return {
        "prediction_hidden": False,
        "comparison_status": "paired" if candidate_ok and baseline_ok else "unavailable",
        "candidate": candidate,
        "baseline": baseline,
        "difference": str(decimal(candidate["value"]) - decimal(baseline["value"]))
        if candidate_ok and baseline_ok
        else None,
        "formal_eligibility": "unavailable",
    }
