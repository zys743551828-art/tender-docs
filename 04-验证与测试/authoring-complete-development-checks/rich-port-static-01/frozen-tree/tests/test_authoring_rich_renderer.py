"""Pure RICH admission and explicit mock-adapter control; never Docker/HTTP/model."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import requests
import test_authoring_generated as generated_cases
import test_authoring_page_navigation as navigation_cases
import test_authoring_rich as rich_cases

from packages.authoring import (
    page_navigation,
    rich_docx,
    rich_images,
    rich_renderer,
    rich_verification,
)
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import sha
from packages.word_intake.inspection import inspect_docx


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure RICH admission test forbids Docker, HTTP, and providers")

    async def forbidden_async(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(rich_renderer.sandbox, "_docker", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async)
    monkeypatch.setattr(DeepSeekClient, "from_env", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


@pytest.fixture
def rich_input():
    snapshot = generated_cases.snapshot.__wrapped__()
    snapshot["sections"][0]["attrs"]["title"] = "Alpha"
    config = snapshot["authoring_structure"]["style_template"]["config"] | {
        "margin_left_twips": 720,
        "margin_right_twips": 720,
    }
    extension = snapshot["authoring_structure"]
    extension["style_template"] = extension["style_template"] | {
        "config": config,
        "config_digest": sha(config),
    }
    extension["digest"] = sha({k: v for k, v in extension.items() if k != "digest"})
    normalized = rich_images.normalize(rich_cases.raster(metadata=True), "image/png")
    asset, version = str(uuid4()), str(uuid4())
    snapshot["sections"][0]["content"] = [
        rich_cases.table(rich_cases.row(rich_cases.cell(3))),
        {
            "type": "image",
            "attrs": {
                "id": uuid4().hex,
                "asset_id": asset,
                "asset_revision": 1,
                "asset_version_id": version,
                "width_twips": 2400,
                "height_twips": 1800,
                "alt": "明确离线合成图",
                "caption": "正文数字12与120不能删除",
            },
        },
    ]
    materials = [
        {
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "normalization_proof": normalized["proof"],
            "bytes": normalized["bytes"],
        }
    ]
    base = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs/authoring-rich-static-checks/word03-base-profile.json"
        ).read_text()
    )
    profile = rich_renderer.profile_for(base)
    pagination = {
        "mode": "chapter_start_new_page",
        "toc_start_pages": {node["section_id"]: None for node in extension["nodes"]},
    }
    source = rich_docx.assemble(snapshot, materials, pagination=pagination)
    return snapshot, materials, profile, pagination, source


def test_rich_admission_is_exact_and_external_word_still_blocks_PAGE(rich_input):
    snapshot, materials, profile, pagination, source = rich_input
    manifest = rich_verification.manifest(source, snapshot, materials, pagination=pagination)
    prepared = rich_renderer.prepare(
        source, snapshot, materials, manifest, profile, pagination=pagination
    )
    assert (
        prepared["proof"]["native_table_count"] == 1
        and prepared["proof"]["image_drawing_count"] == 1
    )
    assert (
        len(list(prepared["roots"]["word/document.xml"].iter("{" + rich_docx.W + "}bookmarkStart")))
        == 1
    )
    assert inspect_docx(source)["conversion_eligible"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_layout_passes", 4),
        ("dynamic_field_policy", "allow_arbitrary_fields"),
        ("max_image_placement_pixels", 32000001),
    ],
)
def test_even_rehashed_profile_policy_changes_fail_before_Docker(rich_input, field, value):
    snapshot, materials, profile, pagination, source = rich_input
    changed = copy.deepcopy(profile)
    changed["identity"][field] = value
    changed["profile_digest"] = sha(changed["identity"])
    result = rich_renderer.render_once(
        source,
        snapshot,
        materials,
        rich_verification.manifest(source, snapshot, materials, pagination=pagination),
        changed,
        pagination=pagination,
    )
    assert result["state"] == "failed" and result["error_code"] == "AUTHORING_RICH_PROFILE_CHANGED"
    assert result["conversion_attempted"] is False


def test_same_labels_do_not_allow_foreign_DOCX_bytes(rich_input):
    snapshot, materials, profile, pagination, source = rich_input
    mutated = generated_cases.repack(
        source,
        lambda files: files.__setitem__("word/extra.xml", b'<?xml version="1.0"?><untrusted/>'),
    )
    manifest = rich_verification.manifest(mutated, snapshot, materials, pagination=pagination)
    result = rich_renderer.render_once(
        mutated, snapshot, materials, manifest, profile, pagination=pagination
    )
    assert result["state"] == "failed" and not result["conversion_attempted"]


def test_unconfigured_render_keeps_real_closed_DOCX_and_explicit_failure(rich_input):
    snapshot, materials, profile, pagination, source = rich_input
    result = rich_renderer.render(snapshot, materials, None, mode=pagination["mode"])
    assert (
        result["state"] == "failed"
        and result["error_code"] == "AUTHORING_RICH_RENDER_NOT_CONFIGURED"
    )
    assert result["docx"] == source and result["render_iterations"] == []


def test_explicit_mock_adapter_loop_uses_actual_PDF_dest_measurements(rich_input, monkeypatch):
    snapshot, materials, profile, pagination, source = rich_input
    calls = []

    def mock_adapter(source, snapshot, materials, manifest, profile, *, pagination):
        calls.append(pagination)
        entries = rich_docx.navigation_entries(snapshot, pagination)
        pdf = navigation_cases.small_pdf(entries, starts=[2], count=2)
        nav = page_navigation.inspect_navigation(
            pdf,
            entries,
            mode=pagination["mode"],
            style=snapshot["authoring_structure"]["style_template"]["config"],
        )
        return {
            "state": "succeeded",
            "coverage": "partial" if nav["gaps"] else "complete",
            "gaps": nav["gaps"],
            "pdf": pdf,
            "correspondence": {"navigation": nav},
        }

    monkeypatch.setattr(rich_renderer, "render_once", mock_adapter)
    result = rich_renderer.render(snapshot, materials, profile, mode=pagination["mode"])
    assert len(calls) == 2 and result["navigation_proof"]["fixed_point"] is True
    assert list(calls[0]["toc_start_pages"].values()) == [None]
    assert list(calls[1]["toc_start_pages"].values()) == [2]
    # This assertion proves loop control/nav only; mock adapter did not render RICH.
    assert len(result["render_iterations"]) == 2


def test_explicit_mock_third_failure_never_labels_old_navigation_as_final(rich_input, monkeypatch):
    snapshot, materials, profile, pagination, source = rich_input
    calls = []

    def mock_adapter(source, snapshot, materials, manifest, profile, *, pagination):
        calls.append(pagination)
        if len(calls) == 3:
            return rich_renderer.failure(source, profile, "AUTHORING_GENERATED_CONVERSION_TIMEOUT")
        entries = rich_docx.navigation_entries(snapshot, pagination)
        pdf = navigation_cases.small_pdf(entries, starts=[len(calls) + 1], count=3)
        nav = page_navigation.inspect_navigation(
            pdf,
            entries,
            mode=pagination["mode"],
            style=snapshot["authoring_structure"]["style_template"]["config"],
        )
        return {
            "state": "succeeded",
            "coverage": "partial",
            "gaps": nav["gaps"],
            "pdf": pdf,
            "correspondence": {"navigation": nav},
        }

    monkeypatch.setattr(rich_renderer, "render_once", mock_adapter)
    result = rich_renderer.render(snapshot, materials, profile, mode=pagination["mode"])
    assert len(calls) == 3 and result["state"] == "failed" and result["pdf"] is None
    assert "navigation_proof" not in result and "navigation_history_proof" in result


@pytest.mark.parametrize(
    "attack", ["external_navigation", "foreign_anchor", "duplicate_bookmark", "missing_end"]
)
def test_rich_navigation_XML_closure_rejects_noncanonical_links(rich_input, attack):
    from xml.etree import ElementTree as ET

    from packages.domain.runtime import DomainError

    snapshot, materials, profile, pagination, source = rich_input

    def mutate(files):
        root = ET.fromstring(files["word/document.xml"])
        bookmark = root.find(".//{" + rich_docx.W + "}bookmarkStart")
        if attack == "external_navigation":
            root.find(".//{" + rich_docx.W + "}hyperlink").set(
                "{" + rich_docx.R + "}id", "externalRelationship"
            )
        elif attack == "foreign_anchor":
            root.find(".//{" + rich_docx.W + "}hyperlink").set(
                "{" + rich_docx.W + "}anchor", "sec_" + uuid4().hex
            )
        else:
            for parent in root.iter():
                if bookmark in list(parent) and attack == "duplicate_bookmark":
                    parent.append(copy.deepcopy(bookmark))
                    break
                for child in list(parent):
                    if child.tag == "{" + rich_docx.W + "}bookmarkEnd" and attack == "missing_end":
                        parent.remove(child)
        files["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    mutated = generated_cases.repack(source, mutate)
    with pytest.raises(DomainError):
        rich_verification.verify_container(mutated, materials, page_number=True)
