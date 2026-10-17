"""Reviewed module registration; no dynamic plugins, consumers or provider startup."""

from fastapi.routing import APIRoute


def mount(app):
    from apps.api.historical_input_routes import router as historical_input
    from apps.api.historical_routes import router as historical_proof
    from apps.api.lifecycle_routes import router as lifecycle
    from apps.api.model_governance_routes import router as governance
    from apps.api.model_registry_routes import router as registry
    from apps.api.scoring_routes import router as scoring
    from apps.api.task_draft_routes import router as task_drafts

    routers = (
        lifecycle,
        task_drafts,
        governance,
        historical_proof,
        historical_input,
        registry,
        scoring,
    )
    seen = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                key = (route.path, method)
                if key in seen:
                    raise RuntimeError("Duplicate reviewed operation registration")
                seen.add(key)
    for router in routers:
        app.include_router(router)


def extend_openapi(actual):
    from apps.api.composition_contracts import extend_lifecycle
    from packages.model_governance.contracts import extend_openapi as governance
    from packages.model_governance.historical_contracts import extend_openapi as historical_proof
    from packages.model_governance.historical_input_contracts import (
        extend_openapi as historical_input,
    )
    from packages.model_governance.registry_contracts import extend_openapi as registry
    from packages.model_governance.scoring_contracts import extend_openapi as scoring
    from packages.tasks.contracts import extend_openapi as tasks

    # Target mappings run first. These projections only replace registered actual operations.
    for extension in (governance, historical_proof, historical_input, registry, scoring, tasks):
        extension(actual)
    extend_lifecycle(actual)
