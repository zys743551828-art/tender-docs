"""Read-only actual old schema probe. Authentication is an explicit stub, not auth evidence."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api import historical_routes
from packages.domain.runtime import DomainError, connection

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_PROOF_UNCONFIGURED") != "1",
    reason="Read-only nonowner old schema; never writes session/fixtures or applies SQL",
)


def test_old_schema_explicit_503_precedes_business_write(monkeypatch):
    with connection() as c:
        assert c.execute(
            "SELECT to_regnamespace('historical_backtest') IS NULL AS value"
        ).fetchone()["value"]
        assert not c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
            "rolsuper"
        ]
    monkeypatch.setattr(historical_routes, "auth", lambda *args: {"member_id": uuid4()})
    app = FastAPI()
    app.include_router(historical_routes.router)

    @app.exception_handler(DomainError)
    async def error(request, exception):
        return JSONResponse({"code": exception.code}, status_code=exception.status)

    with TestClient(app) as web:
        cap = web.get("/api/v1/historical-proof/capabilities")
        assert cap.status_code == 200 and cap.json()["schema_configured"] is False
        assert cap.json()["historical_input_execution"] is False
        response = web.post(
            "/api/v1/historical-proof/subjects",
            json={
                "submission_id": str(uuid4()),
                "proof_refs": [],
                "declared_data_origin": "synthetic",
                "reason": "只读未配置探针",
            },
            headers={"If-Match": '"unconfigured"', "Idempotency-Key": uuid4().hex},
        )
        assert (
            response.status_code == 503
            and response.json()["code"] == "HISTORICAL_PROOF_NOT_CONFIGURED"
        )
