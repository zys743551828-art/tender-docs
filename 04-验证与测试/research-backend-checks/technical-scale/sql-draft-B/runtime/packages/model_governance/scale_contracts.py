"""Seven implemented denominator-registry operations; independent score integration absent."""

from packages.model_governance.contracts import obj, ref
from packages.model_governance.historical_contracts import extend_openapi as historical_schemas
from packages.model_governance.scale_requests import (
    TechnicalScaleCorrection,
    TechnicalScaleRequest,
    TechnicalScaleWithdrawal,
)


def extend_openapi(actual):
    historical_schemas(actual)
    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (TechnicalScaleRequest, TechnicalScaleCorrection, TechnicalScaleWithdrawal):
        value = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(value.pop("$defs", {}))
        schemas[model.__name__] = value
    uuid = {"type": "string", "format": "uuid"}
    date = {"type": "string", "format": "date-time"}
    string, boolean, null = {"type": "string"}, {"type": "boolean"}, {"type": "null"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    integer = {"type": "integer", "minimum": 0}
    purpose = {"enum": ["current_prebid", "historical_backtest"]}
    method = {"const": "explicit-whole-technical-total-and-scale/1"}
    flags = {
        "formal_eligibility": {"const": "unavailable"},
        "paid_execution_enabled": {"const": False},
    }

    def nullable(value):
        return {"anyOf": [value, null]}

    def array(value):
        return {"type": "array", "items": value}

    identity = obj(
        {
            **dict.fromkeys(["bidder_id", "round_id", "engineering_project_id"], uuid),
            **dict.fromkeys(["bidder_name", "engineering_title"], string),
            **dict.fromkeys(["region", "engineering_type", "lot"], nullable(string)),
            "round_no": {"type": "integer", "minimum": 1},
            "binding_status": {"enum": ["pending", "confirmed"]},
            "authority_id": nullable(uuid),
            "digest": sha,
        }
    )
    identity = {"anyOf": [identity, ref("HistoricalIdentityProjection"), null]}
    head = obj(
        {
            "bid_id": uuid,
            "purpose": purpose,
            "context_id": uuid,
            "version_id": uuid,
            "decision_id": uuid,
            "revision": integer,
            "state": {"enum": ["pending", "confirmed", "withdrawn"]},
        }
    )
    source_head = obj(
        {
            "ancestor_id": uuid,
            "digest": sha,
            "policy_class": {
                "enum": ["prebid_content", "official_label", "post_outcome", "derived_mixed"]
            },
            "known_at": date,
            "revision": integer,
            "status": {"enum": ["candidate", "published", "withdrawn"]},
            "valid_from": date,
            "valid_until": nullable(date),
        }
    )
    document = obj(
        {
            "role": {"enum": ["tender", "supplement"]},
            "document_version_id": uuid,
            "original_name": string,
            "blob_digest": sha,
            "parse_id": nullable(uuid),
            "coverage": nullable({"enum": ["complete", "partial", "failed"]}),
            "page_count": nullable(integer),
            "evidence": array(
                obj(
                    {
                        "id": uuid,
                        "physical_page": {"type": "integer", "minimum": 1},
                        "digest": sha,
                        "text_content": string,
                    }
                )
            ),
            "source_heads": array(source_head),
        }
    )
    schemas["TechnicalScaleCapabilities"] = obj(
        {
            "schema_configured": boolean,
            "sourced_denominator_registration": boolean,
            "supported_declaration_method": method,
            "supported_scale_descriptions": array(string),
            "executable_aggregation": {"const": False},
            "score_input_integration": {"const": False},
        }
        | flags
    )
    schemas["TechnicalScaleContext"] = obj(
        {
            "bid_id": uuid,
            "purpose": purpose,
            "submission_id": nullable(uuid),
            "historical_as_of": nullable(date),
            "identity": identity,
            "historical_subject": nullable(ref("HistoricalSubjectAuthorityView")),
            "historical_claims": array(ref("HistorySourceClaimView")),
            "material_snapshot_id": nullable(uuid),
            "material_choices": array(obj({"id": uuid, "revision": integer, "created_at": date})),
            "documents": array(document),
            "head": nullable(head),
            "ordinary_work_writable": boolean,
            "supported_declaration_method": method,
            "supported_scale_descriptions": array(string),
            "executable_aggregation": {"const": False},
        }
        | flags
    )
    decision = obj(
        {
            "id": uuid,
            "state": {"enum": ["pending", "confirmed", "withdrawn"]},
            "created_at": date,
            "actor_id": uuid,
            "reason": string,
        }
    )
    evidence = obj(
        {
            **dict.fromkeys(
                ["id", "document_version_id", "parse_id", "evidence_id", "source_id"], uuid
            ),
            **dict.fromkeys(["blob_digest", "evidence_digest"], sha),
            "physical_page": {"type": "integer", "minimum": 1},
            "source_revision": nullable(integer),
            "quote": string,
            "supported_fields": array(
                {"enum": ["technical_maximum", "scale_description", "aggregation"]}
            ),
        }
    )
    schemas["TechnicalScaleView"] = obj(
        {
            "id": uuid,
            "bid_id": uuid,
            "purpose": purpose,
            "submission_id": nullable(uuid),
            "historical_subject_authority_id": nullable(uuid),
            "material_snapshot_id": uuid,
            "identity": identity,
            "technical_maximum": nullable(string),
            "recorded_maximum": nullable(string),
            "scale_description": string,
            "aggregation": ref("ScaleAggregation"),
            "maximum_usable": boolean,
            "aggregation_usable": {"const": False},
            "current_applicability": boolean,
            "state": {
                "enum": ["pending", "confirmed", "withdrawn", "superseded", "needs_revalidation"]
            },
            "reasons": array(string),
            "is_current_head": boolean,
            "head_revision": nullable(integer),
            "decision": nullable(decision),
            "supersedes_id": nullable(uuid),
            "evidence_refs": array(evidence),
            "created_at": date,
            "created_by": uuid,
        }
        | flags
    )
    schemas["TechnicalScaleList"] = obj(
        {
            "items": array(ref("TechnicalScaleView")),
            "limit": {"const": 200},
            "formal_eligibility": {"const": "unavailable"},
        }
    )
    bindings = {
        ("get", "/technical-scale-capabilities"): ("TechnicalScaleCapabilities", 200),
        ("get", "/bids/{bid_id}/technical-scale-context"): ("TechnicalScaleContext", 200),
        ("post", "/bids/{bid_id}/technical-scales"): ("TechnicalScaleView", 201),
        ("get", "/bids/{bid_id}/technical-scales"): ("TechnicalScaleList", 200),
        ("get", "/technical-scales/{scale_id}"): ("TechnicalScaleView", 200),
        ("post", "/technical-scales/{scale_id}/corrections"): ("TechnicalScaleView", 201),
        ("post", "/technical-scales/{scale_id}/withdrawals"): ("TechnicalScaleView", 200),
    }
    for (verb, path), (name, status) in bindings.items():
        operation = actual.get("paths", {}).get("/api/v1" + path, {}).get(verb)
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Sourced denominator only; executable aggregation and scoring/model qualification unavailable",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
