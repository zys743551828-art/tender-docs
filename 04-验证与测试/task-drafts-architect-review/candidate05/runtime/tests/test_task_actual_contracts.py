"""Actual task exports must not inherit incompatible planned template schemas."""

from copy import deepcopy

from fastapi import FastAPI

from apps.api.export_openapi import implemented_openapi
from apps.api.task_draft_routes import router as drafts
from apps.api.task_routes import router as tasks
from packages.tasks.contracts import extend_openapi


def test_actual_directory_request_response_and_read_projections_override_old_target():
    app = FastAPI()
    app.include_router(tasks)
    actual = implemented_openapi(app)
    extend_openapi(actual)
    route = actual["paths"]["/api/v1/bids/{bid_id}/task-definitions"]
    assert route["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TemplatePage"
    }
    assert route["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TemplateRequest"
    }
    assert route["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TemplateView"
    }
    for path, model in (
        ("/api/v1/task-kinds", "TaskKindPage"),
        ("/api/v1/bids/{bid_id}/task-notifications", "TaskNotificationPage"),
        ("/api/v1/bids/{bid_id}/task-schedule-recoveries", "TaskScheduleRecoveryPage"),
    ):
        assert actual["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/" + model}
    assert not any("task-definition-drafts" in p for p in actual["paths"])
    assert {r["name"] for r in route["post"]["parameters"] if r["in"] == "header"} == {
        "Origin",
        "X-CSRF-Token",
        "Idempotency-Key",
        "If-Match",
    }


def test_draft_contracts_are_mounted_only_when_actual_router_is_registered():
    app = FastAPI()
    app.include_router(drafts)
    actual = implemented_openapi(app)
    authoring_draft = deepcopy(actual["components"]["schemas"]["DraftView"])
    paths = deepcopy(actual["paths"])
    extend_openapi(actual)
    assert actual["components"]["schemas"]["DraftView"] == authoring_draft
    assert set(paths) == set(actual["paths"])
    assert len(paths) == 3
    schemas = actual["components"]["schemas"]
    assert "TaskDraftView" in schemas and "TaskDraftInterpretation" in schemas
    assert "raw_request" in schemas["TaskDraftInterpretation"]["properties"]
    assert schemas["TaskDraftInterpretation"]["properties"]["run_created"]["const"] is False


def test_task_drafts_do_not_replace_authoring_draft_contracts_in_combined_main():
    from apps.api.main import app as main

    old = implemented_openapi(main)
    expected = deepcopy(old["components"]["schemas"]["DraftView"])
    app = FastAPI()
    app.include_router(drafts)
    actual = implemented_openapi(app)
    extend_openapi(actual)
    assert actual["components"]["schemas"]["DraftView"] == expected
    assert "sections" in expected["properties"]
    draft = actual["paths"]["/api/v1/bids/{bid_id}/task-definition-drafts/{draft_id}"]["get"]
    assert draft["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TaskDraftView"
    }
