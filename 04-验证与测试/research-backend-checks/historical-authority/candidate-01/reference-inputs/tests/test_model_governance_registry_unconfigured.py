"""Real nonowner old-baseline refusal. Never initializes or patches any schema."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.auth import csrf
from apps.api.model_registry_routes import router
from packages.domain.runtime import DomainError, connection, required, secret_hash, sha, uid

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_RELEASE_UNCONFIGURED") != "1",
    reason="Explicit nonowner profile without release extension only",
)


def test_missing_release_is_503_and_never_writes_objects(monkeypatch):
    actor, token = uid(), uuid4().hex
    with connection() as c:
        assert c.execute("SELECT to_regnamespace('model_release') IS NULL AS value").fetchone()[
            "value"
        ]
        assert not c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
            "rolsuper"
        ]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成登记未配置测试')", (actor,))
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (uid(), actor, secret_hash(token), sha(csrf(token).encode())),
        )
    writes = []
    monkeypatch.setattr("packages.domain.storage.put", lambda *args: writes.append(True))
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def error(request, failure):
        return JSONResponse({"code": failure.code}, status_code=failure.status)

    with TestClient(app) as client:
        client.cookies.set("session", token)
        cap = client.get("/api/v1/model-registry/capabilities")
        assert cap.status_code == 200 and not cap.json()["schema_configured"]
        assert cap.json()["formal_model_eligibility"] == "unavailable"
        response = client.post(
            "/api/v1/model-registry/models",
            json={"training_job_id": str(uuid4()), "reason": "synthetic only"},
            headers={
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": csrf(token),
                "Idempotency-Key": uuid4().hex,
                "If-Match": '"synthetic"',
            },
        )
        assert (
            response.status_code == 503
            and response.json()["code"] == "MODEL_RELEASE_NOT_CONFIGURED"
        )
        assert writes == []
