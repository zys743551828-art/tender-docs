"""Actual PDFium sparse-foreground regression; no Docker/network/provider/PG."""

import io
from uuid import uuid4

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from packages.authoring import rich_images, rich_pdf, templates


def specimen(size, foreground, *, covered, fractional=False):
    source = Image.new("RGB", size, "white")
    point = (50, 35)
    if foreground == "pixel":
        source.putpixel(point, (0, 0, 0))
    else:
        ImageDraw.Draw(source).text(
            point, "9", fill=(220, 220, 220) if foreground == "grey" else "black"
        )
    raw = io.BytesIO()
    source.save(raw, format="PNG")
    source.close()
    normalized = rich_images.normalize(raw.getvalue(), "image/png")
    asset, version = str(uuid4()), str(uuid4())
    width, height = size
    left, bottom = (72.4, 300.2) if fractional else (72, 300)
    if fractional:
        width, height = width * 1.13, height * 1.07
    snapshot = {
        "sections": [
            {
                "content": [
                    {
                        "type": "image",
                        "attrs": {
                            "id": "sparse-image",
                            "asset_id": asset,
                            "asset_revision": 1,
                            "asset_version_id": version,
                            "width_twips": round(width * 20),
                            "height_twips": round(height * 20),
                        },
                    }
                ]
            }
        ]
    }
    materials = [
        {
            "asset_id": asset,
            "asset_revision": 1,
            "asset_version_id": version,
            "bytes": normalized["bytes"],
            "normalization_proof": normalized["proof"],
        }
    ]
    document = pdfium.PdfDocument.new()
    page = document.new_page(11906 / 20, 16838 / 20)
    original = Image.open(io.BytesIO(normalized["bytes"]))
    bitmap = pdfium.PdfBitmap.from_pil(original, recopy=True)
    try:
        image = pdfium.PdfImage.new(document)
        image.set_bitmap(bitmap)
        image.set_matrix(pdfium.PdfMatrix(width, 0, 0, height, left, bottom))
        page.insert_obj(image)
        if covered:
            # Exactly the architect's 25x25 local white cover, far smaller than
            # the image. The 450x360 variant adds whitespace without enlarging it.
            cover = pdfium.raw.FPDFPageObj_CreateNewRect(left + 45, bottom + height - 55, 25, 25)
            pdfium.raw.FPDFPageObj_SetFillColor(cover, 255, 255, 255, 255)
            pdfium.raw.FPDFPath_SetDrawMode(cover, pdfium.raw.FPDF_FILLMODE_WINDING, 0)
            pdfium.raw.FPDFPage_InsertObject(page, cover)
        page.gen_content()
        output = io.BytesIO()
        document.save(output)
        return output.getvalue(), snapshot, materials
    finally:
        bitmap.close()
        original.close()
        page.close()
        document.close()


@pytest.mark.parametrize("size", [(120, 90), (450, 360)])
@pytest.mark.parametrize("foreground", ["black", "grey", "pixel"])
def test_sparse_digit_and_single_pixel_survive_whitespace_without_mean_dilution(size, foreground):
    baseline = rich_pdf.inspect_images(
        *specimen(size, foreground, covered=False), templates.DEFAULT_STYLE
    )
    covered = rich_pdf.inspect_images(
        *specimen(size, foreground, covered=True), templates.DEFAULT_STYLE
    )
    assert baseline["coverage"] == "complete", baseline
    assert covered["matched_drawings"] == 1  # Raw pixels still match the frozen original.
    assert covered["coverage"] == "partial", covered
    assert "AUTHORING_IMAGE_VISIBILITY_UNVERIFIED" in covered["gaps"]
    assert covered["images"][0]["page_pixel_maximum_channel_error"] > 2


def test_fractional_position_and_scaling_use_same_isolated_PDFium_raster():
    proof = rich_pdf.inspect_images(
        *specimen((120, 90), "black", covered=False, fractional=True), templates.DEFAULT_STYLE
    )
    assert proof["coverage"] == "complete", proof
    assert proof["images"][0]["page_pixel_maximum_channel_error"] <= 2


def test_visibility_pixel_budget_is_partial_without_unbounded_reference_renders(monkeypatch):
    monkeypatch.setattr(rich_pdf, "MAX_VISIBILITY_RENDER_PIXELS", 1)
    proof = rich_pdf.inspect_images(
        *specimen((120, 90), "black", covered=False), templates.DEFAULT_STYLE
    )
    assert proof["coverage"] == "partial"
    assert proof["visibility_render_pixels"] == 0
    assert "AUTHORING_IMAGE_VISIBILITY_PIXEL_BUDGET" in proof["gaps"]
