"""Fixture isolation/transport tests; never open a project database or real provider."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from qa.assistant_browser_fixture.local_storage import LocalObjects
from qa.assistant_browser_fixture.mock_provider import answer, client_for, install_api_profile_only
from qa.assistant_browser_fixture.safety import MARKER, environment, read_control, verify_candidate


def test_role_environment_is_positive_and_single_dsn(monkeypatch):
    monkeypatch.setenv("OWNER_DATABASE_DSN", "never-forward")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "never-forward")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-forward")
    value = {
        "origin": "http://127.0.0.1:5184",
        "session_secret": "test-session",
        "csrf_secret": "test-csrf",
        "api_dsn": "api-only",
        "worker_dsn": "worker-only",
    }
    for mode in ("api", "publisher", "seed", "worker"):
        env = environment(value, mode)
        role = "worker" if mode == "worker" else "api"
        assert {key for key in env if key.endswith("_DATABASE_DSN")} == {
            role.upper() + "_DATABASE_DSN"
        }
        assert "DEEPSEEK_API_KEY" not in env and "OWNER_DATABASE_DSN" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env and "PYTHONPATH" not in env
        assert env["WORKBENCH_ENV_FILE"] == "/dev/null"
        if mode != "api":
            assert "SESSION_SECRET" not in env and "CSRF_SECRET" not in env


@pytest.mark.parametrize(
    "key", ["../escape", "a/../escape", "/absolute", "a//b", "a\\b", "https://example.com/object"]
)
def test_local_object_traversal_and_external_address_rejected(tmp_path, key):
    tmp_path.chmod(0o700)
    store = LocalObjects(tmp_path)
    with pytest.raises(ValueError):
        store.put(key, b"synthetic", "text/plain")


def test_local_object_exact_bytes_and_symlink_refusal(tmp_path):
    tmp_path.chmod(0o700)
    store = LocalObjects(tmp_path)
    store.put("blobs/test-01", b"synthetic\x00bytes", "application/octet-stream")
    assert store.get("blobs/test-01") == b"synthetic\x00bytes"
    assert store.get_object(Bucket="explicit-fixture", Key="blobs/test-01")["ContentLength"] == 15
    store.path("blobs/link").symlink_to(store.path("blobs/test-01"))
    with pytest.raises(OSError):
        store.get("blobs/link")


def test_control_rejects_implicit_mode_or_paid_scope(tmp_path):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"marker": MARKER, "mode": "hold", "scopes": ["global"]}))
    assert read_control(path)["mode"] == "hold"
    path.write_text(json.dumps({"marker": MARKER, "mode": "real", "scopes": ["global"]}))
    with pytest.raises(ValueError):
        read_control(path)
    path.write_text(json.dumps({"marker": MARKER, "mode": "answer", "scopes": ["review"]}))
    with pytest.raises(ValueError):
        read_control(path)


def test_candidate_drift_refused_before_product_import(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "module.py").write_text("changed")
    path = tmp_path / "candidate.json"
    path.write_text(
        json.dumps(
            {"runtime_root": str(root), "sources": [{"path": "module.py", "sha256": "0" * 64}]}
        )
    )
    with pytest.raises(ValueError, match="source mismatch"):
        verify_candidate({"candidate": str(path), "runtime_root": str(root)})


def manifest():
    return {
        "context": {
            "target_bid_ids": [str(uuid4())],
            "sources": [
                {
                    "source_key": "source-01",
                    "text": "合成原件原文。",
                    "quote_ranges": [{"start": 0, "end": 7}],
                    "provenance": {
                        "kind": "original_document",
                        "document_version_id": str(uuid4()),
                    },
                }
            ],
        }
    }


def test_mock_has_visible_marker_exact_citation_and_explicit_proposal():
    source = manifest()
    result = answer(source, "knowledge_action")
    assert "显式离线合成" in result["answer"]
    assert result["citations"][0]["quote"] in source["context"]["sources"][0]["text"]
    assert result["tool_proposals"][0]["target_bid_id"] == source["context"]["target_bid_ids"][0]
    source["context"]["sources"] = []
    with pytest.raises(ValueError):
        answer(source, "knowledge_action")


def test_network_guard_blocks_http_and_nonloopback_but_mock_survives():
    facility = Path(__file__).resolve().parents[1] / "qa/assistant_browser_fixture"
    code = """
import sys, socket, asyncio, httpx
sys.path.insert(0, sys.argv[1])
from safety import install_network_guard
install_network_guard()
for url in ['https://api.deepseek.com/v1/chat/completions', 'http://127.0.0.1:1']:
    try: httpx.get(url, trust_env=False)
    except RuntimeError as error: assert 'blocks real HTTP' in str(error)
    else: raise AssertionError('HTTP was not blocked')
try: socket.socket().connect(('198.51.100.1',443))
except RuntimeError as error: assert 'non-loopback' in str(error)
else: raise AssertionError('socket was not blocked')
with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'explicit_mock':True}))) as client:
    assert client.get('https://api.deepseek.com').json()['explicit_mock']
async def probe():
    async with httpx.AsyncClient(trust_env=False) as client:
        try: await client.get('https://api.deepseek.com')
        except RuntimeError as error: assert 'blocks real HTTP' in str(error)
        else: raise AssertionError('Async HTTP was not blocked')
asyncio.run(probe())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(facility)],
        capture_output=True,
        text=True,
        timeout=20,
        env={"PATH": os.defpath},
    )
    assert result.returncode == 0, result.stderr


def test_unknown_is_mock_timeout_and_never_default_transport():
    events = []
    client = client_for(manifest(), "unknown", events.append)
    assert isinstance(client._transport, httpx.MockTransport)
    with pytest.raises(httpx.ReadTimeout):
        client._transport.handle_request(
            httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
        )
    assert events == [{"event": "mock_request", "mode": "unknown", "real_provider_attempts": 0}]


def test_api_can_freeze_profile_but_never_generate(monkeypatch):
    from packages.domain.providers.deepseek import DeepSeekClient

    monkeypatch.setattr(DeepSeekClient, "from_env", DeepSeekClient.from_env)
    install_api_profile_only()
    client = DeepSeekClient.from_env()
    assert isinstance(client._transport, httpx.MockTransport)
    with pytest.raises(RuntimeError, match="cannot execute generation"):
        client._transport.handle_request(
            httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
        )
