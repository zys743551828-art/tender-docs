"""Document bytes, stable patch ranges, input whitelist, honest format failures."""

import copy
import io
import os
import zipfile
from xml.etree import ElementTree as ET

import pytest

from packages.authoring.build import assemble_docx, render_pdf
from packages.authoring.content import empty_content, validate_content
from packages.authoring.patches import replace_node
from packages.domain.runtime import DomainError, sha


def content():
    section = empty_content("施工组织方案", 0)
    section["content"][0]["content"] = [
        {"type": "text", "text": "施工周期 120 天。Quality and safety plan."}
    ]
    return section


def test_actual_word_zip_is_deterministic_and_has_only_body():
    section = content()
    section["attrs"]["rule_ids"] = []
    raw = assemble_docx({"sections": [section]})
    assert raw == assemble_docx({"sections": [section]})
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert "word/document.xml" in archive.namelist()
        text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        assert "施工组织方案" in text and "120 天" in text
        assert "source_refs" not in text and "AI指令" not in text
        assert b'TargetMode="External"' not in archive.read("word/_rels/document.xml.rels")


@pytest.mark.parametrize("mutation", ["script", "url", "duplicate", "markup", "depth"])
def test_editor_rejects_executable_or_ambiguous_nodes(mutation):
    section = content()
    if mutation == "script":
        section["content"][0]["type"] = "script"
    elif mutation == "url":
        section["content"][0]["content"][0]["marks"] = [
            {"type": "link", "attrs": {"href": "javascript:x"}}
        ]
    elif mutation == "duplicate":
        section["content"].append(copy.deepcopy(section["content"][0]))
    elif mutation == "markup":
        section["content"][0]["html"] = "<script/>"
    else:
        child = section["content"][0]
        for i in range(20):
            child = {
                "type": "bulletList",
                "attrs": {"id": str(i)},
                "content": [
                    {"type": "listItem", "attrs": {"id": "li" + str(i)}, "content": [child]}
                ],
            }
        section["content"] = [child]
    with pytest.raises(DomainError):
        validate_content(section)


def test_patch_requires_exact_stable_range_and_preserves_original():
    original = content()
    node = original["content"][0]
    replacement = copy.deepcopy(node)
    replacement["content"][0]["text"] = "人工核验的新表述"
    updated = replace_node(original, node["attrs"]["id"], sha(node), replacement)
    assert updated != original and "120" in original["content"][0]["content"][0]["text"]
    with pytest.raises(DomainError) as exc:
        replace_node(updated, node["attrs"]["id"], sha(node), replacement)
    assert exc.value.code == "PATCH_RANGE_CHANGED"


def test_pdf_conversion_failure_does_not_erase_word():
    raw = assemble_docx({"sections": [content()]})
    result = render_pdf(raw, executable="/does-not-exist/soffice", font_path="/none")
    assert result.state == "failed" and result.pdf is None
    assert result.chain["source_docx_digest"] == sha(raw)
    assert "RENDERER_UNAVAILABLE" in result.gaps
    assert zipfile.is_zipfile(io.BytesIO(raw))


@pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_RENDER") != "1", reason="需真实LibreOffice及中文字体"
)
def test_real_libreoffice_pdf_has_expected_physical_text_and_chain():
    import pypdfium2

    raw = assemble_docx({"sections": [content()]})
    result = render_pdf(
        raw,
        executable=os.environ.get("AUTHORING_RENDERER", "soffice"),
        font_path=os.environ.get(
            "AUTHORING_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
    )
    assert result.state == "succeeded", result.gaps
    assert result.chain["source_docx_digest"] == sha(raw)
    assert result.chain["pdf_digest"] == sha(result.pdf)
    assert result.chain["page_count"] >= 1
    assert result.chain["fontset_digest"] and result.chain["renderer_digest"]
    assert "RENDER_SANDBOX_NOT_ATTESTED" in result.gaps
    pdf = pypdfium2.PdfDocument(result.pdf)
    try:
        page = pdf[0]
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        assert "施工组织方案" in text and "120" in text and "Quality" in text
        textpage.close()
        page.close()
    finally:
        pdf.close()


def test_actual_openapi_editor_schema_rejects_wrong_parent_child_and_roundtrips_content():
    import json
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    from packages.authoring.contracts import extend_openapi
    from packages.domain.runtime import ROOT

    target = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    actual = extend_openapi({"components": deepcopy(target["components"]), "paths": {}})
    schemas = actual["components"]["schemas"]
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/AuthoringDocument", "components": {"schemas": schemas}}
    )
    assert not list(validator.iter_errors(content()))
    wrong = content()
    wrong["content"][0]["content"] = [copy.deepcopy(wrong["content"][0])]
    assert list(validator.iter_errors(wrong))


@pytest.mark.parametrize("bad", ["\ud800", "\x00"])
def test_unrepresentable_xml_text_never_becomes_successful_word(bad):
    section = content()
    section["content"][0]["content"][0]["text"] = bad
    with pytest.raises(DomainError):
        assemble_docx({"sections": [section]})


def test_renderer_process_environment_excludes_business_credentials(monkeypatch):
    from packages.authoring.build import renderer_environment

    monkeypatch.setenv("API_DATABASE_DSN", "not-a-real-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-secret")
    assert "API_DATABASE_DSN" not in renderer_environment()
    assert "DEEPSEEK_API_KEY" not in renderer_environment()


def test_truncated_converter_pdf_is_failed_while_actual_word_is_retained(monkeypatch):
    """Synthetic converter output, actual PDFium error boundary; no LO quality claim."""
    from pathlib import Path

    from packages.authoring import build, build_repository

    identity = {
        "renderer_digest": sha("synthetic-converter"),
        "fontset_digest": sha("synthetic-font"),
        "options_digest": build.TEMPLATE_DIGEST,
    }
    monkeypatch.setattr(build, "render_identity", lambda *_: identity)

    class BrokenOutputProcess:
        def __init__(self, *_args, cwd, **_kwargs):
            (Path(cwd) / "body.pdf").write_bytes(b"%PDF-1.7\ntruncated-converter-output\n")
            self.returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def communicate(self, **_kwargs):
            return b"", b""

    monkeypatch.setattr(build.subprocess, "Popen", BrokenOutputProcess)
    stored = {}
    monkeypatch.setattr(
        build_repository.storage, "put", lambda key, data, _media: stored.__setitem__(key, data)
    )
    monkeypatch.setattr(build_repository.storage, "get", lambda key: stored[key])
    snapshot = {"sections": [content()], "snapshot_digest": sha("synthetic-snapshot")}
    manifest = {
        "writer_digest": build.WRITER_DIGEST,
        "formats": ["docx", "pdf"],
        "renderer_identity": identity,
    }
    # Preserve the original actual PDFium truncated-byte regression directly.
    invalid = build.render_pdf(
        assemble_docx(snapshot),
        executable="synthetic",
        font_path="synthetic",
        expected_identity=identity,
        sandbox_attested=False,
    )
    assert invalid.pdf is None and "PDF_INVALID" in invalid.gaps
    # Current runtime refuses old unsandboxed render profiles; DOCX still survives.
    computed = build_repository.compute_prepared_build(
        {
            "input_id": "synthetic-input",
            "info": {"draft_snapshot_id": "synthetic-snapshot", "manifest": manifest},
            "snapshot": snapshot,
        }
    )
    docx = next(x for x in computed["outputs"] if x["format"] == "docx")
    pdf = next(x for x in computed["outputs"] if x["format"] == "pdf")
    assert docx["state"] == "succeeded" and zipfile.is_zipfile(
        io.BytesIO(stored[docx["object_key"]])
    )
    assert pdf["state"] == "failed" and "RENDER_PROTOCOL_UNSUPPORTED" in pdf["gaps"]
    assert not any(key.endswith("pdf") for key in stored)


def test_actual_follow_up_schema_rejects_fake_review_or_adoption_identity():
    import json
    from uuid import uuid4

    from jsonschema import Draft202012Validator, FormatChecker

    from packages.authoring.contracts import extend_openapi
    from packages.domain.runtime import ROOT

    actual = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    extend_openapi(actual)
    validator = Draft202012Validator(
        {"$ref": "#/components/schemas/BuildFollowUpView", "components": actual["components"]},
        format_checker=FormatChecker(),
    )
    value = {
        "requested": False,
        "state": "not_requested",
        "adopted_document_version_id": None,
        "parse_operation_id": None,
        "request_id": None,
        "review_id": None,
        "reasons": [],
    }
    validator.validate(value)
    for changed in [
        {"review_id": str(uuid4())},
        {"state": "adopted"},
        {"requested": True},
        {"unknown": "invented"},
    ]:
        assert list(validator.iter_errors(value | changed))
    adopted = value | {
        "requested": True,
        "state": "adopted",
        "adopted_document_version_id": str(uuid4()),
        "parse_operation_id": str(uuid4()),
        "request_id": str(uuid4()),
    }
    validator.validate(adopted)
    assert list(validator.iter_errors(adopted | {"request_id": None}))
