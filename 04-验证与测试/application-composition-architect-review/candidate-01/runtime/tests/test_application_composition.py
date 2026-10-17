"""Fixed application registration/contract checks; no database or external effect claims."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from apps.api.composition import mount
from apps.api.export_openapi import implemented_openapi
from apps.api.main import app
from packages.domain import reviews
from packages.tasks.inputs import review_guard

ROOT = Path(__file__).resolve().parents[1]


def operations(actual):
    return [operation for methods in actual["paths"].values() for operation in methods.values()]


def test_registered_paths_operation_ids_and_task_guard_are_unique():
    pairs = [
        (r.path, method) for r in app.routes if isinstance(r, APIRoute) for method in r.methods
    ]
    assert len(pairs) == len(set(pairs))
    actual = implemented_openapi(app)
    ids = [o["operationId"] for o in operations(actual)]
    assert len(ids) == len(set(ids))
    assert reviews.TASK_INPUT_GUARD is review_guard
    expected = [
        ("/api/v1/model-governance/capabilities", "get"),
        ("/api/v1/historical-proof/capabilities", "get"),
        ("/api/v1/historical-inputs/capabilities", "get"),
        ("/api/v1/model-registry/capabilities", "get"),
        ("/api/v1/scoring-capabilities", "get"),
        ("/api/v1/bids/{bid_id}/lifecycle", "post"),
        ("/api/v1/bids/{bid_id}/task-definition-drafts", "post"),
    ]
    for path, method in expected:
        operation = actual["paths"][path][method]
        success = [v for code, v in operation["responses"].items() if code.startswith("2")]
        assert success and all(
            v.get("content", {}).get("application/json", {}).get("schema") for v in success
        )
    assert not any(
        "technical-scales" in path or "authoring-structure" in path for path in actual["paths"]
    )


def test_duplicate_registration_is_rejected_before_mutation():
    isolated = FastAPI()
    mount(isolated)
    count = len(isolated.routes)
    with pytest.raises(RuntimeError, match="Duplicate reviewed operation"):
        mount(isolated)
    assert len(isolated.routes) == count


def test_all_references_resolve_and_every_schema_is_valid():
    actual = implemented_openapi(app)
    for schema in actual["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                assert ref.startswith("#/"), "No network schema resolution"
                target = actual
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(actual)


def test_body_models_keep_their_validation_semantics_and_draft_names_are_distinct():
    actual = implemented_openapi(app)
    schemas = actual["components"]["schemas"]

    def expand(value, registry, stack=()):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[-1]
                if name in stack:
                    return {"recursive_schema": name}
                return expand(registry[name], registry, stack + (name,))
            return {
                k: expand(v, registry, stack)
                for k, v in value.items()
                if k not in {"title", "default"}
            }
        if isinstance(value, list):
            return [expand(v, registry, stack) for v in value]
        return value

    seen: dict[str, dict] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.body_field:
            continue
        model = route.body_field.type_
        if not hasattr(model, "model_json_schema"):
            continue
        original = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = original.pop("$defs", {})
        expected = expand(original, definitions | {model.__name__: original})
        assert expand(schemas[model.__name__], schemas) == expected, (route.path, model.__name__)
        if model.__name__ in seen:
            assert seen[model.__name__] == expected, "Same model name must have identical semantics"
        seen[model.__name__] = expected
    assert "sections" in schemas["DraftView"]["properties"]
    assert "interpretation" in schemas["TaskDraftView"]["properties"]
    assert "interpretation" not in schemas["DraftView"]["properties"]
    assert schemas["ScoreCapabilities"]["properties"]["paid_execution_enabled"] == {"const": False}


def test_target_and_fixed_base_are_not_mutated_by_export():
    target = ROOT / "packages/contracts/openapi/openapi.json"
    before = target.read_bytes()
    first = implemented_openapi(app)
    second = implemented_openapi(app)
    assert first == second and before == target.read_bytes()
    # Public response schema is copied, never an alias to a previous caller's JSON tree.
    damaged = deepcopy(first)
    damaged["components"]["schemas"]["TaskDraftView"]["title"] = "caller mutation"
    assert implemented_openapi(app) == first
    assert (
        json.loads(before)["components"]["schemas"]["DraftView"]
        != first["components"]["schemas"]["TaskDraftView"]
    )


def test_declared_response_models_match_the_final_response_schema_semantics():
    actual = implemented_openapi(app)
    schemas = actual["components"]["schemas"]

    def expand(value, registry, stack=()):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[-1]
                if name in stack:
                    return {"recursive_schema": name}
                return expand(registry[name], registry, stack + (name,))
            return {
                k: expand(v, registry, stack)
                for k, v in value.items()
                if k not in {"title", "default"}
            }
        if isinstance(value, list):
            return [expand(v, registry, stack) for v in value]
        return value

    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.response_model is None:
            continue
        original = TypeAdapter(route.response_model).json_schema(
            ref_template="#/components/schemas/{model}", mode="serialization"
        )
        definitions = original.pop("$defs", {})
        definitions[getattr(route.response_model, "__name__", "root")] = original
        for method in route.methods:
            operation = actual["paths"][route.path][method.lower()]
            response = operation["responses"][str(route.status_code or 200)]["content"][
                "application/json"
            ]["schema"]
            assert expand(original, definitions) == expand(response, schemas), (route.path, method)
            checked += 1
    assert checked > 0
