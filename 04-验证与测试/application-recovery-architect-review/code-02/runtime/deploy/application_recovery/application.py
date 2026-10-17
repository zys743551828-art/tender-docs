"""Actual frozen application, exposed only to a bounded synchronous ASGI acceptance harness."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from collections import Counter
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from deploy.application_recovery import archive, binding
from deploy.application_recovery import security as s


class PausedApplication:
    """Not a general deployment: method/path/body allowlist for one newly created test UUID."""

    def __init__(self, app, fixture):
        self.app = app
        self.new_bid = None
        self.create_body = {"title": "合成恢复后新项目-" + uuid4().hex}
        self.read_paths = {
            "/health/live",
            "/health/ready",
            "/api/v1/auth/session",
            "/api/v1/bids/" + fixture["ids"]["bid"],
            "/api/v1/knowledge/" + fixture["ids"]["knowledge"],
            "/api/v1/report-exports/" + fixture["ids"]["report"],
        }

    def bind_new_bid(self, identity):
        identity = str(UUID(identity))
        if self.new_bid is not None:
            raise s.RecoveryError("NEW_TEST_BID_ALREADY_BOUND")
        self.new_bid = identity
        self.read_paths.update(
            {"/api/v1/bids/" + identity, "/api/v1/bids/" + identity + "/settings"}
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        permitted = method == "GET" and path in self.read_paths
        permitted |= method == "POST" and path in {"/api/v1/auth/login", "/api/v1/bids"}
        permitted |= (
            method == "PATCH"
            and self.new_bid is not None
            and path == "/api/v1/bids/" + self.new_bid + "/settings"
        )
        if not permitted:
            return await JSONResponse({"code": "RECOVERY_WORK_PAUSED"}, status_code=423)(
                scope, receive, send
            )
        if method in {"POST", "PATCH"} and path != "/api/v1/auth/login":
            raw = bytearray()
            while True:
                message = await receive()
                raw.extend(message.get("body", b""))
                if len(raw) > 8192:
                    return await JSONResponse({"code": "RECOVERY_INPUT_LIMIT"}, status_code=413)(
                        scope, receive, send
                    )
                if not message.get("more_body"):
                    break
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            expected: dict[str, Any] = {}
            if method == "POST":
                expected.update(self.create_body)
            else:
                expected["result_reminder"] = False
            if body != expected:
                return await JSONResponse(
                    {"code": "RECOVERY_INPUT_NOT_ALLOWLISTED"}, status_code=423
                )(scope, receive, send)

            async def replay():
                return {"type": "http.request", "body": bytes(raw), "more_body": False}

            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)


def rows(c):
    result = {}
    for item in archive.generic.catalog(c)["relations"]:
        from psycopg import sql

        query = sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t").format(
            sql.Identifier(item["schema"]), sql.Identifier(item["name"])
        )
        result[(item["schema"], item["name"])] = Counter(r[0] for r in c.execute(query))
    return result


def check(cluster, plan, fixture, credential):
    with psycopg.connect(s.connection_dsn(cluster, "reader")) as c:
        archive.reader_guard(c, cluster)
        before = rows(c)
    runtime = s.api_environment(s.connection_dsn(cluster, "api"))
    payload = {
        "backend": plan["backend"],
        "fixture": {"ids": fixture["ids"]},
        "credential": {key: credential[key] for key in ("login_name", "password", "old_cookie")},
    }
    completed = subprocess.run(  # nosec B603
        [
            str(s.ROOT / ".venv/bin/python"),
            "-I",
            str(s.ROOT / "deploy/application_recovery/runtime_child.py"),
        ],
        input=json.dumps(payload).encode(),
        env=runtime,
        cwd=s.ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    if completed.returncode or len(completed.stdout) > 1024 * 1024:
        raise s.RecoveryError("ISOLATED_API_ACCEPTANCE_FAILED_PRIVATE_DETAILS_SUPPRESSED")
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        raise s.RecoveryError("API_ACCEPTANCE_RECEIPT_INVALID") from None
    if result.get("new_sync_work_verified") is not True:
        raise s.RecoveryError("API_ACCEPTANCE_RECEIPT_NOT_VERIFIED")
    with psycopg.connect(s.connection_dsn(cluster, "reader")) as c:
        after = rows(c)
    for key, counter in before.items():
        if counter - after[key]:
            raise s.RecoveryError("RESTORED_EXISTING_HISTORY_WAS_MODIFIED")
    for key in (("design", "outbox"), ("design", "operations"), ("design", "model_calls")):
        if before[key] != after[key]:
            raise s.RecoveryError("RESTORED_OLD_QUEUE_OR_UNKNOWN_FEE_CHANGED")
    result.update(existing_history_unchanged=True, old_queues_and_unknown_fees_unchanged=True)
    return result


def runtime_check(plan, fixture, credential):
    """Child receives exactly API-purpose environment and no private infrastructure receipts."""
    s.validate_api_environment(os.environ)
    binding.install(plan["backend"])
    runtime = os.environ
    import apps.api.main as implementation
    from apps.api.main import app

    if not str(implementation.__file__).startswith(plan["backend"]["source_root"] + "/"):
        raise s.RecoveryError("APPLICATION_NOT_FROM_FROZEN_INPUTS")
    paused = PausedApplication(app, fixture)
    with TestClient(paused, base_url="http://127.0.0.1:5199") as client:
        client.cookies.set("session", credential["old_cookie"])
        if client.get("/api/v1/auth/session").status_code != 401:
            raise s.RecoveryError("OLD_SESSION_COOKIE_ACCEPTED")
        login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": runtime["APP_ORIGIN"]},
            json={k: credential[k] for k in ("login_name", "password")},
        )
        if login.status_code != 200:
            raise s.RecoveryError("REAL_APPLICATION_LOGIN_FAILED")
        csrf = login.json()["csrf_token"]
        paths = {
            "project": "/api/v1/bids/" + fixture["ids"]["bid"],
            "knowledge": "/api/v1/knowledge/" + fixture["ids"]["knowledge"],
            "report": "/api/v1/report-exports/" + fixture["ids"]["report"],
        }
        for path in paths.values():
            if client.get(path).status_code != 200:
                raise s.RecoveryError("RESTORED_APPLICATION_READ_FAILED")
        headers = {
            "Origin": runtime["APP_ORIGIN"],
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "ar-" + uuid4().hex,
        }
        denied = client.post("/api/v1/bids", json=paused.create_body)
        if denied.status_code != 403:
            raise s.RecoveryError("REAL_APPLICATION_CSRF_NOT_ENFORCED")
        created = client.post("/api/v1/bids", headers=headers, json=paused.create_body)
        if created.status_code != 201:
            raise s.RecoveryError("REAL_APPLICATION_NEW_WORK_FAILED")
        new_bid = created.json()["id"]
        paused.bind_new_bid(new_bid)
        replayed = client.post("/api/v1/bids", headers=headers, json=paused.create_body)
        if (
            replayed.status_code != 201
            or replayed.json()["id"] != new_bid
            or replayed.headers.get("Idempotent-Replayed") != "true"
        ):
            raise s.RecoveryError("REAL_APPLICATION_IDEMPOTENCY_FAILED")
        path = "/api/v1/bids/" + new_bid + "/settings"
        current = client.get(path)
        patch_headers = {
            **headers,
            "Idempotency-Key": "ar-" + uuid4().hex,
            "If-Match": current.headers["ETag"],
        }
        changed = client.patch(path, headers=patch_headers, json={"result_reminder": False})
        if changed.status_code != 200 or changed.json()["settings"]["result_reminder"] is not False:
            raise s.RecoveryError("REAL_APPLICATION_SYNCHRONOUS_SAVE_FAILED")
        if client.get("/api/v1/bids/" + new_bid).status_code != 200:
            raise s.RecoveryError("NEW_SYNCHRONOUS_WORK_READ_FAILED")
        for method, path in (
            ("PATCH", paths["project"] + "/settings"),
            ("POST", paths["project"] + "/uploads"),
            ("GET", "/api/v1/operations/" + fixture["ids"]["operation"]),
        ):
            if client.request(method, path, headers=headers, json={}).status_code != 423:
                raise s.RecoveryError("RECOVERY_PAUSED_BOUNDARY_BYPASSED")
    return {
        "login_verified": True,
        "restored_reads": list(paths),
        "old_cookie_rejected": True,
        "csrf_verified": True,
        "idempotency_verified": True,
        "new_sync_work_verified": True,
        "new_bid_id": new_bid,
        "work_state": "work_paused_with_single_sync_test_exception",
        "old_inflight_resume_qualified": False,
        "consumers_started": False,
        "provider_calls": 0,
        "runtime_excludes_backup_worker_owner_and_model_credentials": True,
        "module_origins": binding.require().origins(),
    }
