"""实际报价契约闭包及边界，防止把目标未实现路径当成actual。"""

from fastapi import FastAPI
from jsonschema import Draft202012Validator

from apps.api.pricing_routes import router
from packages.pricing.contracts import extend_openapi


def actual_contract():
    app = FastAPI()
    app.include_router(router)
    actual = app.openapi()
    actual.setdefault("components", {}).setdefault("schemas", {})
    return extend_openapi(actual)


def validate(name, value):
    actual = actual_contract()
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + name, "components": actual["components"]}
    ).validate(value)


def test_actual_pricing_contract_is_closed_and_only_extends_registered_operations():
    actual = actual_contract()
    operations = [
        operation
        for path in actual["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post"}
    ]
    assert len(operations) == 10
    assert actual["components"]["schemas"]["PricingSimulationView"]["properties"]["price_score"][
        "type"
    ] == ["string", "null"]
    assert {"eligible", "notes", "input_digest", "formula_snapshot"} <= set(
        actual["components"]["schemas"]["PricingSimulationView"]["required"]
    )
    for name, schema in actual["components"]["schemas"].items():
        Draft202012Validator.check_schema(schema)
        if name.startswith("Pric") and schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, name
    empty = {"paths": {}, "components": {"schemas": {}}}
    extend_openapi(empty)
    assert empty["paths"] == {}
