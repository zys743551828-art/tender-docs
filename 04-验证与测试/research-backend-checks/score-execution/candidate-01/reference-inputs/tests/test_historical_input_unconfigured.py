"""Read-only absent authority probe. Auth is explicitly stubbed, not login evidence."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api import historical_input_routes
from packages.domain.runtime import DomainError, connection

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_INPUT_UNCONFIGURED") != "1",
    reason="Read-only old-schema API-role probe, no fixtures/DDL",
)


def test_missing_input_authority_returns_specific_503_without_writes(monkeypatch):
    with connection() as c:
        assert c.execute("SELECT to_regnamespace('historical_input') IS NULL AS absent").fetchone()[
            "absent"
        ]
        assert not c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
            "rolsuper"
        ]
    monkeypatch.setattr(historical_input_routes, "auth", lambda *args: {"member_id": uuid4()})

    def forbidden(*args, **kwargs):
        raise AssertionError("Absent authority must reject before any object I/O")

    monkeypatch.setattr(historical_input_routes.repo, "stage", forbidden)
    app = FastAPI()
    app.include_router(historical_input_routes.router)

    @app.exception_handler(DomainError)
    async def error(request, exception):
        return JSONResponse({"code": exception.code}, status_code=exception.status)

    with TestClient(app) as web:
        cap = web.get("/api/v1/historical-inputs/capabilities")
        assert cap.status_code == 200 and not cap.json()["historical_input_registration"]
        body = {
            key: str(uuid4())
            for key in ["submission_id", "historical_subject_authority_id", "rule_set_id"]
        } | {
            "source_claim_ids": [str(uuid4())],
            "material_version_ids": [str(uuid4())],
            "reason": "只读缺配置探针",
        }
        response = web.post(
            "/api/v1/historical-inputs",
            json=body,
            headers={"If-Match": '"no-authority"', "Idempotency-Key": uuid4().hex},
        )
        assert (
            response.status_code == 503
            and response.json()["code"] == "HISTORICAL_INPUT_NOT_CONFIGURED"
        )
