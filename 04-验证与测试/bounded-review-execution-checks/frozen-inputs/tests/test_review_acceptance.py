"""The acceptance envelope is enforced before any model delegation, including concurrency."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

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
