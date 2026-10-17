"""Only the six implemented registry control operations; no scoring/acceptance claims."""

from packages.model_governance.contracts import obj, ref


def extend_openapi(actual):
    from apps.api.model_registry_routes import RegisterModelRequest, RouteChange

    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (RegisterModelRequest, RouteChange):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    uuid = {"type": "string", "format": "uuid"}
    string = {"type": "string"}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    boolean = {"type": "boolean"}
    nullable_uuid = {"anyOf": [uuid, {"type": "null"}]}
    scope = obj(
        {
            "judgment_method_digest": sha,
            "feature_schema": {"const": "complete-three-band-features/1"},
            "engineering_types": {"type": "array", "items": {"anyOf": [string, {"type": "null"}]}},
            "maximums": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0}},
        }
    )
    schemas["RegistryCapabilities"] = obj(
        {
            "schema_configured": boolean,
            "immutable_registration": boolean,
            "shadow_route_control": boolean,
            "current_review_shadow_producer": {"const": False},
            "formal_model_eligibility": {"const": "unavailable"},
            "paid_execution_enabled": {"const": False},
        }
    )
    # The document is a bounded, algorithm-validated inert JSON envelope. It is never an
    # accepted model nor a client-provided source of authority.
    schemas["RegistryPreview"] = obj(
        {
            "model_id": uuid,
            "training_job_id": uuid,
            "document": {"type": "object"},
            "formal_eligibility": {"const": "unavailable"},
        }
    )
    schemas["RegisteredScoreModel"] = obj(
        {
            "model_id": uuid,
            "training_job_id": uuid,
            "artifact_digest": sha,
            "registered_at": {"type": "string", "format": "date-time"},
            "origin": {"enum": ["synthetic", "real"]},
            "research_only": boolean,
            "current": boolean,
            "scope": scope,
            "route_key": sha,
            "formal_eligibility": {"const": "unavailable"},
        }
    )
    schemas["ControlledModelRoute"] = obj(
        {
            "route_key": sha,
            "lane": {"enum": ["shadow", "business"]},
            "revision": {"type": "integer", "minimum": 0},
            "model_id": nullable_uuid,
            "rollback_model_id": nullable_uuid,
            "current": boolean,
            "formal_eligibility": {"const": "unavailable"},
        }
    )
    bindings = {
        ("get", "/capabilities"): ("RegistryCapabilities", 200),
        ("get", "/training-jobs/{training_job_id}/model-preview"): ("RegistryPreview", 200),
        ("post", "/models"): ("RegisteredScoreModel", 201),
        ("get", "/models/{model_id}"): ("RegisteredScoreModel", 200),
        ("get", "/routes/{lane}/{route_key}"): ("ControlledModelRoute", 200),
        ("post", "/routes/{lane}/{route_key}/changes"): ("ControlledModelRoute", 200),
    }
    for (method, suffix), (name, status) in bindings.items():
        operation = actual.get("paths", {}).get("/api/v1/model-registry" + suffix, {}).get(method)
        if operation is not None:
            responses = operation.setdefault("responses", {})
            if status != 200:
                responses.pop("200", None)
            responses[str(status)] = {
                "description": "Actual registry software; formal eligibility unavailable",
                "content": {"application/json": {"schema": ref(name)}},
            }
    return actual
