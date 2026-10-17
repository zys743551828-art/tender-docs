"""Business fixture role. No multi-role DSNs, parent secrets or network provider."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID

# -I ignores script directory; explicitly add only this test facility and frozen product root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety import MARKER, install_network_guard, read_control, verify_candidate  # noqa: E402

STOP = threading.Event()
LAST_MODULE_NAMES = None
CURRENT_REQUEST: ContextVar = ContextVar("explicit_fixture_request", default=None)


def module_binding(value):
    global LAST_MODULE_NAMES
    names = tuple(
        sorted(name for name in sys.modules if name.split(".")[0] in {"apps", "packages"})
    )
    if names == LAST_MODULE_NAMES:
        return
    root = Path(value["runtime_root"]).resolve()
    manifest = json.loads(Path(value["candidate"]).read_text())
    expected = {item["path"]: item["sha256"] for item in manifest["sources"]}
    modules = []
    for name, module in sorted(sys.modules.items()):
        if name.split(".")[0] not in {"apps", "packages"}:
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError("Product module loaded outside frozen fixture root")
        relative = str(resolved.relative_to(root))
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if expected.get(relative) != digest:
            raise RuntimeError("Loaded product module not in frozen candidate")
        modules.append({"module": name, "path": str(resolved), "sha256": digest})
    output = Path(value["evidence"]) / (value["mode"] + "-loaded-modules.json")
    output.write_text(
        json.dumps(
            {"pid": os.getpid(), "candidate_sha256": value["candidate_sha256"], "modules": modules},
            indent=2,
        )
        + "\n"
    )
    LAST_MODULE_NAMES = names


def record(value, event):
    path = Path(value["evidence"]) / (value["mode"] + "-events.jsonl")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def worker_tick(value):
    from local_storage import LocalObjects
    from mock_provider import client_for

    from packages.assistant import global_runtime
    from packages.domain.providers.deepseek import DeepSeekClient
    from packages.domain.runtime import connection

    control = read_control(Path(value["control"]))
    if control["mode"] == "hold":
        return
    members = [UUID(item) for item in value["member_ids"]]
    # Worker-only role checks every manifest/job; API final authority is a separate process.
    with connection(True) as c:
        rows = (
            c.execute(
                """SELECT j.id,j.scope,j.input_manifest,o.payload
            FROM assistant_jobs j JOIN assistant_global_outbox o ON o.run_id=j.id
            JOIN assistant_global_runs r ON r.id=j.id
            WHERE j.actor_id=ANY(%s) AND j.scope='global' AND j.kind='answer'
            AND r.state IN ('queued','running')
            AND (r.state='queued' OR r.lease_until<=clock_timestamp()) ORDER BY j.created_at LIMIT 10""",
                (members,),
            ).fetchall()
            if "global" in control["scopes"]
            else []
        )
        if "project" in control["scopes"]:
            rows += c.execute(
                """SELECT j.id,j.scope,j.input_manifest,o.payload FROM assistant_jobs j
                JOIN operations p ON p.id=j.operation_id JOIN outbox o ON o.operation_id=p.id
                WHERE j.actor_id=ANY(%s) AND j.scope='project' AND j.kind='answer'
                AND p.state IN ('queued','running','cancel_requested')
                AND o.payload->>'handler_version'='assistant/1' ORDER BY j.created_at LIMIT 10""",
                (members,),
            ).fetchall()
    LocalObjects(Path(value["objects"])).install()
    for row in rows:
        client = client_for(
            row["input_manifest"],
            control["mode"],
            lambda event, identity=row["id"]: record(value, event | {"job_id": str(identity)}),
        )
        DeepSeekClient.from_env = classmethod(lambda cls, selected=client: selected)
        if row["scope"] == "global":
            global_runtime.handle(row["payload"])
        else:
            from apps.worker import engine

            if "assistant/1" not in engine.LANE_HANDLERS["assistant"]:
                raise RuntimeError("Frozen engine has no actual assistant registry")
            engine.handle_lane("assistant", row["payload"])
        record(
            value,
            {
                "event": "handled",
                "job_id": str(row["id"]),
                "scope": row["scope"],
                "transport": "explicit-direct-handler-not-kafka",
            },
        )
        module_binding(value)


def publisher_tick(value):
    from packages.assistant import coordinator
    from packages.domain.runtime import connection

    with connection() as c:
        rows = c.execute(
            "SELECT id FROM assistant_jobs WHERE actor_id=ANY(%s) AND kind='answer' ORDER BY created_at LIMIT 1000",
            ([UUID(item) for item in value["member_ids"]],),
        ).fetchall()
    if rows:
        coordinator.tick([row["id"] for row in rows])
    module_binding(value)


def serve_api(value):
    import uvicorn
    from mock_provider import install_api_profile_only

    from apps.api.export_openapi import implemented_openapi
    from apps.api.main import app
    from apps.api.task_draft_routes import router
    from packages.tasks.contracts import extend_openapi

    install_api_profile_only()
    app.include_router(router)

    def local_download_url(key, method="get_object", extra=None, seconds=900):
        from apps.api.auth import auth
        from apps.api.object_transfer import make_download_url
        from packages.domain.runtime import DomainError, connection, fetch

        request = CURRENT_REQUEST.get()
        if (
            request is None
            or request.method != "GET"
            or method != "get_object"
            or "artifact_id" not in request.path_params
        ):
            raise DomainError("FIXTURE_DOWNLOAD_ONLY", "本地合成夹具仅支持经鉴权的原件下载", 503)
        version = request.path_params["artifact_id"]
        session = auth(request)
        with connection() as c:
            row = fetch(
                c,
                "SELECT v.original_name,b.object_key,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
                (version,),
            )
        if row["object_key"] != key:
            raise DomainError("FIXTURE_OBJECT_MISMATCH", "合成原件定位不符", 409)
        return make_download_url(
            request,
            version,
            session=session,
            object_key=key,
            filename=row["original_name"],
            byte_size=row["byte_size"],
        )

    from packages.domain import storage

    storage.sign = local_download_url  # Explicit local port bridges to actual authenticated proxy.

    def actual():
        result = implemented_openapi(app)
        extend_openapi(result)
        return result

    app.openapi = actual

    @app.middleware("http")
    async def fixture_marker(request, call_next):
        token = CURRENT_REQUEST.set(request)
        try:
            response = await call_next(request)
            response.headers["X-Assistant-Fixture"] = MARKER
            return response
        finally:
            CURRENT_REQUEST.reset(token)

    document = actual()
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    record(
        value,
        {
            "event": "actual_contract",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "paths": len(document["paths"]),
            "operations": sum(
                method in {"get", "post", "put", "patch", "delete"}
                for methods in document["paths"].values()
                for method in methods
            ),
        },
    )
    module_binding(value)
    uvicorn.run(app, host="127.0.0.1", port=value["port"], access_log=False, log_level="warning")


def main():
    value = json.loads(Path(sys.argv[1]).read_text())
    binding = verify_candidate(value)
    role = "worker" if value["mode"] == "worker" else "api"
    if set(key for key in os.environ if key.endswith("_DATABASE_DSN")) != {
        role.upper() + "_DATABASE_DSN"
    }:
        raise RuntimeError("Fixture business role received mixed DSNs")
    if any(
        key in os.environ
        for key in (
            "DEEPSEEK_API_KEY",
            "OBJECT_STORE_ACCESS_KEY",
            "OBJECT_STORE_SECRET_KEY",
            "OWNER_DATABASE_DSN",
        )
    ):
        raise RuntimeError("Unexpected provider/cloud/owner capability")
    if any(key in value for key in ("api_dsn", "worker_dsn", "session_secret", "csrf_secret")):
        raise RuntimeError("Business input contains multi-role secret configuration")
    sys.path.insert(0, value["runtime_root"])
    install_network_guard(lambda event: record(value, event))
    from local_storage import LocalObjects

    from packages.domain.runtime import assert_role, connection

    assert_role(role == "worker")
    with connection(role == "worker") as c:
        identity = c.execute(
            "SELECT current_database() AS database,current_user AS login"
        ).fetchone()
        if identity["database"] != value["database_name"]:
            raise RuntimeError("Dedicated database identity mismatch")
    LocalObjects(Path(value["objects"])).install()
    record(
        value,
        {
            "event": "startup",
            "marker": MARKER,
            "pid": os.getpid(),
            "uid": os.getuid(),
            "role": role,
            "database": identity["database"],
            "independent_nonowner_login": True,
            "single_dsn": True,
            "provider_key_present": False,
            "cloud_credentials_present": False,
            "fixture_root": str(Path(__file__).resolve().parent),
            "fixture_sources": [
                {
                    "path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(Path(__file__).resolve().parent.glob("*.py"))
            ],
            **binding,
        },
    )
    module_binding(value)
    if value["mode"] == "api":
        serve_api(value)
        return
    if value["mode"] == "seed":
        from seed import seed

        seed(value)
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    tick = worker_tick if value["mode"] == "worker" else publisher_tick
    while not STOP.is_set():
        tick(value)
        STOP.wait(0.6)


if __name__ == "__main__":
    main()
