"""Actual registry requests are server identities, never labels or accepted booleans."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from apps.api.model_registry_routes import RegisterModelRequest, RouteChange, router
from packages.model_governance.registry_contracts import extend_openapi


def test_only_six_actual_operations_closed_request_and_no_planned_producer():
    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    operations = [
        (path, method)
        for path, item in actual["paths"].items()
        for method in item
        if method in {"get", "post"}
    ]
    assert len(operations) == 6
    assert not any("score" in path or "acceptance" in path for path, _ in operations)
    assert actual["components"]["schemas"]["RegisterModelRequest"]["additionalProperties"] is False
    assert actual["components"]["schemas"]["RouteChange"]["additionalProperties"] is False
    for path, method in operations:
        assert all(
            response["content"]["application/json"]["schema"].get("$ref")
            for status, response in actual["paths"][path][method]["responses"].items()
            if status in {"200", "201"}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("accepted", True),
        ("metrics", {"mae": 0}),
        ("origin", "real"),
        ("features", [1, 2, 3, 4]),
        ("labels", [0]),
        ("scope", {}),
    ],
)
def test_registration_refuses_user_supplied_authority(field, value):
    with pytest.raises(ValidationError):
        RegisterModelRequest.model_validate(
            {"training_job_id": str(uuid4()), "reason": "synthetic fixture", field: value}
        )


@pytest.mark.parametrize(
    "body",
    [
        {"action": "publish", "reason": " "},
        {"action": "force_publish", "reason": "synthetic"},
        {"action": "publish", "reason": "synthetic", "training_job_id": str(uuid4())},
        {"action": "publish", "reason": "synthetic", "ignore_source": True},
    ],
)
def test_route_commands_do_not_have_force_or_source_override(body):
    with pytest.raises(ValidationError):
        RouteChange.model_validate(body)
