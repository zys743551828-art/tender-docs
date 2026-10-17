"""Actual dedicated scoring surface only; no accepted model or paid execution claim."""

from packages.model_governance.contracts import obj, ref


def extend_openapi(actual):
    from apps.api.scoring_routes import (
        ScoreInputRequest,
        ScoreJobChange,
        ScoreJobRequest,
        ShadowRequest,
    )

    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (ScoreInputRequest, ScoreJobChange, ScoreJobRequest, ShadowRequest):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    uuid = {"type": "string", "format": "uuid"}
    date = {"type": "string", "format": "date-time"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    string, boolean, null = {"type": "string"}, {"type": "boolean"}, {"type": "null"}
    integer = {"type": "integer", "minimum": 0}

    def nullable(value):
        return {"anyOf": [value, null]}

    unavailable = {"const": "unavailable"}
    flags = {"formal_eligibility": unavailable, "paid_execution_enabled": {"const": False}}
    strings = {"type": "array", "items": string}
    identity = obj(
        dict.fromkeys(["bidder_id", "round_id", "engineering_project_id"], uuid)
        | dict.fromkeys(["bidder_name", "engineering_title"], string)
        | dict.fromkeys(["region", "engineering_type", "lot"], nullable(string))
        | {
            "round_no": {"type": "integer", "minimum": 1},
            "binding_status": {"enum": ["pending", "confirmed"]},
            "authority_id": nullable(uuid),
            "digest": sha,
        }
    )
    schemas["ScoreCapabilities"] = obj(
        {
            "schema_configured": boolean,
            "complete_judgment_registration": boolean,
            "score_dispatch_configured": {"const": False},
            "numeric_prediction_available": {"const": False},
        }
        | flags
    )
    schemas["ScoreInputContext"] = obj(
        {
            "bid_id": uuid,
            **dict.fromkeys(
                [
                    "current_body_id",
                    "current_material_id",
                    "current_rule_set_id",
                    "current_review_id",
                ],
                nullable(uuid),
            ),
            "phase": {"enum": ["preparation", "working", "submitted", "closed", "terminated"]},
            "archived_at": nullable(date),
            "identity": identity,
            "judgment_method_digest": sha,
            "price_config_digest": sha,
            "full_chapters_required": {"const": True},
            "valid_replicates_required": {"const": 3},
        }
        | flags
    )
    schemas["ScoreInputView"] = obj(
        {
            **dict.fromkeys(["id", "bid_id", "actor_id"], uuid),
            "review_id": nullable(uuid),
            "historical_input_id": nullable(uuid),
            "origin": {"enum": ["synthetic", "uncertain"]},
            "method_digest": sha,
            "technical_maximum": null,
            "maximum_verified": {"const": False},
            "created_at": date,
            "input_digest": sha,
            "execution_key": sha,
            "rule_count": {"type": "integer", "minimum": 1, "maximum": 1000},
            "call_count": {"type": "integer", "minimum": 3, "maximum": 3000},
            "conservative_budget": string,
            "currency": {"const": "USD"},
            "fee_basis": {"const": "conservative_peak_cache_miss_upper_bound"},
            "current": boolean,
        }
        | flags
    )
    schemas["ScoreJobView"] = obj(
        {
            **dict.fromkeys(["id", "input_id", "command_id", "created_by", "bid_id"], uuid),
            "review_id": nullable(uuid),
            "historical_input_id": nullable(uuid),
            "state": {"enum": ["queued", "running", "paused", "succeeded", "cancelled", "failed"]},
            "execution_key": sha,
            "generation": {"type": "integer", "minimum": 1},
            "fence": integer,
            **dict.fromkeys(["budget", "reserved_fee", "estimated_fee"], string),
            "created_at": date,
            "finished_at": nullable(date),
            "source_current": boolean,
            "reused_calls": integer,
            "calls": {
                "type": "array",
                "items": obj(
                    {
                        "state": {"enum": ["reserved", "succeeded", "not_sent", "unknown"]},
                        "count": integer,
                    }
                ),
            },
            "result": nullable(obj({"digest": sha, "created_at": date})),
            "fee_basis": {"const": "usage_price_estimate_not_invoice"},
        }
        | flags
    )
    prediction = obj({"mode": unavailable, "value": null, "intervals": null, "reasons": strings})
    payload = obj(
        {
            "band_id": nullable(string),
            "reason": string,
            "coverage": {"const": "complete"},
            "status": {"const": "valid"},
            "evidence": {"type": "array", "items": obj({"evidence_id": uuid, "quote": string})},
        }
    )
    judgment = obj(
        {
            "replicate": {"type": "integer", "minimum": 1, "maximum": 3},
            "call_id": uuid,
            "payload": payload,
        }
    )
    item = obj(
        {
            "package_digest": sha,
            "valid_replicates": {"const": 3},
            "judgments": {"type": "array", "minItems": 3, "maxItems": 3, "items": judgment},
            "prediction": obj({"mode": unavailable, "reasons": strings}),
        }
    )
    features = obj(
        {
            "schema": {"const": "complete-three-band-features/1"},
            "status": unavailable,
            "values": null,
            "reasons": strings,
        }
    )
    features["properties"]["status"] = {"const": "unavailable"}
    common = {
        "job_id": uuid,
        "input_id": uuid,
        "result_digest": sha,
        "created_at": date,
        "source_current": boolean,
        "items": {"type": "array", "items": item},
        "formal_eligibility": unavailable,
        "prediction_hidden": boolean,
    }
    schemas["ScoreResultView"] = obj(
        common | {"features": features, "prediction": prediction}, required=list(common)
    )
    schemas["ScoreResultView"]["allOf"] = [
        {
            "if": {"properties": {"prediction_hidden": {"const": True}}},
            "then": {"not": {"anyOf": [{"required": ["features"]}, {"required": ["prediction"]}]}},
            "else": {"required": ["features", "prediction"]},
        }
    ]
    schemas["ScoreShadowContext"] = obj(
        {
            **dict.fromkeys(["job_id", "model_id"], uuid),
            "route_key": string,
            "route_revision": integer,
            **dict.fromkeys(
                ["input_digest", "score_result_digest", "model_artifact_digest", "method_digest"],
                sha,
            ),
            **dict.fromkeys(["source_current", "model_current", "route_current"], boolean),
            "formal_eligibility": unavailable,
            "additional_model_calls": {"const": 0},
            "numeric_prediction_available": {"const": False},
        }
    )
    comparison = obj(
        {
            "schema": {"const": "persisted-score-shadow/1"},
            **dict.fromkeys(["job_id", "model_id"], uuid),
            **dict.fromkeys(
                ["input_digest", "score_result_digest", "model_artifact_digest", "method_digest"],
                sha,
            ),
            "prediction": prediction,
            "formal_eligibility": unavailable,
        }
    )
    base = {
        **dict.fromkeys(["id", "job_id", "model_id", "created_by", "bid_id"], uuid),
        "route_key": string,
        "route_revision": integer,
        "digest": sha,
        "created_at": date,
        "historical_snapshot": {"const": True},
        "prediction_hidden": boolean,
        "additional_model_calls": {"const": 0},
        "formal_eligibility": unavailable,
    }
    schemas["ScoreShadowView"] = obj(base | {"comparison": comparison}, required=list(base))
    schemas["ScoreShadowView"]["allOf"] = [
        {
            "if": {"properties": {"prediction_hidden": {"const": True}}},
            "then": {"not": {"required": ["comparison"]}},
            "else": {"required": ["comparison"]},
        }
    ]
    for (method, path), (name, status) in {
        ("get", "/scoring-capabilities"): ("ScoreCapabilities", 200),
        ("get", "/bids/{bid_id}/score-context"): ("ScoreInputContext", 200),
        ("post", "/bids/{bid_id}/score-inputs"): ("ScoreInputView", 201),
        ("get", "/score-inputs/{input_id}"): ("ScoreInputView", 200),
        ("post", "/score-inputs/{input_id}/jobs"): ("ScoreJobView", 201),
        ("get", "/score-jobs/{job_id}"): ("ScoreJobView", 200),
        ("post", "/score-jobs/{job_id}/state"): ("ScoreJobView", 200),
        ("get", "/score-jobs/{job_id}/result"): ("ScoreResultView", 200),
        ("get", "/score-jobs/{job_id}/shadow-context"): ("ScoreShadowContext", 200),
        ("post", "/score-jobs/{job_id}/shadows"): ("ScoreShadowView", 201),
        ("get", "/score-shadows/{shadow_id}"): ("ScoreShadowView", 200),
    }.items():
        operation = actual.get("paths", {}).get("/api/v1" + path, {}).get(method)
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Complete judgment software; formal qualification unavailable",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
