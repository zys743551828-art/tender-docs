"""Reviewed B on PM fresh nonowner DB; local CPU PDF, memory objects, Mock score only."""

import os
import subprocess  # nosec B404 -- fixed CPU helper, no shell
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from apps.api.denominator_routes import router
from apps.api.technical_scale_routes import router as scales
from packages.domain.runtime import connection
from packages.model_governance.denominator_contracts import extend_openapi
from tests import test_historical_input_upload_path as ordinary
from tests import test_scoring_integration as scoring
from tests.test_research_integration import client as research_client  # noqa: F401
from tests.test_scale_integration import body as scale_body
from tests.test_scale_integration import chinese_pdf

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DENOMINATOR_INTEGRATION") != "1",
    reason="Requires new reviewed B database; no patch/DDL/Docker",
)


@pytest.fixture
def project(research_client, monkeypatch, request):  # noqa: F811
    original = ordinary.pdf_bytes
    maximum = getattr(request, "param", "60")

    def pdf(text):
        if maximum != "30":
            return chinese_pdf(text)
        return subprocess.run(  # nosec B603 -- fixed trusted helper/argv, strict environment
            [
                str(scoring.ROOT / ".venv-parsing/bin/python"),
                "-I",
                str(scoring.ROOT / "tests/test_denominator_pdf_helper.py"),
            ],
            input="交通组织评分条款满分60分。\f技术总分满分为30分，量尺为未加权原始分。".encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=20,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        ).stdout

    # The frozen generic fixture registers one claim per returned proof row. Select
    # one actual proof block per exact version here, while keeping BOTH real pages
    # and all blocks in the DB/canonical full chapter. No authority is weakened.
    original_clean = ordinary.clean

    def clean(value):
        result = original_clean(value)
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and "known_at" in result[0]
            and "obtained_at" in result[0]
        ):
            return list({row["document_version_id"]: row for row in result}.values())
        return result

    monkeypatch.setattr(ordinary, "clean", clean)
    monkeypatch.setattr(
        ordinary,
        "pdf_bytes",
        lambda text: pdf(f"技术总分满分为{maximum}分，量尺为未加权原始分。")
        if "original tender" in text
        else original(text),
    )
    for value in scoring.project.__wrapped__(research_client, monkeypatch):
        value[0].app.include_router(router)
        value[0].app.include_router(scales)
        yield value


def scale(project, branch="current"):
    web, headers, bid, _, historical, _ = project
    with connection() as c:
        h = c.execute(
            "SELECT submission_id,subject_id,material_snapshot_id FROM historical_backtest.inputs WHERE id=%s",
            (historical,),
        ).fetchone()
    params = (
        {
            "purpose": "historical_backtest",
            "submission_id": str(h["submission_id"]),
            "material_snapshot_id": str(h["material_snapshot_id"]),
        }
        if branch == "historical"
        else {}
    )
    ctx = web.get(f"/api/v1/bids/{bid}/technical-scale-context", params=params)
    assert ctx.status_code == 200, ctx.text
    document = ctx.json()
    for source in document["documents"]:
        source["evidence"].sort(key=lambda e: not e["text_content"].startswith("技术总分满分"))
    value = scale_body(
        {
            "subject_id": str(h["subject_id"]),
            "submission_id": str(h["submission_id"]),
            "material_snapshot_id": str(h["material_snapshot_id"]),
        },
        document,
        branch == "historical",
    )
    # Exact explicit source token, not the model's extracted maximum.
    value["technical_maximum"] = "30" if "为30分" in value["evidence_refs"][0]["quote"] else "60"
    saved = web.post(
        f"/api/v1/bids/{bid}/technical-scales", json=value, headers=headers(ctx.headers["etag"])
    )
    assert saved.status_code == 201, saved.text
    return saved


def bind(project, target):
    web, headers, *_ = project
    ctx = web.get(f"/api/v1/score-inputs/{target}/denominator-context")
    assert ctx.status_code == 200, ctx.text
    saved = web.post(
        f"/api/v1/score-inputs/{target}/denominators",
        json={"reason": "合成有据分母引用"},
        headers=headers(ctx.headers["etag"]),
    )
    assert saved.status_code == 201, saved.text
    validate(web, saved, "ScoreDenominatorView")
    return saved


def validate(web, response, name):
    web.app.openapi_schema = None
    actual = extend_openapi(web.app.openapi())
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + name, "components": actual["components"]}
    ).validate(response.json())


def derive(project, basis, job):
    web, headers, *_ = project
    return web.post(
        f"/api/v1/score-denominators/{basis.json()['id']}/coverages",
        json={"job_id": job, "reason": "0额外模型调用的合成派生"},
        headers=headers(basis.headers["etag"]),
    )


def test_current_review_late_confirmation_derives_without_new_calls_or_rewriting_score_f(project):
    web, _, _, _, _, _ = project
    frozen, job = scoring.create_job(project, "current")
    output = scoring.child("success", job.json()["id"])
    assert output["ok"] and output["mock_calls"] == 3, output
    source = scale(project)
    basis = bind(project, frozen.json()["id"])
    assert basis.json()["current"] and basis.json()["technical_maximum"] == "60", basis.text
    enriched = derive(project, basis, job.json()["id"])
    assert enriched.status_code == 201, enriched.text
    validate(web, enriched, "SourcedScoreCoverageView")
    assert enriched.json()["recorded_document"]["coverage"]["fraction"] == "1"
    assert enriched.json()["recorded_document"]["features"]["status"] == "complete"
    assert enriched.json()["formal_eligibility"] == "unavailable"
    with connection() as c:
        old = c.execute(
            "SELECT i.technical_maximum,i.maximum_verified,r.document FROM score_execution.inputs i JOIN score_execution.jobs j ON j.input_id=i.id JOIN score_execution.results r ON r.job_id=j.id WHERE i.id=%s",
            (frozen.json()["id"],),
        ).fetchone()
        assert old["technical_maximum"] is None and not old["maximum_verified"]
        assert old["document"]["features"]["status"] == "unavailable"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.calls WHERE job_id=%s",
                (job.json()["id"],),
            ).fetchone()["n"]
            == 3
        )
        confirmed = c.execute(
            "SELECT created_at FROM technical_scale.decisions WHERE version_id=%s",
            (source.json()["id"],),
        ).fetchone()["created_at"]
        review = c.execute(
            "SELECT i.as_of FROM score_execution.inputs s JOIN review_runs r ON r.id=s.review_id JOIN review_inputs i ON i.id=r.input_id WHERE s.id=%s",
            (frozen.json()["id"],),
        ).fetchone()["as_of"]
        assert confirmed > review


def test_missing_authority_keeps_null_and_does_not_send(project):
    frozen, job = scoring.create_job(project, "current")
    basis = bind(project, frozen.json()["id"])
    assert basis.json()["technical_maximum"] is None and not basis.json()["current"]
    rejected = derive(project, basis, job.json()["id"])
    assert rejected.status_code == 409, rejected.text
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_execution.calls WHERE job_id=%s",
                (job.json()["id"],),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("project", ["30"], indirect=True)
def test_smaller_sourced_total_conflicts_without_rescaling_extracted_rule_points(project):
    frozen, _ = scoring.create_job(project, "current")
    scale(project)
    basis = bind(project, frozen.json()["id"])
    assert (
        basis.json()["recorded_status"] == "conflict" and basis.json()["technical_maximum"] is None
    ), basis.text


def test_historical_closed_project_and_withdrawal_preserve_recorded_snapshot(project):
    web, headers, bid, *_ = project
    source = scale(project, "historical")
    with connection() as c:
        c.execute(
            "UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",
            (bid,),
        )
    frozen, job = scoring.create_job(project, "historical")
    output = scoring.child("success", job.json()["id"])
    assert output["ok"] and output["mock_calls"] == 3, output
    basis = bind(project, frozen.json()["id"])
    saved = derive(project, basis, job.json()["id"])
    assert saved.status_code == 201, saved.text
    removed = web.post(
        f"/api/v1/technical-scales/{source.json()['id']}/withdrawals",
        json={"reason": "合成撤回来源权威"},
        headers=headers(source.headers["etag"]),
    )
    assert removed.status_code == 200, removed.text
    archive = web.get(f"/api/v1/score-coverages/{saved.json()['id']}")
    assert archive.status_code == 200 and archive.json()["technical_maximum"] is None
    assert archive.json()["recorded_document"]["technical_maximum"] == "60"
    assert not archive.json()["current"]
    with connection() as c:
        assert (
            c.execute("SELECT phase FROM bid_profiles WHERE bid_id=%s", (bid,)).fetchone()["phase"]
            == "closed"
        )


def test_authority_withdrawal_during_compute_prevents_new_derived_effect(project, monkeypatch):
    from packages.model_governance import denominator_repository as repo

    web, headers, *_ = project
    frozen, job = scoring.create_job(project, "current")
    assert scoring.child("success", job.json()["id"])["ok"]
    source = scale(project)
    basis = bind(project, frozen.json()["id"])
    compute = repo.compute

    def changed(value):
        removed = web.post(
            f"/api/v1/technical-scales/{source.json()['id']}/withdrawals",
            json={"reason": "派生期间独立事务撤回"},
            headers=headers(source.headers["etag"]),
        )
        assert removed.status_code == 200, removed.text
        return compute(value)

    monkeypatch.setattr(repo, "compute", changed)
    failed = derive(project, basis, job.json()["id"])
    assert failed.status_code in {409, 412}, failed.text
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM score_basis.results WHERE binding_id=%s",
                (UUID(basis.json()["id"]),),
            ).fetchone()["n"]
            == 0
        )
