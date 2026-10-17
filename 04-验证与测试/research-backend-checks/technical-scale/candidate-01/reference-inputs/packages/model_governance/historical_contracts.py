"""Eight actual proof/subject operations only. Historical input execution is absent."""

from packages.model_governance.contracts import obj, ref


def extend_openapi(actual):
    from packages.model_governance.historical_requests import (
        HistoricalConfirmationRequest,
        HistoricalSubjectRequest,
        HistorySourceClaimRequest,
    )

    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (
        HistoricalConfirmationRequest,
        HistoricalSubjectRequest,
        HistorySourceClaimRequest,
    ):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    uuid = {"type": "string", "format": "uuid"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    date = {"type": "string", "format": "date-time"}
    string = {"type": "string"}
    boolean = {"type": "boolean"}
    integer = {"type": "integer", "minimum": 0}
    null = {"type": "null"}
    nullable_uuid = {"anyOf": [uuid, null]}
    nullable_date = {"anyOf": [date, null]}
    decision = {"enum": ["pending", "confirmed", "rejected"]}
    fact_kind = {
        "enum": [
            "content_existed",
            "published",
            "effective",
            "known_to_bidder",
            "received_by_bidder",
        ]
    }
    origin = {"enum": ["real", "synthetic", "uncertain"]}
    proof = obj(
        {
            "id": uuid,
            "bid_id": uuid,
            "subject_id": nullable_uuid,
            "claim_id": nullable_uuid,
            "fact_kind": {"anyOf": [fact_kind, null]},
            "document_version_id": uuid,
            "document_digest": sha,
            "parse_id": uuid,
            "evidence_id": uuid,
            "physical_page": {"type": "integer", "minimum": 1},
            "evidence_digest": sha,
            "source_id": uuid,
            "root_source_id": uuid,
            "quote": string,
            "explanation": string,
        }
    )
    own_decision = obj(
        {"id": uuid, "decision": decision, "actor_id": uuid, "reason": string, "created_at": date}
    )
    common = {
        "id": uuid,
        "bid_id": uuid,
        "submission_id": uuid,
        "declared_origin": origin,
        "supersedes_id": nullable_uuid,
        "reason": string,
        "actor_id": uuid,
        "created_at": date,
        "last_own_decision": {"anyOf": [own_decision, null]},
        "head_revision": integer,
        "is_current_head": boolean,
        "submission_current": boolean,
        "manual_decision": {"enum": ["pending", "confirmed", "rejected", "superseded"]},
        "proofs_current": boolean,
        "proof_refs": {"type": "array", "items": proof},
        "historical_input_available": {"const": False},
        "formal_eligibility": {"const": "unavailable"},
    }
    schemas["HistoricalSubjectAuthorityView"] = obj(
        common
        | {
            "kind": {"const": "subject"},
            "body_version_id": uuid,
            "body_digest": sha,
            "submitted_at": date,
            "bidder_id": nullable_uuid,
            "round_id": nullable_uuid,
            "engineering_project_id": nullable_uuid,
            "identity_snapshot": {"anyOf": [ref("HistoricalIdentityProjection"), null]},
            "subject_relationship_current": boolean,
            "related_subject_current": null,
        }
    )
    schemas["HistorySourceClaimView"] = obj(
        common
        | {
            "kind": {"const": "claim"},
            "document_version_id": uuid,
            "document_digest": sha,
            "subject_id": nullable_uuid,
            "subject_relationship_current": null,
            "related_subject_current": boolean,
            "facts": {
                "type": "array",
                "items": obj(
                    {
                        "claim_id": uuid,
                        "fact_kind": fact_kind,
                        "claimed_at": nullable_date,
                        "valid_until": nullable_date,
                        "explanation": string,
                    }
                ),
            },
        }
    )
    schemas["HistoricalProofCapabilities"] = obj(
        {
            "schema_configured": boolean,
            "proof_and_subject_commands": boolean,
            "historical_input_execution": {"const": False},
            "formal_model_eligibility": {"const": "unavailable"},
            "paid_execution_enabled": {"const": False},
        }
    )
    subject_head = obj(
        {"subject_id": uuid, "decision_id": uuid, "revision": integer, "decision": decision}
    )
    claim_head = obj(
        {
            "document_version_id": uuid,
            "claim_id": uuid,
            "decision_id": uuid,
            "revision": integer,
            "decision": decision,
        }
    )
    schemas["HistoricalProofContext"] = obj(
        {
            "submission_id": uuid,
            "bid_id": uuid,
            "body_version_id": uuid,
            "body_digest": sha,
            "submitted_at": date,
            "original_review_id": nullable_uuid,
            "subject_head": {"anyOf": [subject_head, null]},
            "claim_heads": {"type": "array", "items": claim_head},
            "historical_input_available": {"const": False},
            "formal_eligibility": {"const": "unavailable"},
            "prediction_timing": {"const": "retrospective"},
        }
    )
    bindings = {
        ("get", "/capabilities"): ("HistoricalProofCapabilities", 200),
        ("get", "/submissions/{submission_id}/context"): ("HistoricalProofContext", 200),
        ("post", "/subjects"): ("HistoricalSubjectAuthorityView", 201),
        ("get", "/subjects/{subject_id}"): ("HistoricalSubjectAuthorityView", 200),
        ("post", "/subjects/{subject_id}/confirm"): ("HistoricalSubjectAuthorityView", 200),
        ("post", "/source-claims"): ("HistorySourceClaimView", 201),
        ("get", "/source-claims/{claim_id}"): ("HistorySourceClaimView", 200),
        ("post", "/source-claims/{claim_id}/confirm"): ("HistorySourceClaimView", 200),
    }
    for (method, suffix), (name, status) in bindings.items():
        operation = actual.get("paths", {}).get("/api/v1/historical-proof" + suffix, {}).get(method)
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Manual evidence authority only; input qualification unavailable",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
