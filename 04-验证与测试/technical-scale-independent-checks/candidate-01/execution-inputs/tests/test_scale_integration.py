"""Actual fresh nonowner PG/login API; synthetic claims, memory objects, zero paid calls.

The ordinary upload fixture retains actual 2026 timestamps. Historical statements
are explicitly synthetic mechanism inputs and do not prove any real old PDF existed.
"""

import json
import os
import subprocess  # nosec B404 -- fixed isolated local CPU helper
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from apps.api.technical_scale_routes import router
from packages.domain.runtime import clean, connection
from packages.model_governance.scale_contracts import extend_openapi
from tests.test_research_integration import client as research_client  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCALE_INTEGRATION") != "1", reason="Requires separately fresh reviewed C"
)
ROOT = Path(__file__).resolve().parents[1]
DECLARATION = "技术总分满分为60分，量尺为未加权原始分。"


def chinese_pdf(text):
    return subprocess.run(  # nosec B603 -- fixed argv and environment, no shell
        [
            str(ROOT / ".venv-parsing/bin/python"),
            "-I",
            str(ROOT / "tests/test_scale_pdf_helper.py"),
        ],
        input=text.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=20,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    ).stdout


@pytest.fixture
def world(research_client, monkeypatch):  # noqa: F811
    from tests import test_historical_input_upload_path as ordinary

    original = ordinary.pdf_bytes
    monkeypatch.setattr(
        ordinary,
        "pdf_bytes",
        lambda text: chinese_pdf(DECLARATION) if "original tender" in text else original(text),
    )
    ordinary.test_actual_new_upload_then_historical_input_keeps_real_now_and_no_old_review(
        research_client, monkeypatch
    )
    web, actor, headers, objects = research_client
    web.app.include_router(router)
    with connection() as c:
        bid = c.execute(
            "SELECT id FROM bids WHERE created_by=%s ORDER BY created_at DESC LIMIT 1", (actor,)
        ).fetchone()["id"]
        item = clean(
            c.execute(
                "SELECT h.submission_id,h.subject_id,v.material_snapshot_id FROM historical_backtest.subject_heads h JOIN historical_backtest.inputs v ON v.submission_id=h.submission_id WHERE v.bid_id=%s ORDER BY v.created_at DESC LIMIT 1",
                (bid,),
            ).fetchone()
        )
    return {"web": web, "headers": headers, "objects": objects, "bid": str(bid), **item}


def context(w, historical=False):
    params = (
        {
            "purpose": "historical_backtest",
            "submission_id": w["submission_id"],
            "material_snapshot_id": w["material_snapshot_id"],
        }
        if historical
        else {}
    )
    response = w["web"].get(f"/api/v1/bids/{w['bid']}/technical-scale-context", params=params)
    assert response.status_code == 200, response.text
    validate(w, response, "TechnicalScaleContext")
    return response


def body(w, ctx, historical=False):
    document = next(
        (d for d in ctx["documents"] if d["document_version_id"] == w.get("preferred_document")),
        ctx["documents"][0],
    )
    block = document["evidence"][0]
    scope = {"purpose": "current_prebid"}
    if historical:
        docs = {h["document_version_id"] for h in document["source_heads"]}
        scope = {
            "purpose": "historical_backtest",
            "submission_id": w["submission_id"],
            "material_snapshot_id": w["material_snapshot_id"],
            "historical_subject_authority_id": w["subject_id"],
            "claim_ids": [
                c["id"] for c in ctx["historical_claims"] if c["document_version_id"] in docs
            ],
        }
    return {
        "intent": "confirm",
        "context": scope,
        "technical_maximum": "60.00",
        "scale_description": "未加权原始分",
        "aggregation": {"status": "unknown", "description": "无可执行聚合依据"},
        "evidence_refs": [
            {
                "document_version_id": document["document_version_id"],
                "blob_digest": document["blob_digest"],
                "parse_id": document["parse_id"],
                "physical_page": block["physical_page"],
                "evidence_id": block["id"],
                "quote": block["text_content"],
                "supported_fields": ["technical_maximum", "scale_description"],
            }
        ],
        "reason": "明确合成满分机制，不授真实模型资格",
    }


def register(w, value, tag, headers=None):
    return w["web"].post(
        f"/api/v1/bids/{w['bid']}/technical-scales",
        json=value,
        headers=headers or w["headers"](tag),
    )


def validate(w, response, name):
    actual = extend_openapi(w["web"].app.openapi())
    Draft202012Validator(
        {"$ref": "#/components/schemas/" + name, "components": actual["components"]}
    ).validate(response.json())


def test_seven_api_current_confirm_correction_withdraw_history(world):
    w = world
    capabilities = w["web"].get("/api/v1/technical-scale-capabilities")
    assert capabilities.status_code == 200 and capabilities.json()["schema_configured"]
    validate(w, capabilities, "TechnicalScaleCapabilities")
    ctx = context(w)
    value = body(w, ctx.json())
    headers = w["headers"](ctx.headers["etag"])
    saved = register(w, value, None, headers)
    assert saved.status_code == 201, saved.text
    assert saved.json()["technical_maximum"] == "60.00" and saved.json()["current_applicability"]
    validate(w, saved, "TechnicalScaleView")
    replay = register(w, value, None, headers)
    assert replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true", (
        replay.text
    )
    correction = w["web"].post(
        f"/api/v1/technical-scales/{saved.json()['id']}/corrections",
        json=value | {"base_version_id": saved.json()["id"], "reason": "保留量尺的有据追加版本"},
        headers=w["headers"](saved.headers["etag"]),
    )
    assert correction.status_code == 201, correction.text
    old = w["web"].get(f"/api/v1/technical-scales/{saved.json()['id']}")
    assert not old.json()["is_current_head"] and old.json()["technical_maximum"] is None
    listed = w["web"].get(f"/api/v1/bids/{w['bid']}/technical-scales")
    assert listed.status_code == 200 and len(listed.json()["items"]) == 2
    withdrawn = w["web"].post(
        f"/api/v1/technical-scales/{correction.json()['id']}/withdrawals",
        json={"reason": "显式合成撤回"},
        headers=w["headers"](correction.headers["etag"]),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert (
        withdrawn.json()["technical_maximum"] is None
        and not withdrawn.json()["current_applicability"]
    )


def test_historical_confirm_without_fake_review_and_extra_claim_rejected(world):
    w = world
    ctx = context(w, True)
    value = body(w, ctx.json(), True)
    extra = next(
        c["id"]
        for c in ctx.json()["historical_claims"]
        if c["id"] not in value["context"]["claim_ids"]
    )
    bad = register(
        w,
        value
        | {"context": value["context"] | {"claim_ids": value["context"]["claim_ids"] + [extra]}},
        ctx.headers["etag"],
    )
    assert bad.status_code == 409, bad.text
    saved = register(w, value, ctx.headers["etag"])
    assert saved.status_code == 201 and saved.json()["current_applicability"], saved.text
    assert saved.json()["formal_eligibility"] == "unavailable"
    with connection() as c:
        assert (
            c.execute(
                "SELECT review_id FROM submission_records WHERE id=%s", (w["submission_id"],)
            ).fetchone()["review_id"]
            is None
        )


@pytest.mark.parametrize("change", ["wrong_quote", "cross_parse", "false_maximum"])
def test_invalid_evidence_rejected_before_object_get(world, monkeypatch, change):
    from packages.domain import storage

    w = world
    ctx = context(w)
    value = body(w, ctx.json())
    if change == "wrong_quote":
        value["evidence_refs"][0]["quote"] = "60"
    elif change == "cross_parse":
        value["evidence_refs"][0]["parse_id"] = str(uuid4())
    else:
        value["technical_maximum"] = "80"
    calls = []
    monkeypatch.setattr(storage, "get", lambda key: calls.append(key))
    failed = register(w, value, ctx.headers["etag"])
    assert failed.status_code in {409, 422}, failed.text
    assert calls == []


def test_source_change_during_object_io_rejected_at_final_cas(world, monkeypatch):
    from packages.domain import storage
    from packages.rag.indexing import original_source_id

    w = world
    ctx = context(w)
    value = body(w, ctx.json())
    original_get = storage.get
    changed = []

    def get(key):
        if not changed:
            with connection() as c:
                assert c.execute("SELECT pg_try_advisory_xact_lock(90127,1) AS free").fetchone()[
                    "free"
                ]
                c.execute(
                    "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                    (original_source_id(UUID(value["evidence_refs"][0]["document_version_id"])),),
                )
            changed.append(True)
        return original_get(key)

    monkeypatch.setattr(storage, "get", get)
    failed = register(w, value, ctx.headers["etag"])
    assert failed.status_code in {409, 412}, failed.text
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM technical_scale.versions WHERE bid_id=%s", (w["bid"],)
            ).fetchone()["n"]
            == 0
        )


def test_real_controlled_word_full_ancestor_claims_missing_extra_and_withdrawal(
    world, monkeypatch, tmp_path
):
    """Actual local network-none renderer/CPU parse; historical claims are synthetic.

    Both exact versions have *separate synthetic* proofs. An old Word statement
    never automatically proves the newly rendered PDF was available back then.
    """
    from fastapi.testclient import TestClient

    from apps.api.main import app
    from packages.domain import parsing_profile
    from packages.rag.indexing import original_source_id
    from packages.word_intake.renderer import build_profile
    from tests import test_word_mainflow_integration as word
    from tests.test_historical_input_upload_path import LocalTestParser
    from tests.test_word_intake import make_docx

    w = world
    profile = tmp_path / "explicit-local-render-profile.json"
    profile.write_text(json.dumps(build_profile()))
    monkeypatch.setenv("WORD_INTAKE_ENABLED", "1")
    monkeypatch.setenv("WORD_RENDER_PROFILE_PATH", str(profile))
    parser = LocalTestParser()
    monkeypatch.setattr("apps.api.main.configured_parser", lambda: parser)
    monkeypatch.setattr(parsing_profile, "configured_parser", lambda: parser)
    monkeypatch.setattr(word, "headers", lambda _w, tag=None: w["headers"](tag))
    app.include_router(router)
    with TestClient(app) as web:
        web.cookies.update(w["web"].cookies)
        w = w | {"web": web, "client": web}
        uploaded = word.upload(
            w, make_docx(DECLARATION), filename="technical-total.docx", role="tender"
        )
        _, converted = word.pump(uploaded["verified"]["conversion_operation_id"])
        assert converted["outcome"]["coverage"] == "complete", converted
        committed = word.commit(w, uploaded)
        word.pump(committed["operation"]["id"])
        with connection() as c:
            derived = c.execute(
                "SELECT document_version_id FROM document_relations WHERE source_document_version_id=%s AND kind='controlled_render' AND verification='verified'",
                (uploaded["version"],),
            ).fetchone()["document_version_id"]
            material = c.execute(
                "SELECT current_material_id FROM bid_profiles WHERE bid_id=%s", (w["bid"],)
            ).fetchone()["current_material_id"]
            attachment = clean(
                c.execute(
                    "SELECT d.id AS document_version_id,b.digest AS blob_digest,e.parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN evidence_blocks e ON e.parse_id=p.id WHERE d.id=%s ORDER BY p.created_at DESC LIMIT 1",
                    (derived,),
                ).fetchone()
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM retrieval.chunks WHERE source_id=%s",
                    (original_source_id(UUID(uploaded["version"])),),
                ).fetchone()["n"]
                == 0
            )
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM parse_runs WHERE document_version_id=%s",
                    (uploaded["version"],),
                ).fetchone()["n"]
                == 0
            )
        w |= {"material_snapshot_id": str(material), "preferred_document": str(derived)}
        attachment["explanation"] = "显式合成独立版本证明，仅工程机制，非历史真实性证据"
        claims = []
        for document in (str(derived), uploaded["version"]):
            proof_context = web.get(
                f"/api/v1/historical-proof/submissions/{w['submission_id']}/context"
            )
            created = web.post(
                "/api/v1/historical-proof/source-claims",
                json={
                    "submission_id": w["submission_id"],
                    "document_version_id": document,
                    "historical_subject_authority_id": w["subject_id"],
                    "declared_data_origin": "synthetic",
                    "reason": "显式合成每版本证据，不从Word推断PDF当年存在",
                    "facts": [
                        {
                            "fact_kind": kind,
                            "claimed_at": "2019-01-01T00:00:00Z",
                            "proof_refs": [attachment],
                            "explanation": "独立合成事实",
                        }
                        for kind in ("content_existed", "known_to_bidder", "effective")
                    ],
                },
                headers=w["headers"](proof_context.headers["etag"]),
            )
            assert created.status_code == 201, created.text
            confirmed = web.post(
                f"/api/v1/historical-proof/source-claims/{created.json()['id']}/confirm",
                json={"reason": "显式合成逐版本核验"},
                headers=w["headers"](created.headers["etag"]),
            )
            assert confirmed.status_code == 200, confirmed.text
            claims.append(created.json()["id"])
        ctx = context(w, True)
        value = body(w, ctx.json(), True)
        assert set(value["context"]["claim_ids"]) == set(claims)
        missing = value | {"context": value["context"] | {"claim_ids": claims[:1]}}
        failed = register(w, missing, ctx.headers["etag"])
        assert failed.status_code == 409, failed.text
        extra = next(c["id"] for c in ctx.json()["historical_claims"] if c["id"] not in claims)
        failed = register(
            w,
            value | {"context": value["context"] | {"claim_ids": claims + [extra]}},
            ctx.headers["etag"],
        )
        assert failed.status_code == 409, failed.text
        saved = register(w, value, ctx.headers["etag"])
        assert saved.status_code == 201 and saved.json()["current_applicability"], saved.text
        assert saved.json()["formal_eligibility"] == "unavailable"
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                (original_source_id(UUID(uploaded["version"])),),
            )
        stale = web.get(f"/api/v1/technical-scales/{saved.json()['id']}")
        assert (
            stale.status_code == 200
            and not stale.json()["current_applicability"]
            and stale.json()["technical_maximum"] is None
        )


def test_pending_preserves_unknown_denominator(world):
    w = world
    ctx = context(w)
    value = body(w, ctx.json()) | {
        "intent": "save_pending",
        "technical_maximum": None,
        "scale_description": "",
        "evidence_refs": [],
    }
    saved = register(w, value, ctx.headers["etag"])
    assert saved.status_code == 201, saved.text
    assert saved.json()["technical_maximum"] is None and not saved.json()["current_applicability"]
    validate(w, saved, "TechnicalScaleView")


def test_closed_current_refused_but_separate_historical_registration_allowed(world):
    w = world
    with connection() as c:
        c.execute(
            "UPDATE bid_profiles SET phase='closed',archived_at=clock_timestamp() WHERE bid_id=%s",
            (w["bid"],),
        )
    current = context(w)
    failed = register(w, body(w, current.json()), current.headers["etag"])
    assert failed.status_code == 409, failed.text
    historical = context(w, True)
    saved = register(w, body(w, historical.json(), True), historical.headers["etag"])
    assert saved.status_code == 201 and saved.json()["current_applicability"], saved.text
    with connection() as c:
        profile = c.execute(
            "SELECT phase,archived_at FROM bid_profiles WHERE bid_id=%s", (w["bid"],)
        ).fetchone()
    assert profile["phase"] == "closed" and profile["archived_at"] is not None


def test_replay_after_source_withdrawal_is_not_current_confirmation(world):
    from packages.rag.indexing import original_source_id

    w = world
    ctx = context(w)
    value = body(w, ctx.json())
    headers = w["headers"](ctx.headers["etag"])
    saved = register(w, value, None, headers)
    assert saved.status_code == 201, saved.text
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (original_source_id(UUID(value["evidence_refs"][0]["document_version_id"])),),
        )
    replay = register(w, value, None, headers)
    assert replay.status_code == 409 and replay.json()["code"] == "TECHNICAL_SCALE_STALE", (
        replay.text
    )
    old = w["web"].get(f"/api/v1/technical-scales/{saved.json()['id']}")
    assert old.json()["recorded_maximum"] == "60.00" and old.json()["technical_maximum"] is None


def test_worker_has_no_authority_access_and_api_rr_fails_closed(research_client):  # noqa: F811
    import psycopg

    from packages.domain.runtime import DomainError
    from packages.model_governance import scale_repository

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection(True) as c:
            c.execute("SELECT * FROM technical_scale.versions LIMIT 1")
    with pytest.raises(DomainError) as error:
        with connection() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            scale_repository.lock(c, uuid4())
    assert error.value.code == "CURRENT_AUTHORITY_REQUIRES_READ_COMMITTED"
