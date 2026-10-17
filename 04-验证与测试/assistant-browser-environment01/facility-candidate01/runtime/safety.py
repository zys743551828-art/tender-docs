"""Explicit test process configuration; no implicit repository .env or paid network."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

MARKER = "explicit-mock-no-provider-no-kafka-local-storage"
MODES = {"hold", "answer", "knowledge_action", "unknown"}


def configuration(path: Path) -> dict:
    value = json.loads(path.read_text())
    required = {
        "marker",
        "candidate",
        "runtime_root",
        "database_name",
        "origin",
        "port",
        "objects",
        "control",
        "evidence",
        "member_ids",
        "api_dsn",
        "worker_dsn",
        "session_secret",
        "csrf_secret",
    }
    if set(value) != required or value["marker"] != MARKER:
        raise ValueError("Explicit fixture configuration required")
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Fixture configuration must be private (0600)")
    if value["origin"] != "http://127.0.0.1:5184" or value["port"] != 8816:
        raise ValueError("Dedicated browser fixture Origin and API port required")
    if len({UUID(item) for item in value["member_ids"]}) != 2:
        raise ValueError("Exactly two dedicated fixture members required")
    if value["database_name"] != "tender_checks_assistant_ui01":
        raise ValueError("Dedicated assistant browser database required")
    for key in ("api_dsn", "worker_dsn"):
        parsed = urlsplit(value[key])
        if (
            parsed.scheme not in {"postgresql", "postgres"}
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.path != "/" + value["database_name"]
            or parsed.query
            or not parsed.username
            or not parsed.password
        ):
            raise ValueError("Only dedicated loopback URI DSNs without query options")
    if urlsplit(value["api_dsn"]).username == urlsplit(value["worker_dsn"]).username:
        raise ValueError("Independent API and Worker LOGINs required")
    return value


def verify_candidate(value: dict) -> dict:
    manifest = Path(value["candidate"])
    candidate = json.loads(manifest.read_text())
    root = Path(value["runtime_root"]).resolve()
    if root != Path(candidate["runtime_root"]).resolve():
        raise ValueError("Frozen runtime root mismatch")
    for item in candidate["sources"]:
        path = (root / item["path"]).resolve()
        if (
            not path.is_relative_to(root)
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ValueError("Frozen candidate source mismatch")
    return {
        "candidate_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "source_count": len(candidate["sources"]),
        "runtime_root": str(root),
    }


def environment(value: dict, mode: str) -> dict[str, str]:
    role = "worker" if mode == "worker" else "api"
    # Do not copy arbitrary parent env (including proxies, owner/logins, keys or PYTHONPATH).
    result = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "WORKBENCH_ENV_FILE": "/dev/null",
        "WORKBENCH_RUNTIME_ROLE": role,
        "OBJECT_STORE_BACKEND": "explicit-local-fixture",
        "OBJECT_STORE_BUCKET": "explicit-synthetic-browser-fixture",
        "OBJECT_STORE_PREFIX": "assistant-fixture/",
        f"{role.upper()}_DATABASE_DSN": value[f"{role}_dsn"],
    }
    if mode == "api":
        result.update(
            {
                "APP_ORIGIN": value["origin"],
                "SESSION_SECRET": value["session_secret"],
                "CSRF_SECRET": value["csrf_secret"],
            }
        )
    return result


def install_network_guard(record=None):
    import httpx

    def deny(*args, **kwargs):
        if record:
            record({"event": "blocked_real_http_transport"})
        raise RuntimeError("Explicit fixture blocks real HTTP transport")

    async def deny_async(*args, **kwargs):
        if record:
            record({"event": "blocked_real_http_transport"})
        raise RuntimeError("Explicit fixture blocks real HTTP transport")

    httpx.HTTPTransport.handle_request = deny
    httpx.AsyncHTTPTransport.handle_async_request = deny_async
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def allowed(address):
        if isinstance(address, str):
            return False  # No arbitrary Unix socket service escape.
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return False

    def connect(sock, address):
        if not allowed(address):
            if record:
                record({"event": "blocked_nonloopback_socket"})
            raise RuntimeError("Explicit fixture blocks non-loopback socket")
        return original(sock, address)

    def connect_ex(sock, address):
        if not allowed(address):
            if record:
                record({"event": "blocked_nonloopback_socket"})
            raise RuntimeError("Explicit fixture blocks non-loopback socket")
        return original_ex(sock, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]


def read_control(path: Path):
    value = json.loads(path.read_text())
    if set(value) != {"marker", "mode", "scopes"} or value["marker"] != MARKER:
        raise ValueError("Explicit fixture control required")
    if (
        value["mode"] not in MODES
        or not value["scopes"]
        or not set(value["scopes"]) <= {"global", "project"}
    ):
        raise ValueError("Invalid fixture control")
    return value
