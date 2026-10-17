"""Actual independent denominator routes only; no main/live or model qualification."""

from copy import deepcopy

from packages.model_governance.contracts import obj, ref


def extend_openapi(actual):
    from apps.api.denominator_routes import CoverageRequest, DenominatorRequest
    from packages.model_governance.scale_contracts import extend_openapi as scales
    from packages.model_governance.scoring_contracts import extend_openapi as scoring

    actual = scales(scoring(actual))
    schemas = actual["components"]["schemas"]
    for model in (CoverageRequest, DenominatorRequest):
        schemas[model.__name__] = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    uuid = {"type": "string", "format": "uuid"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    string = {"type": "string"}
    boolean = {"type": "boolean"}
    null = {"type": "null"}
    integer = {"type": "integer", "minimum": 0}
    date = {"type": "string", "format": "date-time"}
    decimal = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?$"}

    def nullable(value):
        return {"anyOf": [value, null]}

    flags = {
        "formal_eligibility": {"const": "unavailable"},
        "paid_execution_enabled": {"const": False},
    }
    schemas["ScoreDenominatorContext"] = obj(
        {
            "target_kind": {"enum": ["score_input", "historical_input", "stagea_member"]},
            "target_id": uuid,
            "sample_id": nullable(uuid),
            "bid_id": uuid,
            "input_digest": sha,
            "method_digest": sha,
            "scale_authority": nullable(ref("TechnicalScaleView")),
        }
        | flags
    )
    schemas["ScoreDenominatorView"] = obj(
        {
            "id": uuid,
            **dict.fromkeys(
                [
                    "score_input_id",
                    "historical_input_id",
                    "cohort_id",
                    "sample_id",
                    "scale_version_id",
                    "scale_decision_id",
                ],
                nullable(uuid),
            ),
            "scale_head_revision": nullable({"type": "integer", "minimum": 1}),
            "scope_digest": sha,
            "recorded_maximum": nullable(decimal),
            "technical_maximum": nullable(decimal),
            "scale_description": nullable(string),
            "recorded_status": {"enum": ["usable", "unavailable", "conflict"]},
            "reason": string,
            "created_at": date,
            "current": boolean,
        }
        | flags
    )
    strings = {"type": "array", "items": string}
    unavailable = obj(
        {
            "schema": {"const": "complete-three-band-features/1"},
            "status": {"const": "unavailable"},
            "values": null,
            "reasons": strings,
        }
    )
    complete = obj(
        {
            "schema": {"const": "complete-three-band-features/1"},
            "status": {"const": "complete"},
            "feature_names": {
                "const": ["raw_percent", "modal_share", "spread_percent", "evidence_share"]
            },
            "values": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
            "technical_maximum": decimal,
            "rule_details": {
                "type": "array",
                "items": obj(
                    {
                        "rule_id": uuid,
                        "maximum": decimal,
                        "raw_points": decimal,
                        "band_counts": {
                            "type": "object",
                            "additionalProperties": {"type": "integer", "minimum": 1},
                        },
                        "call_ids": {"type": "array", "items": uuid, "minItems": 3, "maxItems": 3},
                    }
                ),
            },
            "reasons": strings,
            "input_digest": sha,
        }
    )
    coverage = obj(
        {
            "valid_rules": integer,
            "necessary_rules": integer,
            "covered_rule_maximum": decimal,
            "necessary_rule_maximum": decimal,
            "technical_maximum": nullable(decimal),
            "fraction": nullable(decimal),
            "status": {"enum": ["complete", "partial", "unavailable"]},
        }
    )
    item = deepcopy(schemas["ScoreResultView"]["properties"]["items"])
    document = obj(
        {
            "schema": {"const": "sourced-score-coverage/1"},
            "binding_id": uuid,
            "source_result_digest": sha,
            "method_digest": sha,
            "technical_maximum": nullable(decimal),
            "scale_description": nullable(string),
            "coverage": coverage,
            "items": item,
            "features": {"oneOf": [complete, unavailable]},
            "prediction": obj({"mode": {"const": "unavailable"}, "value": null, "intervals": null}),
            "formal_eligibility": {"const": "unavailable"},
            "reasons": strings,
        }
    )
    document["required"].remove("features")
    document["required"].remove("prediction")
    schemas["SourcedScoreCoverageView"] = obj(
        {
            "id": uuid,
            "binding_id": uuid,
            "job_id": uuid,
            "digest": sha,
            "created_at": date,
            "recorded_document": document,
            "historical_snapshot": {"const": True},
            "prediction_hidden": boolean,
            "current": boolean,
            "technical_maximum": nullable(decimal),
        }
        | flags
    )
    schemas["SourcedScoreCoverageView"]["allOf"] = [
        {
            "if": {"properties": {"prediction_hidden": {"const": True}}},
            "then": {
                "properties": {
                    "recorded_document": {
                        "not": {"anyOf": [{"required": ["features"]}, {"required": ["prediction"]}]}
                    }
                }
            },
            "else": {"properties": {"recorded_document": {"required": ["features", "prediction"]}}},
        }
    ]
    for prefix in (
        "/score-inputs/{input_id}",
        "/historical-inputs/{input_id}",
        "/model-governance/cohorts/{cohort_id}/members/{sample_id}",
    ):
        for method, suffix, name, status in (
            ("get", "/denominator-context", "ScoreDenominatorContext", 200),
            ("post", "/denominators", "ScoreDenominatorView", 201),
        ):
            _bind(actual, method, "/api/v1" + prefix + suffix, name, status)
    for method, path, name, status in (
        ("get", "/score-denominators/{binding_id}", "ScoreDenominatorView", 200),
        ("post", "/score-denominators/{binding_id}/coverages", "SourcedScoreCoverageView", 201),
        ("get", "/score-coverages/{coverage_id}", "SourcedScoreCoverageView", 200),
    ):
        _bind(actual, method, "/api/v1" + path, name, status)
    return actual


def _bind(actual, method, path, name, status):
    operation = actual.get("paths", {}).get(path, {}).get(method)
    if operation is not None:
        operation["security"] = [{"SessionCookie": []}]
        operation["responses"] = {
            str(status): {
                "description": "Independent sourced denominator reference or immutable derived coverage",
                "content": {"application/json": {"schema": ref(name)}},
            }
        }
