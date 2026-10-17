"""Only implemented FR029 request/response scope; no planned release/scoring paths."""


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def extend_openapi(actual):
    from apps.api.model_governance_routes import (
        CohortRequest,
        EvaluationRequest,
        ProtocolRequest,
        TrainingJobRequest,
        TrainingProtocol,
    )

    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (
        CohortRequest,
        ProtocolRequest,
        TrainingJobRequest,
        TrainingProtocol,
        EvaluationRequest,
    ):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    string = {"type": "string"}
    uuid = {"type": "string", "format": "uuid"}
    date = {"type": "string", "format": "date-time"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    boolean = {"type": "boolean"}
    integer = {"type": "integer", "minimum": 0}
    origin = {"enum": ["synthetic", "real"]}
    unavailable = {"const": "unavailable"}
    purposes = ["fit", "development", "calibration", "final", "excluded"]
    schemas["GovernanceCapabilities"] = obj(
        {
            "schema_configured": boolean,
            "feature_schema": {"const": "complete-three-band-features/1"},
            "software_training": boolean,
            "formal_model_eligibility": unavailable,
            "synthetic_permanent_research_only": {"const": True},
            "paid_execution_enabled": {"const": False},
            "scoring_worker_configured": {"const": False},
            "reasons": {"type": "array", "items": string},
        }
    )
    schemas["GovernanceProtocol"] = obj(
        {
            "id": uuid,
            "digest": sha,
            "document": ref("TrainingProtocol"),
            "origin": origin,
            "created_by": uuid,
            "created_at": date,
            "formal_eligibility": unavailable,
            "research_only": boolean,
        }
    )
    schemas["GovernanceCohort"] = obj(
        {
            "id": uuid,
            "research_run_id": uuid,
            "protocol_id": uuid,
            "digest": sha,
            "origin": origin,
            "test_start": date,
            "created_by": uuid,
            "created_at": date,
            "protocol_digest": sha,
            "member_counts": {
                "type": "array",
                "items": obj({"purpose": {"enum": purposes}, "count": integer}),
            },
            "current": boolean,
            "formal_eligibility": unavailable,
            "research_only": boolean,
        }
    )
    schemas["GovernanceCohortPreview"] = obj(
        {
            "research_run_id": uuid,
            "protocol_id": uuid,
            "repeat_index": {"type": "integer", "minimum": 1, "maximum": 10},
            "calibration_group_ids": {"type": "array", "items": uuid},
            "source_digest": sha,
            "export_digests": obj(
                dict.fromkeys(["training", "final_features", "final_labels"], sha)
            ),
            "member_counts": obj(dict.fromkeys(purposes, integer)),
            "research_only": {"const": True},
            "formal_eligibility": unavailable,
            "external_model_calls": {"const": 0},
        }
    )
    # Result payloads are JSON data, never pickle or executable model expressions. Closed
    # outer identity/schema plus algorithm-specific validation lives in statistics.validate_model.
    result = obj({"digest": sha, "payload": {"type": "object"}, "created_at": date})
    schemas["GovernanceJob"] = obj(
        {
            "id": uuid,
            "cohort_id": uuid,
            "kind": {"enum": ["train", "evaluate"]},
            "input_digest": sha,
            "generation": {"type": "integer", "minimum": 1},
            "state": {"enum": ["queued", "running", "succeeded", "failed", "cancelled"]},
            "created_by": uuid,
            "created_at": date,
            "current": boolean,
            "result": {"anyOf": [result, {"type": "null"}]},
            "formal_eligibility": unavailable,
        }
    )
    bindings = {
        ("get", "/capabilities"): ("GovernanceCapabilities", 200),
        ("post", "/training-protocols"): ("GovernanceProtocol", 201),
        ("get", "/training-protocols/{protocol_id}"): ("GovernanceProtocol", 200),
        ("post", "/cohort-previews"): ("GovernanceCohortPreview", 200),
        ("post", "/cohorts"): ("GovernanceCohort", 201),
        ("get", "/cohorts/{cohort_id}"): ("GovernanceCohort", 200),
        ("post", "/training-jobs"): ("GovernanceJob", 201),
        ("get", "/jobs/{job_id}"): ("GovernanceJob", 200),
        ("post", "/evaluations"): ("GovernanceJob", 201),
    }
    for (method, suffix), (name, status) in bindings.items():
        operation = actual.get("paths", {}).get("/api/v1/model-governance" + suffix, {}).get(method)
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Actual model-governance software response; formal eligibility unavailable",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
