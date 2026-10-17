"""Closed score interfaces and process isolation, no DB/provider/bucket operations."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from apps.api.scoring_routes import ScoreInputRequest, ScoreJobRequest, router
from packages.domain.runtime import DomainError
from packages.model_governance.common import GovernanceError
from packages.model_governance.scoring_contracts import extend_openapi
from packages.model_governance.scoring_profiles import environment
from packages.model_governance.scoring_runtime import execute_job, score_connection


def test_eleven_actual_operations_closed_schema_no_cert_or_paid_endpoint():
    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    operations = [
        (path, method)
        for path, item in actual["paths"].items()
        for method in item
        if method in {"get", "post"}
    ]
    assert len(operations) == 11
    assert not any("certification" in path or "paid" in path for path, _ in operations)
    for path, method in operations:
        for status, response in actual["paths"][path][method]["responses"].items():
            if status in {"200", "201"}:
                assert response["content"]["application/json"]["schema"]["$ref"].startswith(
                    "#/components/schemas/"
                )
    assert actual["components"]["schemas"]["ScoreInputRequest"]["additionalProperties"] is False
    assert actual["components"]["schemas"]["ScoreCapabilities"]["properties"][
        "paid_execution_enabled"
    ] == {"const": False}


@pytest.mark.parametrize(
    "extra",
    [
        {"origin": "real"},
        {"maximum_verified": True},
        {"technical_maximum": "60"},
        {"labels": [0]},
        {"prompt": "arbitrary request"},
        {"profile_digest": "a" * 64},
    ],
)
def test_score_input_does_not_accept_client_authority(extra):
    with pytest.raises(ValidationError):
        ScoreInputRequest.model_validate(
            {"review_id": str(uuid4()), "reason": "合成完整范围"} | extra
        )


@pytest.mark.parametrize(
    "value", [{}, {"review_id": str(uuid4()), "historical_input_id": str(uuid4())}]
)
def test_exactly_one_server_input(value):
    with pytest.raises(ValidationError):
        ScoreInputRequest.model_validate(value | {"reason": "合成"})


@pytest.mark.parametrize("budget", [1, 0, False, "-1", "NaN", "Infinity", "1e2"])
def test_budget_requires_explicit_finite_decimal_string(budget):
    with pytest.raises(ValidationError):
        ScoreJobRequest.model_validate({"reason": "合成预算", "budget": budget})


def test_score_profile_does_not_inherit_general_keys_and_disables_dotenv(tmp_path, monkeypatch):
    profile = tmp_path / "purpose.env"
    profile.write_text("MODEL_SCORE_DATABASE_DSN='postgresql://explicit-synthetic-score'\n")
    profile.chmod(0o600)
    monkeypatch.setenv("API_DATABASE_DSN", "must-not-inherit")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-inherit")
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "must-not-inherit")
    child = environment(profile)
    assert set(child) == {"MODEL_SCORE_DATABASE_DSN", "WORKBENCH_ENV_FILE", "LANG", "LC_ALL", "TZ"}
    assert child["WORKBENCH_ENV_FILE"] == "/dev/null"
    assert all("must-not-inherit" != value for value in child.values())


@pytest.mark.parametrize("case", ["mode", "symlink", "api", "bucket", "duplicate", "empty"])
def test_profile_rejects_nonisolated_inputs(tmp_path, case):
    path = tmp_path / "score.env"
    path.write_text("MODEL_SCORE_DATABASE_DSN='postgresql://synthetic-only'\n")
    path.chmod(0o600)
    if case == "mode":
        path.chmod(0o644)
    elif case == "symlink":
        link = tmp_path / "link.env"
        link.symlink_to(path)
        path = link
    elif case in {"api", "bucket"}:
        path.write_text(
            path.read_text()
            + ("API_DATABASE_DSN=x\n" if case == "api" else "OBJECT_STORE_SECRET_KEY=x\n")
        )
    elif case == "duplicate":
        path.write_text(path.read_text() * 2)
    else:
        path.write_text("")
    with pytest.raises(GovernanceError):
        environment(path)


def test_general_process_rejected_before_connect(monkeypatch):
    import psycopg

    def forbidden(*args, **kwargs):
        raise AssertionError("General environment must not connect as score purpose")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setenv("API_DATABASE_DSN", "explicit-general-marker")
    with pytest.raises(DomainError) as failure, score_connection():
        pass
    assert failure.value.code == "SCORE_ENVIRONMENT_NOT_ISOLATED"


def test_default_runtime_cannot_construct_paid_client_or_claim_job(monkeypatch):
    from packages.model_governance import scoring_runtime

    def forbidden(*args, **kwargs):
        raise AssertionError("No transport must reject before any database claim")

    monkeypatch.setattr(scoring_runtime.ScoreAuthority, "claim", forbidden)
    with pytest.raises(DomainError) as failure:
        execute_job(uuid4(), 1)
    assert failure.value.code == "SCORE_PAID_EXECUTION_DISABLED"


@pytest.mark.parametrize("current", [["frozen", "added"], [], ["frozen", "frozen"]])
def test_append_or_drop_cannot_expand_an_old_review_root(current):
    from packages.model_governance.scoring_repository import require_frozen_scoring_scope

    with pytest.raises(DomainError, match="实际冻结范围"):
        require_frozen_scoring_scope(
            {"rules": [{"kind": "scoring", "id": "frozen"}]},
            {"candidates": [{"rule_id": item} for item in current]},
        )


def test_frozen_scoring_scope_compares_exact_set_independent_of_order():
    from packages.model_governance.scoring_repository import require_frozen_scoring_scope

    require_frozen_scoring_scope(
        {"rules": [{"kind": "scoring", "id": "b"}, {"kind": "scoring", "id": "a"}]},
        {"candidates": [{"rule_id": "a"}, {"rule_id": "b"}]},
    )


def test_review_root_retains_actual_utf8_bytes_instead_of_reserializing(monkeypatch):
    from packages.domain.runtime import sha
    from packages.model_governance import scoring_repository as repo

    raw = b'{"identity": {"confirmed": true}, "confirmed_facts": [], "documents": [], "rules": [], "rule_set": {"coverage": "complete"}}\n'
    monkeypatch.setattr(repo.storage, "get", lambda _: raw)
    root, content = repo.read_root(
        {"object_key": "synthetic", "byte_size": len(raw), "digest": sha(raw)}
    )
    assert content.encode() == raw
    assert root["identity"] == {"confirmed": True}


@pytest.mark.parametrize("coverage", ["partial", "conflict", None])
def test_incomplete_rule_extraction_cannot_claim_complete_judgment_scope(monkeypatch, coverage):
    import json

    from packages.domain.runtime import sha
    from packages.model_governance import scoring_repository as repo

    raw = json.dumps(
        {
            "identity": {"confirmed": True},
            "confirmed_facts": [],
            "documents": [],
            "rules": [],
            "rule_set": {"coverage": coverage},
        }
    ).encode()
    monkeypatch.setattr(repo.storage, "get", lambda _: raw)
    with pytest.raises(DomainError) as failure:
        repo.read_root({"object_key": "synthetic", "byte_size": len(raw), "digest": sha(raw)})
    assert failure.value.code == "SCORE_SCOPE_UNSUPPORTED"
