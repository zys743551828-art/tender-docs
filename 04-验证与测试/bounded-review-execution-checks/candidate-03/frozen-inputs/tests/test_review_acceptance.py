"""The acceptance envelope is enforced before any model delegation, including concurrency."""

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from deploy import review_acceptance as acceptance
from deploy.review_acceptance import AcceptanceBoundary, exclusive_window
from packages.domain.runtime import DomainError


def invoke(boundary, bid, *, logical_key="extract-rules", **extra):
    return boundary(
        {"bid_id": str(bid)},
        {},
        logical_key=logical_key,
        messages=[{"role": "user", "content": "synthetic"}],
        json_schema={},
        **extra,
    )


def test_concurrent_window_has_only_two_upstream_invocations():
    bid = uuid4()
    sent = []
    boundary = AcceptanceBoundary(bid, lambda *a, **kw: sent.append(True), lambda: None)

    def call(index):
        try:
            invoke(boundary, bid, logical_key="extract-rules" if index % 2 else "core-judgment")
            return True
        except DomainError as error:
            assert error.code in ("ACCEPTANCE_CALL_LIMIT", "ACCEPTANCE_LOGICAL_CALL_REPEATED")
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(call, range(20))) == 2
    assert len(sent) == boundary.started == 2


def test_foreign_project_or_large_output_never_delegates():
    bid = uuid4()
    boundary = AcceptanceBoundary(bid, lambda *a, **kw: pytest.fail("Must not send"), lambda: None)
    for selected, tokens in ((uuid4(), 100), (bid, 8193)):
        with pytest.raises(DomainError) as error:
            invoke(boundary, selected, max_tokens=tokens)
        assert error.value.code == "ACCEPTANCE_SCOPE_REJECTED"
    assert boundary.started == 0


def test_unknown_failure_consumes_window_and_source_change_blocks_send():
    bid = uuid4()

    def failed(*a, **kw):
        raise DomainError("MODEL_TIMEOUT", "Synthetic unknown", 503)

    boundary = AcceptanceBoundary(bid, failed, lambda: None)
    with pytest.raises(DomainError):
        invoke(boundary, bid)
    assert boundary.started == 1

    def changed():
        raise DomainError("ACCEPTANCE_SOURCE_CHANGED", "Synthetic changed", 409)

    boundary.verify_sources = changed
    with pytest.raises(DomainError) as error:
        invoke(boundary, bid)
    assert error.value.code == "ACCEPTANCE_SOURCE_CHANGED" and boundary.started == 1


def test_deadline_and_oversized_prompt_are_rejected_before_send():
    bid = uuid4()
    boundary = AcceptanceBoundary(
        bid, lambda *a, **kw: pytest.fail("Must not send"), lambda: None, deadline=0
    )
    with pytest.raises(DomainError):
        invoke(boundary, bid)
    boundary.deadline = float("inf")
    with pytest.raises(DomainError):
        boundary(
            {"bid_id": str(bid)},
            {},
            logical_key="core-judgment",
            messages=[{"role": "user", "content": "x" * 25000}],
            json_schema={},
        )
    assert boundary.started == 0


@pytest.mark.skipif(
    os.environ.get("RUN_ACCEPTANCE_PG") != "1", reason="Explicit restricted PG opt-in"
)
def test_real_restricted_pg_window_excludes_second_executor_and_releases():
    bid = uuid4()
    with exclusive_window(bid):
        with pytest.raises(DomainError) as error:
            with exclusive_window(bid):
                pytest.fail("Second executor must not enter")
        assert error.value.code == "ACCEPTANCE_WINDOW_BUSY"
    with exclusive_window(bid):
        pass  # Window is released on exit; no business/model row or owner privilege needed.


def test_manifest_identity_dependencies_and_actual_import_directory(tmp_path, monkeypatch):
    assert {
        "apps/worker/engine.py",
        "packages/domain/model_calls.py",
        "packages/domain/providers/deepseek.py",
        "deploy/review_acceptance.py",
    } <= acceptance.REQUIRED_SOURCES
    monkeypatch.setattr(
        acceptance, "REQUIRED_SOURCES", {"README.md", "deploy/review_acceptance.py"}
    )
    rows = [
        {"path": name, "sha256": hashlib.sha256((acceptance.ROOT / name).read_bytes()).hexdigest()}
        for name in acceptance.REQUIRED_SOURCES
    ]
    path = tmp_path / "execution.json"
    path.write_text(json.dumps({"sources": rows}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    verify = acceptance.frozen_sources(path, digest)
    verify()
    with monkeypatch.context() as patches:
        patches.setitem(
            sys.modules,
            "deploy.review_acceptance",
            SimpleNamespace(__file__=str(tmp_path / "wrong.py")),
        )
        with pytest.raises(DomainError) as error:
            verify()
        assert error.value.code == "ACCEPTANCE_IMPORT_MISMATCH"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(DomainError) as error:
        verify()
    assert error.value.code == "ACCEPTANCE_MANIFEST_CHANGED"
    path.write_text(json.dumps({"sources": rows[:1]}))
    with pytest.raises(DomainError) as error:
        acceptance.frozen_sources(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert error.value.code == "ACCEPTANCE_DEPENDENCIES_MISSING"


def fixture_scope(tmp_path, monkeypatch):
    from packages.domain import review_data

    bid, command, operation = uuid4(), uuid4(), uuid4()
    doc = {
        "role": "tender",
        "document_version_id": str(uuid4()),
        "coverage": "complete",
        "gaps": [],
        "pages": [{"physical_page": 1, "quality": "usable", "gaps": []}],
        "blocks": [{"id": str(uuid4()), "text_content": "90 days", "physical_page": 1}],
    }
    body = deepcopy(doc) | {"role": "body", "document_version_id": str(uuid4())}
    body["blocks"] = [{"id": str(uuid4()), "text_content": "120 days", "physical_page": 1}]
    state = {
        "heads": {
            "current_body_id": body["document_version_id"],
            "current_material_id": str(uuid4()),
            "material_revision": 2,
        },
        "fact": None,
    }
    docs = [doc, body]
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"bid_id": str(bid), "heads": state["heads"], "documents": docs}))

    class Cursor:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Database:
        def execute(self, query, args=None):
            if "FROM operations" in query:
                return Cursor(
                    {
                        "bid_id": bid,
                        "command_id": command,
                        "handler_version": "prepare/1",
                        "input_digest": "a" * 64,
                    }
                )
            if "FROM bid_profiles" in query:
                return Cursor(state["heads"])
            if "FROM fact_heads" in query:
                return Cursor({"fact_set_id": state["fact"]})
            return Cursor(None)

    @contextmanager
    def connect(worker):
        assert worker
        yield Database()

    monkeypatch.setattr(review_data, "read_bundle", lambda *a: docs)
    scope = acceptance.FixtureScope(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), bid, command, connect
    )
    message = {
        "bid_id": str(bid),
        "operation_id": str(operation),
        "handler_version": "prepare/1",
        "input_digest": "a" * 64,
    }
    content = {
        "bid_id": str(bid),
        "documents": [
            {
                "role": doc["role"],
                "document_version_id": doc["document_version_id"],
                "coverage": doc["coverage"],
                "gaps": doc["gaps"],
                "pages": doc["pages"],
                "evidence_blocks": doc["blocks"],
            }
        ],
    }
    messages = [{"role": "user", "content": json.dumps(content)}]
    return scope, state, message, messages, path


def test_exact_original_payload_accepts_then_rejects_unapproved_page(tmp_path, monkeypatch):
    scope, _, message, messages, _ = fixture_scope(tmp_path, monkeypatch)
    scope.verify(message, "extract-rules", messages)
    data = json.loads(messages[0]["content"])
    data["documents"][0]["pages"].append({"physical_page": 2})
    messages[0]["content"] = json.dumps(data)
    with pytest.raises(DomainError) as error:
        scope.verify(message, "extract-rules", messages)
    assert error.value.code == "ACCEPTANCE_PAYLOAD_MISMATCH"


@pytest.mark.parametrize("change", ["heads", "facts", "command", "manifest", "operation"])
def test_fixture_head_fact_command_and_manifest_boundaries(tmp_path, monkeypatch, change):
    scope, state, message, messages, path = fixture_scope(tmp_path, monkeypatch)
    if change == "heads":
        state["heads"]["material_revision"] += 1
    elif change == "facts":
        state["fact"] = uuid4()
    elif change == "command":
        scope.command_id = uuid4()
    elif change == "manifest":
        path.write_text(path.read_text() + "\n")
    else:
        del message["operation_id"]
    with pytest.raises(DomainError) as error:
        scope.verify(message, "extract-rules", messages)
    assert error.value.code in {
        "ACCEPTANCE_INPUT_CHANGED",
        "ACCEPTANCE_COMMAND_MISMATCH",
        "ACCEPTANCE_FIXTURE_CHANGED",
    }
