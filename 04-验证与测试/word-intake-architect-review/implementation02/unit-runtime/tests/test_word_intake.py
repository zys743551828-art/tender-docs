"""Bounded external OOXML and qualification checks; no provider calls."""

import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from packages.domain.runtime import DomainError, sha
from packages.word_intake import inspection, renderer
from packages.word_intake.inspection import DOCX, PDF, inspect_upload
from packages.word_intake.repository import REQUIRED_ISOLATION, controlled_qualified


def make_docx(
    text="施工工期120天，质量合格。",
    *,
    font="Noto Sans CJK SC",
    extra=None,
    relationships="",
    body=None,
):
    result = io.BytesIO()
    entries = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        + relationships
        + "</Relationships>",
        "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + (
            body
            or '<w:p><w:r><w:rPr><w:rFonts w:ascii="'
            + escape(font, {'"': "&quot;"})
            + '" w:eastAsia="'
            + escape(font, {'"': "&quot;"})
            + '"/></w:rPr><w:t>'
            + escape(text)
            + "</w:t></w:r></w:p>"
        )
        + "</w:body></w:document>",
    }
    entries.update(extra or {})
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return result.getvalue()


def checked(data):
    return inspect_upload("修改稿.docx", data, sha(data), len(data))


def test_real_docx_inspection_keeps_original_identity_without_execution():
    data = make_docx()
    value = checked(data)
    assert value["media_type"] == DOCX
    assert value["digest"] == sha(data)
    assert value["inspection"]["paragraphs"] == ["施工工期120天，质量合格。"]
    assert value["inspection"]["conversion_eligible"]
    assert "paragraphs" not in inspection.public_report(value["inspection"])


@pytest.mark.parametrize(
    "filename,data,code",
    [
        ("x.pdf", b"PK\x03\x04bad", "INVALID_PDF"),
        ("x.docx", b"%PDF-1.7", "WORD_ARCHIVE_INVALID"),
        ("x.doc", b"x", "DOCUMENT_FORMAT_UNSUPPORTED"),
    ],
)
def test_single_or_multipart_use_same_byte_type_rules(filename, data, code):
    with pytest.raises(DomainError) as caught:
        inspect_upload(filename, data, sha(data), len(data))
    assert caught.value.code == code


def test_digest_size_and_filename_are_not_client_assertions():
    data = make_docx()
    with pytest.raises(DomainError) as caught:
        inspect_upload("x.docx", data, "0" * 64, len(data))
    assert caught.value.code == "UPLOAD_DIGEST_MISMATCH"
    with pytest.raises(DomainError):
        inspect_upload("../x.docx", data, sha(data), len(data))
    with pytest.raises(DomainError):
        inspect_upload("x.docx", data, sha(data), len(data) + 1)


def test_external_macro_embedded_content_is_retained_but_never_rendered(monkeypatch):
    external = '<Relationship Id="external" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" TargetMode="External" Target="http://127.0.0.1/private"/>'
    data = make_docx(
        relationships=external,
        extra={"word/vbaProject.bin": b"macro", "word/embeddings/object.bin": b"object"},
    )
    value = checked(data)
    assert not value["inspection"]["conversion_eligible"]
    assert set(value["inspection"]["active_content_gaps"]) == {
        "EXTERNAL_RELATIONSHIP",
        "ACTIVE_OR_EMBEDDED_CONTENT",
    }
    monkeypatch.setattr(
        renderer, "build_profile", lambda: pytest.fail("unsafe Word must not enter Docker")
    )
    result = renderer.render(data, {"identity": {"enabled": False}, "profile_digest": "0" * 64})
    assert result["error_code"] == "WORD_PACKAGE_ACTIVE_CONTENT_BLOCKED"
    assert result["conversion_attempted"] is False


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"../escape.xml": "<x/>"}, "WORD_ARCHIVE_PATH_INVALID"),
        (
            {"word/evil.xml": '<!DOCTYPE r [<!ENTITY x "boom">]><r>&x;</r>'},
            "WORD_XML_DECLARATION_BLOCKED",
        ),
        ({"word/bomb.bin": b"A" * 1000000}, "WORD_ARCHIVE_EXPANSION_LIMIT"),
    ],
)
def test_archive_traversal_entity_and_expansion_rejected(extra, code):
    with pytest.raises(DomainError) as caught:
        checked(make_docx(extra=extra))
    assert caught.value.code == code


def test_global_xml_node_and_byte_budgets(monkeypatch):
    monkeypatch.setattr(inspection, "MAX_PACKAGE_NODES", 20)
    with pytest.raises(DomainError) as caught:
        checked(
            make_docx(
                extra={
                    "word/extra1.xml": "<r>" + "<n/>" * 8 + "</r>",
                    "word/extra2.xml": "<r>" + "<n/>" * 8 + "</r>",
                }
            )
        )
    assert caught.value.code == "WORD_PACKAGE_XML_COMPLEXITY_LIMIT"
    monkeypatch.setattr(inspection, "MAX_PACKAGE_NODES", 300000)
    monkeypatch.setattr(inspection, "MAX_PACKAGE_XML_BYTES", 100)
    with pytest.raises(DomainError) as caught:
        checked(make_docx())
    assert caught.value.code == "WORD_PACKAGE_XML_LIMIT"


def test_unavailable_renderer_does_not_claim_attempt_or_fake_pdf():
    result = renderer.render(
        make_docx(), {"identity": {"enabled": False}, "profile_digest": "0" * 64}
    )
    assert result["error_code"] == "WORD_RENDER_SANDBOX_UNAVAILABLE"
    assert result["conversion_attempted"] is False
    assert result["pdf"] is None
    assert not controlled_qualified(result)


def test_partial_or_missing_isolation_cannot_issue_controlled_relation():
    result = {
        "state": "succeeded",
        "coverage": "complete",
        "conversion_attempted": True,
        "gaps": [],
        "isolation": dict.fromkeys(REQUIRED_ISOLATION, True),
        "chain": {
            key: "0" * 64
            for key in (
                "source_docx_digest",
                "pdf_digest",
                "profile_digest",
                "renderer_digest",
                "fontset_digest",
                "options_digest",
            )
        },
    }
    assert controlled_qualified(result)
    result["coverage"] = "partial"
    assert not controlled_qualified(result)
    result["coverage"] = "complete"
    result["isolation"]["network_none"] = False
    assert not controlled_qualified(result)
    result["isolation"]["network_none"] = True
    result["chain"].pop("fontset_digest")
    assert not controlled_qualified(result)


def test_pdf_byte_identity_still_supported():
    data = b"%PDF-1.7\nsynthetic-header-only"
    assert inspect_upload("normal.pdf", data, sha(data), len(data))["media_type"] == PDF


def test_sandbox_argv_has_no_user_command_or_host_credentials(tmp_path):
    profile = {"identity": {"image_environment_names": ["POSTGRES_PASSWORD", "PATH"]}}
    argv = renderer._arguments("twb-word-owned", tmp_path / "source.docx", profile)
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "/runner/run.py" in argv
    assert "--pull" in argv and "never" in argv
    assert "POSTGRES_PASSWORD" in argv and not any("POSTGRES_PASSWORD=" in item for item in argv)
    assert not any("docker.sock" in item or "--privileged" in item for item in argv)
    assert "WORD_RENDER_PROFILE_PATH" not in renderer._host_environment(tmp_path)


@pytest.mark.parametrize("name", ["word/./alias.xml", "word//alias.xml"])
def test_archive_noncanonical_aliases_are_rejected(name):
    with pytest.raises(DomainError) as caught:
        checked(make_docx(extra={name: "<x/>"}))
    assert caught.value.code == "WORD_ARCHIVE_PATH_INVALID"


def test_central_directory_count_is_bounded_before_zipinfo_allocation(monkeypatch):
    data = bytearray(make_docx())
    end = data.rfind(b"PK\x05\x06")
    data[end + 10 : end + 12] = (65535).to_bytes(2, "little")
    monkeypatch.setattr(
        inspection.zipfile,
        "ZipFile",
        lambda *args, **kwargs: pytest.fail("preflight must reject before ZipInfo allocation"),
    )
    with pytest.raises(DomainError):
        checked(bytes(data))


def test_actual_word_openapi_shapes_only_registered_routes():
    import json
    from copy import deepcopy
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.openapi.utils import get_openapi

    from apps.api.word_intake_routes import router
    from packages.word_intake.contracts import extend_openapi

    app = FastAPI()
    app.include_router(router)
    actual = get_openapi(title="Word", version="1", routes=app.routes)
    target = json.loads(
        (
            Path(__file__).resolve().parents[1] / "packages/contracts/openapi/openapi.json"
        ).read_text()
    )
    actual["components"] = deepcopy(target["components"])
    extend_openapi(actual)
    retry = actual["paths"]["/api/v1/bids/{bid_id}/word-originals/{version_id}/retry-conversion"][
        "post"
    ]
    assert (
        retry["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/Accepted"
    )
    assert {item["name"] for item in retry["parameters"]} >= {"Idempotency-Key", "If-Match"}


def repack_docx(source, update):
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    update(entries)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return result.getvalue()


def header_docx(part, *, declaration="override", content=None, media=None):
    content = (
        content
        or b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:instrText>DDEAUTO synthetic-no-execution</w:instrText></w:r></w:p></w:hdr>'
    )
    media = media or "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"

    def update(entries):
        if declaration == "override":
            addition = f'<Override PartName="/word/{part}" ContentType="{media}"/>'
        else:
            addition = f'<Default Extension="{part.rsplit(".", 1)[-1]}" ContentType="{media}"/>'
        entries["[Content_Types].xml"] = entries["[Content_Types].xml"].replace(
            b"</Types>", addition.encode() + b"</Types>"
        )
        entries["word/_rels/document.xml.rels"] = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="hdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="'
            + part
            + '"/></Relationships>'
        ).encode()
        entries["word/" + part] = content

    return repack_docx(make_docx(), update)


@pytest.mark.parametrize(
    "part,declaration",
    [
        ("header1.xml", "override"),
        ("header1.XML", "override"),
        ("header1.dat", "override"),
        ("header1.dat", "default"),
    ],
)
def test_declared_xml_parts_are_scanned_independent_of_extension(part, declaration):
    report = checked(header_docx(part, declaration=declaration))["inspection"]
    assert not report["conversion_eligible"]
    assert "EXTERNAL_OR_DYNAMIC_FIELD" in report["active_content_gaps"]
    assert "hdr" in report["unsupported_layout_features"]
    assert report["xml_part_count"] == 5


@pytest.mark.parametrize("kind", ["nodes", "bytes", "dtd"])
def test_arbitrary_named_xml_has_same_package_budgets(monkeypatch, kind):
    payload = (
        b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        + b"<w:p/>" * 20
        + b"</w:hdr>"
    )
    code = "WORD_PACKAGE_XML_COMPLEXITY_LIMIT"
    if kind == "nodes":
        monkeypatch.setattr(inspection, "MAX_PACKAGE_NODES", 25)
    elif kind == "bytes":
        source = header_docx("header.dat", content=payload)
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            budget = sum(i.file_size for i in archive.infolist()) - len(payload) // 2
        monkeypatch.setattr(inspection, "MAX_PACKAGE_XML_BYTES", budget)
        code = "WORD_PACKAGE_XML_LIMIT"
    else:
        payload = b'<!DOCTYPE hdr [<!ENTITY bad "x">]><hdr>&bad;</hdr>'
        code = "WORD_XML_DECLARATION_BLOCKED"
    with pytest.raises(DomainError) as caught:
        checked(header_docx("header.dat", content=payload))
    assert caught.value.code == code


@pytest.mark.parametrize(
    "addition",
    [
        '<Default Extension="XML" ContentType="application/xml"/>',
        '<Override PartName="/word/document.xml" ContentType="application/xml"/>',
        '<Override PartName="/word/missing.xml" ContentType="application/xml"/>',
        '<Default Extension="dat" ContentType="not-a-media-type"/>',
        '<Unexpected ContentType="application/xml"/>',
    ],
)
def test_invalid_or_conflicting_content_type_declarations_rejected(addition):
    source = repack_docx(
        make_docx(),
        lambda e: e.__setitem__(
            "[Content_Types].xml",
            e["[Content_Types].xml"].replace(b"</Types>", addition.encode() + b"</Types>"),
        ),
    )
    with pytest.raises(DomainError) as caught:
        checked(source)
    assert caught.value.code in {"WORD_CONTENT_TYPES_INVALID", "WORD_CONTENT_TYPES_CONFLICT"}


def test_relationship_xml_type_mismatch_never_eligible():
    source = header_docx("header.dat", media="application/octet-stream", content=b"not XML")
    with pytest.raises(DomainError) as caught:
        checked(source)
    assert caught.value.code == "WORD_RELATIONSHIP_XML_TYPE_MISMATCH"


@pytest.mark.parametrize("part", ["word/custom.dat", "word/_rels/document.xml.RELS"])
def test_noncanonical_relationship_parts_are_conservatively_rejected(part):
    def update(e):
        e["[Content_Types].xml"] = e["[Content_Types].xml"].replace(
            b"</Types>",
            f'<Override PartName="/{part}" ContentType="{inspection.REL_TYPE}"/></Types>'.encode(),
        )
        e[part] = (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )

    with pytest.raises(DomainError) as caught:
        checked(repack_docx(make_docx(), update))
    assert caught.value.code == "WORD_RELATIONSHIP_PART_NAME_UNSUPPORTED"
