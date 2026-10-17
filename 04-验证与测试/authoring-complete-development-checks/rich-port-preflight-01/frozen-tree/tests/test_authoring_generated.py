"""Pure generated PAGE lane closure; never execute a converter, model or DB here."""

import copy
import io
import os
import zipfile
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from packages.authoring import generated_docx, generated_verification, templates
from packages.authoring.content import empty_content
from packages.authoring.structure_model import node
from packages.authoring.structure_schema import SCHEMA_DIGEST
from packages.domain.runtime import DomainError, sha
from packages.word_intake.inspection import inspect_docx


@pytest.fixture
def snapshot():
    section = empty_content("明确生成正文", 0)
    section["content"][0]["content"] = [
        {"type": "text", "text": "正文数字1与PAGE、DDEAUTO字样均是普通文字。"}
    ]
    extension = {
        "id": str(uuid4()),
        "schema_digest": SCHEMA_DIGEST,
        "nodes": [node(uuid4())],
        "plans": {},
        "directory_template": {
            "id": str(uuid4()),
            "kind": "outline",
            "config": templates.DEFAULT_OUTLINE,
            "config_digest": sha(templates.DEFAULT_OUTLINE),
            "schema_digest": templates.config_schema("outline"),
        },
        "style_template": {
            "id": str(uuid4()),
            "kind": "layout",
            "config": templates.DEFAULT_STYLE,
            "config_digest": sha(templates.DEFAULT_STYLE),
            "schema_digest": templates.config_schema("layout"),
        },
        "metadata": {},
    }
    extension["digest"] = sha(extension)
    return {
        "snapshot_id": str(uuid4()),
        "snapshot_digest": sha("explicit pure snapshot fixture, not PG authority"),
        "sections": [section],
        "authoring_structure": extension,
    }


def repack(source, mutate):
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    mutate(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_exact_generated_page_and_external_word_stays_blocked(snapshot):
    source = generated_docx.assemble(snapshot)
    assert source == generated_docx.assemble(copy.deepcopy(snapshot))
    manifest = generated_docx.producer_manifest(source, snapshot)
    proof = generated_verification.verify(
        source, snapshot, manifest, sha(generated_docx.generator_identity())
    )
    assert proof["page_number"] is True
    assert "word/footer.xml" in proof["parts"]
    # An external caller cannot obtain relaxed conversion merely by uploading these bytes.
    assert inspect_docx(source)["conversion_eligible"] is False
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        footer = ET.fromstring(archive.read("word/footer.xml"))
        fields = list(footer.iter("{" + generated_docx.W + "}fldSimple"))
        assert len(fields) == 1 and fields[0].get("{" + generated_docx.W + "}instr") == " PAGE "


def test_page_disabled_emits_no_dynamic_field(snapshot):
    config = templates.DEFAULT_STYLE | {"page_number": False}
    extension = snapshot["authoring_structure"]
    extension["style_template"]["config"] = config
    extension["style_template"]["config_digest"] = sha(config)
    extension["digest"] = sha({k: v for k, v in extension.items() if k != "digest"})
    source = generated_docx.assemble(snapshot)
    generated_verification.verify_container(source, page_number=False)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        assert all(b"fldSimple" not in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize(
    "attack",
    [
        "dde",
        "switch",
        "split",
        "hidden_header",
        "body_page",
        "nested",
        "unknown_namespace",
        "dtd",
        "extra_part",
        "external_relationship",
        "wrong_type",
        "pi",
        "utf16",
    ],
)
def test_closed_container_rejects_all_field_and_package_bypasses(snapshot, attack):
    source = generated_docx.assemble(snapshot)

    def mutate(files):
        footer = files["word/footer.xml"]
        if attack == "dde":
            files["word/footer.xml"] = footer.replace(b" PAGE ", b" DDEAUTO malicious ")
        elif attack == "switch":
            files["word/footer.xml"] = footer.replace(b" PAGE ", b" PAGE \\* MERGEFORMAT ")
        elif attack == "split":
            files["word/footer.xml"] = footer.replace(
                b"</w:ftr>",
                b"<w:p><w:r><w:instrText>DD</w:instrText></w:r><w:r><w:instrText>EAUTO</w:instrText></w:r></w:p></w:ftr>",
            )
        elif attack == "hidden_header":
            files["word/header.xml"] = (
                b'<?xml version="1.0"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p></w:hdr>'
            )
        elif attack == "body_page":
            files["word/document.xml"] = files["word/document.xml"].replace(
                b"<w:body>",
                b'<w:body><w:p><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>',
            )
        elif attack == "nested":
            files["word/footer.xml"] = footer.replace(
                b"<w:t>1</w:t>",
                b'<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>',
            )
        elif attack == "unknown_namespace":
            files["word/footer.xml"] = footer.replace(
                b"</w:ftr>", b'<x:instrText xmlns:x="urn:unknown">DDEAUTO</x:instrText></w:ftr>'
            )
        elif attack == "dtd":
            files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
                b"?>", b'?><!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]>', 1
            )
        elif attack == "extra_part":
            files["word/secret.dat"] = b'<?xml version="1.0"?><instrText>DDEAUTO</instrText>'
        elif attack == "external_relationship":
            files["word/_rels/document.xml.rels"] = files["word/_rels/document.xml.rels"].replace(
                b'Target="footer.xml"', b'Target="https://invalid.example/" TargetMode="External"'
            )
        elif attack == "wrong_type":
            files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
                b"wordprocessingml.footer+xml", b"wordprocessingml.document.main+xml"
            )
        elif attack == "pi":
            files["word/footer.xml"] = (
                footer + b'<?xml-stylesheet href="https://invalid.example/"?>'
            )
        elif attack == "utf16":
            files["word/footer.xml"] = footer.decode().replace("utf-8", "utf-16").encode("utf-16")

    malicious = repack(source, mutate)
    assert malicious != source
    with pytest.raises(DomainError) as error:
        generated_verification.verify_container(malicious, page_number=True)
    assert error.value.code == "AUTHORING_GENERATED_PACKAGE_BLOCKED"


def test_recomputed_caller_manifest_cannot_authorize_altered_bytes(snapshot):
    raw = generated_docx.assemble(snapshot)
    altered = repack(
        raw,
        lambda files: files.__setitem__(
            "word/document.xml",
            files["word/document.xml"].replace("明确生成正文".encode(), "篡改后的正文".encode()),
        ),
    )
    forged = generated_docx.producer_manifest(altered, snapshot)
    with pytest.raises(DomainError, match="精确产物"):
        generated_verification.verify(
            altered, snapshot, forged, sha(generated_docx.generator_identity())
        )
    with pytest.raises(DomainError, match="生产器摘要"):
        generated_verification.verify(
            raw, snapshot, generated_docx.producer_manifest(raw, snapshot), sha("wrong-generator")
        )


def test_generated_port_rejects_before_any_sandbox_or_model_call(snapshot, monkeypatch):
    import json
    from pathlib import Path

    from packages.authoring import generated_renderer

    public = Path(
        os.environ.get(
            "AUTHORING_GENERATED_BASE_PROFILE_FIXTURE",
            "/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03/renderer-profile.json",
        )
    )
    base = json.loads(public.read_text())
    profile = generated_renderer.profile_for(base)
    raw = generated_docx.assemble(snapshot)

    def no_docker(*args, **kwargs):
        raise AssertionError("No container/preflight may run before source/grammar rejection")

    monkeypatch.setattr(generated_renderer.sandbox, "build_profile", no_docker)
    monkeypatch.setattr(generated_renderer.sandbox, "_docker", no_docker)
    altered = repack(
        raw,
        lambda files: files.__setitem__(
            "word/footer.xml", files["word/footer.xml"].replace(b" PAGE ", b" DDEAUTO hostile ")
        ),
    )
    result = generated_renderer.render(
        altered, snapshot, generated_docx.producer_manifest(altered, snapshot), profile
    )
    assert result["state"] == "failed" and result["conversion_attempted"] is False
    assert result["error_code"] == "AUTHORING_GENERATED_PACKAGE_BLOCKED"
    malformed = generated_renderer.render(
        raw, snapshot, generated_docx.producer_manifest(raw, snapshot), {}
    )
    assert (
        malformed["state"] == "failed"
        and malformed["error_code"] == "AUTHORING_SANDBOX_PROFILE_INVALID"
    )


def test_generated_opc_has_interoperable_default_package_namespaces(snapshot):
    # Actual Docker namespace counterexample is archived separately; this guards its fix.
    raw = generated_docx.assemble(snapshot)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in ["[Content_Types].xml", "word/_rels/document.xml.rels"]:
            part = archive.read(name)
            assert (
                b"xmlns:ns" not in part
                and b'xmlns="http://schemas.openxmlformats.org/package/2006/' in part
            )


def test_prepare_positive_is_pure_and_preserves_body_digits(snapshot, monkeypatch):
    import json
    from pathlib import Path

    from packages.authoring import generated_renderer

    public = Path(
        os.environ.get(
            "AUTHORING_GENERATED_BASE_PROFILE_FIXTURE",
            "/workspace/tender-docs/04-验证与测试/word-intake-development-checks/candidate-03/renderer-profile.json",
        )
    )
    profile = generated_renderer.profile_for(json.loads(public.read_text()))

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure prepare may not execute Docker")

    monkeypatch.setattr(generated_renderer.sandbox, "_docker", forbidden)
    source = generated_docx.assemble(snapshot)
    prepared = generated_renderer.prepare(
        source, snapshot, generated_docx.producer_manifest(source, snapshot), profile
    )
    expected = generated_renderer.expected_body(prepared["roots"])
    assert "正文数字1" in expected and "DDEAUTO" in expected
    assert prepared["proof"]["page_number"] is True


def test_cache_value_and_duplicate_zip_parts_are_rejected(snapshot):
    source = generated_docx.assemble(snapshot)
    wrong_cache = repack(
        source,
        lambda files: files.__setitem__(
            "word/footer.xml", files["word/footer.xml"].replace(b"<w:t>1</w:t>", b"<w:t>2</w:t>")
        ),
    )
    with pytest.raises(DomainError):
        generated_verification.verify_container(wrong_cache, page_number=True)
    output = io.BytesIO(source)
    with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("word/footer.xml", b"not the original")
    with pytest.raises(DomainError):
        generated_verification.verify_container(output.getvalue(), page_number=True)
