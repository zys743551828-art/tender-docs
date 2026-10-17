"""RICH pure mechanisms only, not a DOCX/PDF product or API acceptance claim."""

import copy
import io
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from PIL import Image, PngImagePlugin

from packages.authoring import rich_content, rich_images
from packages.authoring.build import W, element
from packages.authoring.content import empty_content
from packages.authoring.rich_ooxml import write_table
from packages.domain.runtime import DomainError


def paragraph(text="单元格12"):
    return {
        "type": "paragraph",
        "attrs": {"id": uuid4().hex},
        "content": [{"type": "text", "text": text}],
    }


def cell(colspan=1, rowspan=1):
    return {
        "type": "tableCell",
        "attrs": {"id": uuid4().hex, "colspan": colspan, "rowspan": rowspan, "header": False},
        "content": [paragraph()],
    }


def row(*cells):
    return {"type": "tableRow", "attrs": {"id": uuid4().hex}, "content": list(cells)}


def table(*rows):
    return {
        "type": "table",
        "attrs": {
            "id": uuid4().hex,
            "column_widths_twips": [1800, 1800, 1800],
            "caption": "实际表格",
        },
        "content": list(rows),
    }


def document(block):
    doc = empty_content("章节", 0)
    doc["content"] = [block]
    return doc


def test_rectangle_spans_keep_one_owner_and_explicit_empty_continuation():
    value = table(row(cell(2, 2), cell()), row(cell()), row(cell(3)))
    rich_content.validate_content(document(value), available_width_twips=6000)
    matrix = rich_content.table_grid(value)
    assert matrix["grid"][0][0] is matrix["grid"][1][1]
    assert len(matrix["origins"]) == 4
    full = table(row(cell(3, 2)), row())
    rich_content.validate_content(document(full))
    assert len(rich_content.table_grid(full)["origins"]) == 1


@pytest.mark.parametrize(
    "value",
    [
        table(row(cell(2), cell(2))),  # beyond edge
        table(row(cell(2))),  # gap
        table(row(cell(1, 2), cell(2)), row(cell(3))),  # overlaps old rowspan
        table(row(cell(3, 2))),  # dangling below
        table(row()),
    ],
)
def test_invalid_span_or_gap_rejected(value):
    with pytest.raises(DomainError):
        rich_content.validate_content(document(value))


def test_repeated_ids_across_table_and_text_are_rejected():
    value = table(row(cell(3)))
    value["attrs"]["id"] = value["content"][0]["content"][0]["content"][0]["attrs"]["id"]
    with pytest.raises(DomainError):
        rich_content.validate_content(document(value))


def test_real_table_xml_has_grid_horizontal_and_vertical_merge():
    value = table(row(cell(2, 2), cell()), row(cell()))
    calls = []

    def blocks(parent, nodes):
        calls.extend(nodes)
        for node in nodes:
            element(element(element(parent, "p"), "r"), "t", node["content"][0]["text"])

    result = write_table(value, blocks, available_width_twips=6000)
    encoded = ET.tostring(result, encoding="utf-8")
    assert result.tag == "{" + W + "}tbl" and len(calls) == 3
    assert len(result.findall("{" + W + "}tr")) == 2
    assert len(result.findall(".//{" + W + "}gridSpan")) == 2
    assert [v.get("{" + W + "}val") for v in result.findall(".//{" + W + "}vMerge")] == [
        "restart",
        "continue",
    ]
    assert "单元格12" in encoded.decode() and len(result.findall(".//{" + W + "}gridCol")) == 3


def test_table_and_image_overflow_are_actionable_not_silent_scaling():
    with pytest.raises(DomainError) as e:
        rich_content.validate_content(document(table(row(cell(3)))), available_width_twips=5000)
    assert e.value.code == "AUTHORING_TABLE_TOO_WIDE"
    image = {
        "type": "image",
        "attrs": {
            "id": uuid4().hex,
            "asset_id": str(uuid4()),
            "asset_revision": 1,
            "asset_version_id": str(uuid4()),
            "width_twips": 7000,
            "height_twips": 5000,
            "alt": "图示",
            "caption": "",
        },
    }
    with pytest.raises(DomainError) as e:
        rich_content.validate_content(document(image), available_width_twips=6000)
    assert e.value.code == "AUTHORING_IMAGE_TOO_WIDE"
    image["attrs"]["width_twips"] = 5000
    rich_content.validate_content(document(image), available_width_twips=6000)
    image["attrs"]["url"] = "https://example.invalid/image.png"
    with pytest.raises(DomainError):
        rich_content.validate_content(document(image))


def raster(fmt="PNG", *, metadata=False):
    output = io.BytesIO()
    with Image.new("RGB", (12, 9), (120, 200, 20)) as value:
        info = PngImagePlugin.PngInfo()
        info.add_text("untrusted", "arbitrary data")
        value.save(output, format=fmt, **({"pnginfo": info} if fmt == "PNG" and metadata else {}))
    return output.getvalue()


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_actual_codec_normalizes_and_hashes_without_metadata(fmt, mime):
    source = raster(fmt, metadata=True)
    first = rich_images.normalize(source, mime)
    assert first == rich_images.normalize(source, mime)
    assert first["proof"]["width_px"] == 12
    assert first["proof"]["height_px"] == 9
    assert rich_images.verify_normalized(first["bytes"], first["proof"])
    with Image.open(io.BytesIO(first["bytes"])) as image:
        assert image.format == "PNG" and image.info == {}
    with pytest.raises(DomainError):
        rich_images.verify_normalized(first["bytes"] + b"x", first["proof"])


@pytest.mark.parametrize(
    "data,mime",
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/png"),
        (b"", "image/png"),
        (raster(), "image/jpeg"),
        (raster(), "image/svg+xml"),
    ],
)
def test_non_raster_empty_and_mime_mismatch_rejected(data, mime):
    with pytest.raises(DomainError):
        rich_images.normalize(data, mime)


def test_animated_png_is_rejected():
    output = io.BytesIO()
    first, second = Image.new("RGB", (3, 2), "red"), Image.new("RGB", (3, 2), "blue")
    first.save(output, format="PNG", save_all=True, append_images=[second], duration=100, loop=0)
    first.close()
    second.close()
    with pytest.raises(DomainError):
        rich_images.normalize(output.getvalue(), "image/png")


def test_proof_cannot_change_decoder_identity_or_size():
    result = rich_images.normalize(raster(), "image/png")
    for key, value in [
        ("pillow_version", "unknown"),
        ("width_px", 13),
        ("normalizer_digest", "0" * 64),
    ]:
        proof = copy.deepcopy(result["proof"])
        proof[key] = value
        with pytest.raises(DomainError):
            rich_images.verify_normalized(result["bytes"], proof)


def test_actual_rich_docx_contains_native_merge_and_exact_embedded_png():
    import zipfile

    import test_authoring_generated as generated_cases

    from packages.authoring import generated_verification, rich_docx, rich_verification
    from packages.domain.runtime import sha

    snapshot = generated_cases.snapshot.__wrapped__()
    value = table(row(cell(2, 2), cell()), row(cell()))
    asset, version = str(uuid4()), str(uuid4())
    image = {
        "type": "image",
        "attrs": {
            "id": uuid4().hex,
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "width_twips": 2400,
            "height_twips": 1800,
            "alt": "规范本地图片",
            "caption": "真实图片12",
        },
    }
    snapshot["sections"][0]["content"] = [value, image, paragraph("表图后的完整正文120")]
    normalized = rich_images.normalize(raster(metadata=True), "image/png")
    materials = [
        {
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "normalization_proof": normalized["proof"],
            "bytes": normalized["bytes"],
        }
    ]
    output = rich_docx.assemble(snapshot, materials)
    assert output == rich_docx.assemble(copy.deepcopy(snapshot), materials)
    proof = rich_verification.verify(
        output,
        snapshot,
        materials,
        rich_verification.manifest(output, snapshot, materials),
        sha(rich_docx.identity()),
    )
    assert proof["native_table_count"] == 1 and proof["image_drawing_count"] == 1
    with zipfile.ZipFile(io.BytesIO(output)) as package:
        parts = package.namelist()
        media = [p for p in parts if p.startswith("word/media/")]
        assert len(media) == 1 and package.read(media[0]) == normalized["bytes"]
        body = ET.fromstring(package.read("word/document.xml"))
        assert len(body.findall(".//{" + W + "}tbl")) == 1
        assert len(body.findall(".//{" + W + "}vMerge")) == 2
        assert len(body.findall(".//{" + W + "}drawing")) == 1
        texts = "".join(t.text or "" for t in body.iter("{" + W + "}t"))
        assert (
            "表图后的完整正文120" in texts
            and "真实图片12" in texts
            and "AUTHORING_RICH_" not in texts
        )
    # Existing PAGE-only admission must NOT silently expand to this producer.
    with pytest.raises(DomainError):
        generated_verification.verify_container(output, page_number=True)
    with pytest.raises(DomainError):
        rich_docx.assemble(snapshot, [])


@pytest.mark.parametrize(
    "attack",
    [
        "unknown_part",
        "external_image",
        "image_bytes",
        "body_field",
        "split_field",
        "unknown_drawing_attr",
        "wrong_image_type",
        "missing_drawing",
        "active_media",
        "processing_instruction",
    ],
)
def test_rich_container_rejects_active_or_unbound_package_parts(attack):
    import zipfile

    import test_authoring_generated as generated_cases

    from packages.authoring import rich_docx, rich_verification
    from packages.domain.runtime import sha

    snapshot = generated_cases.snapshot.__wrapped__()
    asset, version = str(uuid4()), str(uuid4())
    image = {
        "type": "image",
        "attrs": {
            "id": uuid4().hex,
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "width_twips": 2400,
            "height_twips": 1800,
            "alt": "明确合成图片",
            "caption": "",
        },
    }
    snapshot["sections"][0]["content"] = [table(row(cell(3))), image]
    normalized = rich_images.normalize(raster(), "image/png")
    materials = [
        {
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "normalization_proof": normalized["proof"],
            "bytes": normalized["bytes"],
        }
    ]
    source = rich_docx.assemble(snapshot, materials)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if attack == "unknown_part":
        files["word/unknown.xml"] = b'<?xml version="1.0" encoding="utf-8"?><unknown/>'
    elif attack == "external_image":
        root = ET.fromstring(files["word/_rels/document.xml.rels"])
        entry = next(e for e in root if e.get("Type", "").endswith("/image"))
        entry.set("Target", "https://example.invalid/remote.png")
        entry.set("TargetMode", "External")
        files["word/_rels/document.xml.rels"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
    elif attack == "image_bytes":
        name = next(name for name in files if name.startswith("word/media/"))
        files[name] += b"not-the-frozen-raster"
    elif attack in {"body_field", "split_field", "unknown_drawing_attr", "missing_drawing"}:
        root = ET.fromstring(files["word/document.xml"])
        if attack == "body_field":
            field = element(root.find(".//{" + W + "}tc"), "fldSimple", instr=" PAGE ")
            element(element(field, "r"), "t", "1")
        elif attack == "split_field":
            run = element(root.find(".//{" + W + "}tc"), "r")
            element(run, "instrText", "DD")
            element(run, "instrText", "EAUTO")
        elif attack == "unknown_drawing_attr":
            root.find(".//{" + rich_docx.WP + "}docPr").set("onload", "untrusted")
        else:
            for parent in root.iter():
                for child in list(parent):
                    if child.tag == "{" + W + "}drawing":
                        parent.remove(child)
        files["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    elif attack == "wrong_image_type":
        files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
            b"image/png", b"image/svg+xml"
        )
    elif attack == "active_media":
        files["word/media/evil.svg"] = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    elif attack == "processing_instruction":
        files["word/document.xml"] = files["word/document.xml"].replace(
            b"?>", b"?><?untrusted action?>", 1
        )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    mutated = output.getvalue()
    with pytest.raises(DomainError):
        rich_verification.verify_container(mutated, materials, page_number=True)
    # A newly computed label/digest does not prove the bytes came from the source.
    with pytest.raises(DomainError):
        rich_verification.verify(
            mutated,
            snapshot,
            materials,
            rich_verification.manifest(mutated, snapshot, materials),
            sha(rich_docx.identity()),
        )


@pytest.mark.parametrize("covered", [False, True])
def test_actual_synthetic_PDF_image_pixels_geometry_and_visibility(covered):
    import pypdfium2 as pdfium
    import test_authoring_generated as generated_cases

    from packages.authoring import rich_pdf, templates

    snapshot = generated_cases.snapshot.__wrapped__()
    asset, version = str(uuid4()), str(uuid4())
    normalized = rich_images.normalize(raster(), "image/png")
    snapshot["sections"][0]["content"] = [
        {
            "type": "image",
            "attrs": {
                "id": uuid4().hex,
                "asset_id": asset,
                "asset_revision": 1,
                "asset_version_id": version,
                "width_twips": 2400,
                "height_twips": 1800,
                "alt": "真实像素测试",
                "caption": "",
            },
        }
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
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(11906 / 20, 16838 / 20)
    original = Image.open(io.BytesIO(normalized["bytes"]))
    bitmap = pdfium.PdfBitmap.from_pil(original, recopy=True)
    try:
        image = pdfium.PdfImage.new(doc)
        image.set_bitmap(bitmap)
        image.set_matrix(pdfium.PdfMatrix(120, 0, 0, 90, 72, 600))
        page.insert_obj(image)
        if covered:
            cover = pdfium.raw.FPDFPageObj_CreateNewRect(72, 600, 120, 90)
            pdfium.raw.FPDFPageObj_SetFillColor(cover, 255, 255, 255, 255)
            pdfium.raw.FPDFPath_SetDrawMode(cover, pdfium.raw.FPDF_FILLMODE_WINDING, 0)
            pdfium.raw.FPDFPage_InsertObject(page, cover)
        page.gen_content()
        output = io.BytesIO()
        doc.save(output)
    finally:
        bitmap.close()
        original.close()
        page.close()
        doc.close()
    proof = rich_pdf.inspect_images(output.getvalue(), snapshot, materials, templates.DEFAULT_STYLE)
    assert proof["matched_drawings"] == 1
    if covered:
        assert (
            proof["coverage"] == "partial"
            and "AUTHORING_IMAGE_VISIBILITY_UNVERIFIED" in proof["gaps"]
        )
    else:
        assert proof["coverage"] == "complete", proof
