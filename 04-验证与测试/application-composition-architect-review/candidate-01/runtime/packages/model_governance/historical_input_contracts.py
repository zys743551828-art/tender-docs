"""Four implemented input operations, distinct from proof and future execution APIs."""

from packages.model_governance.contracts import obj, ref
from packages.model_governance.historical_contracts import extend_openapi as proof_schemas
from packages.model_governance.historical_requests import HistoricalInputRequest


def extend_openapi(actual):
    actual = proof_schemas(actual)
    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    schema = HistoricalInputRequest.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(schema.pop("$defs", {}))
    schemas["HistoricalInputRequest"] = schema
    uuid = {"type": "string", "format": "uuid"}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    date = {"type": "string", "format": "date-time"}
    string = {"type": "string"}
    boolean = {"type": "boolean"}
    nullable = lambda value: {"anyOf": [value, {"type": "null"}]}  # noqa: E731 -- local schema notation
    flags = {
        "formal_eligibility": {"const": "unavailable"},
        "execution_available": {"const": False},
        "paid_execution_enabled": {"const": False},
    }
    schemas["HistoricalInputCapabilities"] = obj(
        {"schema_configured": boolean, "historical_input_registration": boolean} | flags
    )
    context = dict(schemas["HistoricalProofContext"]["properties"])
    context.update(
        {
            "historical_input_available": {"const": True},
            "paid_execution_enabled": {"const": False},
            "rule_sets": {
                "type": "array",
                "items": obj(
                    {
                        "id": uuid,
                        "material_id": uuid,
                        "revision": {"type": "integer", "minimum": 1},
                        "digest": digest,
                        "coverage": {"enum": ["complete", "partial", "conflict"]},
                        "material_versions": {
                            "type": "array",
                            "items": obj(
                                {
                                    "document_version_id": uuid,
                                    "role": {"enum": ["tender", "supplement"]},
                                    "original_name": string,
                                }
                            ),
                        },
                    }
                ),
            },
            "document_versions": {
                "type": "array",
                "items": obj(
                    {
                        "id": uuid,
                        "artifact_kind": {"enum": ["source_original", "bid_body"]},
                        "original_name": string,
                        "digest": digest,
                        "latest_parse_id": nullable(uuid),
                        "coverage": nullable({"enum": ["complete", "partial", "failed"]}),
                    }
                ),
            },
        }
    )
    schemas["HistoricalInputContext"] = obj(context)
    schemas["HistoricalInputView"] = obj(
        {
            "id": uuid,
            "bid_id": uuid,
            "submission_id": uuid,
            "body_version_id": uuid,
            "body_digest": digest,
            "subject_id": uuid,
            "subject_decision_id": uuid,
            "historical_as_of": date,
            "rule_set_id": uuid,
            "material_snapshot_id": uuid,
            "method_digest": digest,
            "manifest_blob_id": uuid,
            "manifest_digest": digest,
            "origin": {"enum": ["synthetic", "uncertain"]},
            "prediction_timing": {"const": "retrospective"},
            "execution_purpose": {"const": "historical_backtest"},
            "research_only": {"const": True},
            "actor_id": uuid,
            "created_at": date,
            "authority_digest": digest,
            "rule_count": {"type": "integer", "minimum": 1, "maximum": 1000},
            "current": boolean,
            "notes": {"type": "array", "items": string},
            "chapters": {
                "type": "array",
                "items": obj(
                    dict.fromkeys(["id", "rule_id", "body_parse_id", "manifest_blob_id"], uuid)
                    | {"digest": digest}
                ),
            },
        }
        | flags
    )
    for (method, suffix), (name, status) in {
        ("get", "/capabilities"): ("HistoricalInputCapabilities", 200),
        ("get", "/submissions/{submission_id}/context"): ("HistoricalInputContext", 200),
        ("get", "/{input_id}"): ("HistoricalInputView", 200),
        ("post", ""): ("HistoricalInputView", 201),
    }.items():
        operation = (
            actual.get("paths", {}).get("/api/v1/historical-inputs" + suffix, {}).get(method)
        )
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Current reconstruction authority; never an old prediction",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
