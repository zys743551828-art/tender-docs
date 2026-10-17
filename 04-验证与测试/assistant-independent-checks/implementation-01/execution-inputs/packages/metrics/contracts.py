"""Actual FR011 schema additions; reviewed target contracts remain unchanged."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError
from packages.metrics.core import STAGES, VERIFICATIONS

ID = {"type": "string", "format": "uuid"}
TIME = {"type": "string", "format": "date-time"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"}
NULLID = {"type": ["string", "null"], "format": "uuid"}
NULLTIME = {"type": ["string", "null"], "format": "date-time"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "required": list(properties) if required is None else required,
    }


REF = obj(
    {
        "document_version_id": ID,
        "evidence_id": ID,
        "physical_page": {"type": "integer", "minimum": 1},
        "quote": TEXT,
    }
)
FIELDS = {
    "submission_id": ID,
    "stage": {"enum": list(STAGES)},
    "source_type": {"enum": ["user_reported", "official"]},
    "source_version_id": NULLID,
    "evidence_refs": {"type": "array", "items": REF, "maxItems": 100},
    "announced_at": NULLTIME,
    "known_at": TIME,
    "reason": TEXT,
}
SCHEMAS = {
    "BusinessResultRequest": obj(FIELDS),
    "BusinessResultCorrectionRequest": obj(
        FIELDS | {"base_result_id": ID, "correction_reason": TEXT}
    ),
    "BusinessResultVerificationRequest": obj(
        {
            "state": {"enum": list(VERIFICATIONS)},
            "reason": TEXT,
            "identity_confirmed": {"type": "boolean"},
            "evidence_refs": {"type": "array", "items": REF, "maxItems": 100},
        },
        ["state", "reason"],
    ),
}


def validate(name, body):
    errors = list(
        Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).iter_errors(body)
    )
    if errors:
        raise DomainError(
            "INVALID_REQUEST",
            "请核对实际结果、来源及时间字段",
            422,
            {
                "fields": [
                    {"path": ".".join(map(str, e.absolute_path)), "constraint": e.validator}
                    for e in errors[:20]
                ]
            },
        )


def _extend_requests(actual):
    actual["components"]["schemas"].update(deepcopy(SCHEMAS))
    mapping = {
        (
            "/api/v1/bids/{bid_id}/submissions/{submission_id}/business-results",
            "post",
        ): "BusinessResultRequest",
        (
            "/api/v1/business-results/{result_id}/corrections",
            "post",
        ): "BusinessResultCorrectionRequest",
        (
            "/api/v1/business-results/{result_id}/verification-decisions",
            "post",
        ): "BusinessResultVerificationRequest",
    }
    for (path, method), name in mapping.items():
        if path in actual["paths"]:
            actual["paths"][path][method]["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                },
            }
    return actual


# Explicit actual read models; old target MetricsView is not reused for the richer ledger.
BOOL = {"type": "boolean"}
COUNT = {"type": "integer", "minimum": 0}
NULLCOUNT = {"type": ["integer", "null"], "minimum": 0}
NULLTEXT = {"type": ["string", "null"]}
STRINGS = {"type": "array", "items": {"type": "string"}}
COUNTS = {"type": "object", "additionalProperties": COUNT}
SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
BUSINESS_VIEW = obj(
    FIELDS
    | {
        "id": ID,
        "bid_id": ID,
        "supersedes_id": NULLID,
        "correction_reason": NULLTEXT,
        "created_by": ID,
        "created_at": TIME,
        "verification": {"enum": list(VERIFICATIONS)},
        "verification_authority_id": NULLID,
        "verification_details": {
            "anyOf": [SCHEMAS["BusinessResultVerificationRequest"], {"type": "null"}]
        },
        "head_revision": COUNT,
        "head_authority_id": NULLID,
        "current": BOOL,
        "usable": BOOL,
        "unusable_reasons": STRINGS,
    }
)
HEAD = obj({"result_id": NULLID, "revision": COUNT, "authority_id": NULLID})
RATIO = obj({"numerator": COUNT, "denominator": COUNT, "value": NULLTEXT})
ONLINE = obj(
    {
        "prediction_pairs": COUNT,
        "needed_pairs": {"const": 20},
        "online_error_state": {"enum": ["no_pairs", "insufficient", "sufficient"]},
        "mae": NULLTEXT,
        "bias": NULLTEXT,
        "excluded_pair_counts": COUNTS,
        "scope_note": {"type": "string"},
    }
)
SCOPE = obj(
    {
        "period_from": NULLTIME,
        "period_to": NULLTIME,
        "as_of": TIME,
        "bid_ids": {"anyOf": [{"type": "array", "items": ID}, {"type": "null"}]},
        "statistical_unit": {"const": "engineering_lot_round_bidder_current_actual_submission"},
        "verification_scope": {"enum": ["ledger", "verified_only"]},
        "definition": {"const": "fr011-ledger/v1"},
        "observed_current_heads": COUNT,
        "effective_head_digest": SHA,
        "head_authorities": {
            "type": "array",
            "items": obj(
                {
                    "bid_id": ID,
                    "submission_id": ID,
                    "submission_revision": COUNT,
                    "result_head": HEAD,
                    "result_usable": {"type": ["boolean", "null"]},
                    "result_exclusions": STRINGS,
                }
            ),
        },
        "excluded_submission_counts": COUNTS,
        "identity_submission_conflicts": {
            "type": "array",
            "items": obj(
                {
                    "unit": {"type": "array", "items": ID, "minItems": 3, "maxItems": 3},
                    "submission_ids": {"type": "array", "items": ID},
                }
            ),
        },
        "scope_note": {"type": "string"},
    }
)
QUALIFICATION = obj(
    {
        **{
            key: NULLCOUNT
            for key in ("identified", "resolved", "dismissed", "open", "open_at_submission")
        },
        **{
            key: COUNT
            for key in (
                "known_snapshot_submissions",
                "unknown_snapshot_submissions",
                "official_rejections",
                "known_current_review_submissions",
                "unknown_current_review_submissions",
            )
        },
        "scope_note": {"type": "string"},
        "official_rejection_causality": {"const": "not_inferred"},
    }
)
PREDICTION = {
    "oneOf": [
        obj({"mode": {"enum": ["hidden", "partial", "unavailable"]}, "reasons": STRINGS}),
        obj(
            {
                "id": ID,
                "mode": {"enum": ["raw_uncalibrated", "accepted"]},
                "value": NULLTEXT,
                "maximum": NULLTEXT,
                "lower_bound": NULLTEXT,
                "upper_bound": NULLTEXT,
                "reasons": STRINGS,
            }
        ),
    ]
}
OFFICIAL = obj(
    {
        "id": ID,
        "bid_id": ID,
        "submission_id": ID,
        "score_kind": {"const": "technical_total"},
        "value": NULLTEXT,
        "maximum": NULLTEXT,
        "source_version_id": ID,
        "known_at": TIME,
        "verification": {"const": "verified"},
        "verification_revision": COUNT,
        "supersedes_id": NULLID,
        "verification_details": {"type": ["object", "null"]},
    }
)
POINT = obj(
    {
        "review_id": ID,
        "body_version_id": ID,
        "rule_set_id": NULLID,
        "fact_set_id": NULLID,
        "input_digest": SHA,
        "manifest_digest": SHA,
        "method_digest": SHA,
        "created_at": TIME,
        "body_changed": BOOL,
        "review_state": {"type": "string"},
        "core_coverage": {"type": ["string", "null"]},
        "risk_counts": {"anyOf": [COUNTS, {"type": "null"}]},
        "prediction": PREDICTION,
        "official_technical_score": {"anyOf": [OFFICIAL, {"type": "null"}]},
        "comparable_to_previous": BOOL,
        "break_reasons": STRINGS,
    }
)
SCHEMAS.update(
    {
        "BusinessResultView": BUSINESS_VIEW,
        "BusinessResultPage": obj(
            {
                "bid_id": ID,
                "submission_id": ID,
                "current_result": {"anyOf": [BUSINESS_VIEW, {"type": "null"}]},
                "head_revision": COUNT,
                "head_authority_id": NULLID,
                "items": {"type": "array", "items": BUSINESS_VIEW},
                "next_offset": NULLCOUNT,
                "total": COUNT,
            }
        ),
        "BusinessMetricsView": obj(
            {
                "scope": SCOPE,
                **{
                    key: COUNT
                    for key in (
                        "effective_submissions",
                        "known_outcomes",
                        "unknown_outcomes",
                        "conflicted_outcomes",
                        "unusable_outcomes",
                    )
                },
                "stage_counts": COUNTS,
                "verification_counts": COUNTS,
                "excluded_result_counts": COUNTS,
                "shortlist_rate": RATIO,
                "win_rate": RATIO,
                "qualification": QUALIFICATION,
                "online_error": ONLINE,
                "model_qualification_granted": {"const": False},
            }
        ),
        "BusinessRoundTrendPage": obj(
            {
                "bid_id": ID,
                "items": {"type": "array", "items": POINT},
                "next_after": NULLID,
                "total_reviews": COUNT,
                "prediction_hidden": BOOL,
                "scope_note": {"type": "string"},
            }
        ),
    }
)

_REQUEST_EXTENSION = _extend_requests


def extend_openapi(actual):
    _REQUEST_EXTENSION(actual)
    mappings = {
        ("/api/v1/bids/{bid_id}/submissions/{submission_id}/business-results", "get"): (
            200,
            "BusinessResultPage",
        ),
        ("/api/v1/bids/{bid_id}/submissions/{submission_id}/business-results", "post"): (
            201,
            "BusinessResultView",
        ),
        ("/api/v1/business-results/{result_id}", "get"): (200, "BusinessResultView"),
        ("/api/v1/business-results/{result_id}/corrections", "post"): (201, "BusinessResultView"),
        ("/api/v1/business-results/{result_id}/verification-decisions", "post"): (
            200,
            "BusinessResultView",
        ),
        ("/api/v1/metrics", "get"): (200, "BusinessMetricsView"),
        ("/api/v1/bids/{bid_id}/round-trends", "get"): (200, "BusinessRoundTrendPage"),
    }
    for (path, method), (status, name) in mappings.items():
        if path in actual["paths"]:
            operation = actual["paths"][path][method]
            if status != 200:
                operation["responses"].pop("200", None)
            operation["responses"][str(status)] = {
                "description": "实际FR011持久化与口径投影",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                },
            }
    return actual
