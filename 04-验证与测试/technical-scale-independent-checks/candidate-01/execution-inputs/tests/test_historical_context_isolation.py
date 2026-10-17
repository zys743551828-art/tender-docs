"""Current proof checks reject old snapshots before lookup or idempotent replay."""

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest

from apps.api import historical_routes
from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, connection
from packages.model_governance import historical_repository as repo
from packages.rag.indexing import original_source_id
from tests.test_model_governance_historical_integration import fixture, save_subject

pytest_plugins = ["tests.test_model_governance_historical_integration"]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_ISOLATION") != "1",
    reason="Explicit isolated nonowner PG test only",
)


@pytest.fixture(autouse=True)
def no_external_ports(monkeypatch, client):
    def forbidden(*args, **kwargs):
        raise AssertionError("External object or model ports are forbidden in this mechanism test")

    for name in ("client", "get", "put"):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(forbidden))


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE", "READ UNCOMMITTED"])
def test_context_guard_runs_before_submission_lookup(isolation):
    with connection() as c:
        # Only constants from this test's parametrization are composed, not caller input.
        c.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
        c.execute("SELECT 1").fetchone()
        with pytest.raises(DomainError) as failure:
            repo.lock_submission(c, uuid4())
        assert failure.value.code == "HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED"


def test_confirmation_replay_cannot_publish_withdrawn_proof_from_established_rr(
    client, monkeypatch
):
    web, _, headers, _ = client
    item, proof, projection = fixture(client)
    pending = save_subject(client, item, proof, projection)
    assert pending.status_code == 201
    identity = pending.json()["id"]
    path = f"/api/v1/historical-proof/subjects/{identity}/confirm"
    body = {"reason": "独立来源撤回后的旧快照重放机制"}
    stable = headers(pending.headers["etag"])
    confirmed = web.post(path, json=body, headers=stable)
    assert confirmed.status_code == 200
    unchanged = web.post(path, json=body, headers=stable)
    assert unchanged.status_code == 200 and unchanged.headers["Idempotency-Replayed"] == "true"
    with connection() as c:
        decision_count = c.execute(
            "SELECT count(*) AS n FROM historical_backtest.decisions WHERE object_kind='subject' AND object_id=%s",
            (identity,),
        ).fetchone()["n"]
    with connection() as stale:
        stale.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        stale.execute("SELECT count(*) FROM historical_backtest.subjects").fetchone()
        with connection() as writer:
            writer.execute("SELECT pg_advisory_xact_lock(90127,1)")
            writer.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
                (original_source_id(proof["document_version_id"]),),
            )

        @contextmanager
        def old_snapshot():
            yield stale

        with monkeypatch.context() as scoped:
            scoped.setattr(historical_routes, "connection", old_snapshot)
            rejected = web.post(path, json=body, headers=stable)
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED"
        assert "Idempotency-Replayed" not in rejected.headers
    current = web.get("/api/v1/historical-proof/subjects/" + identity)
    assert current.status_code == 200 and not current.json()["proofs_current"]
    replay = web.post(path, json=body, headers=stable)
    assert replay.status_code == 409 and replay.json()["code"] == "HISTORICAL_AUTHORITY_STALE"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM historical_backtest.decisions WHERE object_kind='subject' AND object_id=%s",
                (identity,),
            ).fetchone()["n"]
            == decision_count
        )
