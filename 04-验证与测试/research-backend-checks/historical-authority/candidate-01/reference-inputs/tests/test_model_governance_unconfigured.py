"""Old check schema explicitly returns 503; never applies draft DDL to it."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.auth import csrf
from apps.api.model_governance_routes import router
from packages.domain.runtime import DomainError, connection, required, secret_hash, sha, uid
from tests.test_model_governance_statistics import bundle

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_GOVERNANCE_UNCONFIGURED") != "1",
    reason="Needs independent nonowner API LOGIN on a schema without new model governance SQL",
)


def test_missing_new_authority_is_explicit_unconfigured_before_business_writes():
    actor, token = uid(), uuid4().hex
    with connection() as c:
        assert c.execute(
            "SELECT to_regnamespace('model_governance') IS NULL AS missing"
        ).fetchone()["missing"]
        assert not c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
            "rolsuper"
        ]
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,'合成模型软件未配置验证')", (actor,)
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (uid(), actor, secret_hash(token), sha(csrf(token).encode())),
        )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def handle(request, error):
        return JSONResponse({"code": error.code}, status_code=error.status)

    with TestClient(app) as client:
        client.cookies.set("session", token)
        capabilities = client.get("/api/v1/model-governance/capabilities")
        assert capabilities.status_code == 200 and capabilities.json()["schema_configured"] is False
        assert capabilities.json()["formal_model_eligibility"] == "unavailable"
        response = client.post(
            "/api/v1/model-governance/training-protocols",
            json={"document": bundle()["protocol"], "reason": "synthetic"},
            headers={
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": csrf(token),
                "Idempotency-Key": uuid4().hex,
            },
        )
        assert (
            response.status_code == 503
            and response.json()["code"] == "MODEL_GOVERNANCE_NOT_CONFIGURED"
        )
