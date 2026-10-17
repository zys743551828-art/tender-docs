"""Extend only actually registered FR029 endpoints; not the planned release/scoring APIs."""


def extend_openapi(actual):
    from apps.api.model_governance_routes import (
        CohortRequest,
        ProtocolRequest,
        TrainingJobRequest,
        TrainingProtocol,
    )

    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    for model in (CohortRequest, ProtocolRequest, TrainingJobRequest, TrainingProtocol):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    # Response shape is authored on the router at the implementation boundary; this
    # function does not manufacture any future model route or scoring operation.
    return actual
