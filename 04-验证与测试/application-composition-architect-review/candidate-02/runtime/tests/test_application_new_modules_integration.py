"""Composition A2/scale only: full ASGI + limited PG + explicit memory objects; no Docker/model."""

import io
import os
from zipfile import ZipFile

import pytest

from apps.worker import engine
from packages.domain import storage
from packages.domain.runtime import connection, fetch, sha
from tests.test_application_composition_integration import headers, lifecycle, validate

pytest_plugins = ["tests.test_application_composition_integration"]


@pytest.mark.skipif(
    os.environ.get("RUN_COMPOSITION_MISSING_EXTENSIONS") != "1",
    reason="Old 17SQL explicit profile only",
)
def test_missing_new_sql_fails_closed_and_does_not_claim_scale_integration(application):
    web = application["client"]
    catalog = web.get("/api/v1/authoring/templates")
    assert catalog.status_code == 503 and catalog.json()["code"] == "AUTHORING_STRUCTURE_NOT_READY"
    scale = web.get("/api/v1/technical-scale-capabilities")
    validate(scale, "/api/v1/technical-scale-capabilities")
    assert not scale.json()["schema_configured"] and not scale.json()["score_input_integration"]
    assert scale.json()["formal_eligibility"] == "unavailable"


@pytest.mark.skipif(
    os.environ.get("RUN_COMPOSITION_NEW_MODULES") != "1",
    reason="New reviewed 19SQL explicit profile only",
)
def test_full_app_tree_plan_dual_template_actual_docx_fenced_publish_and_closed_guard(
    application, monkeypatch
):
    w = application
    web = w["client"]
    bid = w["bid"]
    monkeypatch.delenv("AUTHORING_GENERATED_RENDER_PROFILE_PATH", raising=False)
    monkeypatch.delenv("WORD_RENDER_PROFILE_PATH", raising=False)
    created = web.post(
        f"/api/v1/bids/{bid}/drafts",
        json={"title": "完整应用合成编制", "outline_template_version_id": None},
        headers=headers(w),
    )
    validate(created, "/api/v1/bids/{bid_id}/drafts", "post")
    draft = created.json()["id"]
    path = f"/api/v1/bids/{bid}/drafts/{draft}/structure"
    tree = web.get(path)
    validate(tree, "/api/v1/bids/{bid_id}/drafts/{draft_id}/structure")
    node = tree.json()["nodes"][0]
    plan = {
        "base_draft_revision": tree.json()["draft_revision"],
        "base_plan_revision": 0,
        "state": "drafting",
        "goal": "显式人工计划，不产生AI正文或合规资格",
        "key_points": ["本节人工待完善"],
        "rule_ids": [],
        "source_refs": [],
        "pending_facts": [],
        "planned_pages": 2,
        "budget_basis": "manual",
        "budget_rule_id": None,
        "budget_note": "计划不代表实际物理页",
    }
    planned = web.put(
        path.removesuffix("structure") + f"sections/{node['section_id']}/plan",
        json=plan,
        headers=headers(w, tree.headers["etag"]),
    )
    validate(planned, "/api/v1/bids/{bid_id}/drafts/{draft_id}/sections/{section_id}/plan", "put")
    updated = web.get(path)
    assert updated.json()["page_budget"]["planned_total"] == 2
    assert updated.json()["page_budget"]["actual_total"] is None
    catalog = web.get("/api/v1/authoring/templates")
    validate(catalog, "/api/v1/authoring/templates")
    assert {"layout", "outline"} <= {x["kind"] for x in catalog.json()["items"]}
    body = {
        "draft_id": draft,
        "draft_revision": updated.json()["draft_revision"],
        "directory_template_version_id": updated.json()["directory_template_version_id"],
        "style_template_version_id": updated.json()["style_template_version_id"],
        "formats": ["docx"],
        "follow_up": "none",
    }
    accepted = web.post(f"/api/v1/bids/{bid}/builds", json=body, headers=headers(w))
    assert accepted.status_code == 202
    build = accepted.json()["resource_id"]
    op = accepted.json()["operation"]["id"]
    with connection(True) as c:
        message = fetch(c, "SELECT payload FROM outbox WHERE operation_id=%s", (op,))["payload"]
    lease = engine.acquire(message)
    assert lease is not None
    # The world fixture replaces only storage.get/put with an explicit in-memory object map.
    outcome = engine.run_compute(message, lease)
    assert engine.finish(message, lease, outcome)
    assert engine.acquire(message) is None
    result = web.get(f"/api/v1/builds/{build}")
    validate(result, "/api/v1/builds/{build_id}")
    assert result.json()["follow_up"]["state"] == "not_requested"
    output = result.json()["outputs"][0]
    assert output["format"] == "docx" and output["state"] == "succeeded"
    assert "ACTUAL_SECTION_PAGE_RANGES_UNKNOWN" in output["gaps"]
    with connection() as c:
        artifact = fetch(
            c,
            "SELECT b.object_key,b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s",
            (output["document_version_id"],),
        )
        assert (
            fetch(c, "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s", (op,))["n"] == 0
        )
        frozen = fetch(
            c,
            "SELECT i.manifest FROM builds b JOIN build_inputs i ON i.id=b.input_id WHERE b.id=%s",
            (build,),
        )["manifest"]
        assert frozen["directory_template_version_id"] == body["directory_template_version_id"]
        assert frozen["style_template_version_id"] == body["style_template_version_id"]
    raw = storage.get(artifact["object_key"])
    assert sha(raw) == artifact["digest"]
    with ZipFile(io.BytesIO(raw)) as package:
        assert b"document" in package.read("word/document.xml")
    lifecycle(w, "terminate")
    lifecycle(w, "archive")
    denied = web.put(
        path.removesuffix("structure") + f"sections/{node['section_id']}/plan",
        json=plan
        | {"base_draft_revision": updated.json()["draft_revision"], "base_plan_revision": 1},
        headers=headers(w, updated.headers["etag"]),
    )
    assert denied.status_code == 409 and denied.json()["code"] == "PROJECT_WORK_CLOSED"
    assert web.get(f"/api/v1/builds/{build}").status_code == 200


@pytest.mark.skipif(
    os.environ.get("RUN_COMPOSITION_NEW_MODULES") != "1",
    reason="New reviewed 19SQL explicit profile only",
)
def test_scale_authority_configuration_does_not_change_scoring_prediction_contract(application):
    web = application["client"]
    result = web.get("/api/v1/technical-scale-capabilities")
    validate(result, "/api/v1/technical-scale-capabilities")
    assert result.json()["schema_configured"] is True
    assert result.json()["score_input_integration"] is False
    assert result.json()["executable_aggregation"] is False
    assert (
        result.json()["formal_eligibility"] == "unavailable"
        and not result.json()["paid_execution_enabled"]
    )
    scoring = web.get("/api/v1/scoring-capabilities")
    validate(scoring, "/api/v1/scoring-capabilities")
    assert not scoring.json()["numeric_prediction_available"]
