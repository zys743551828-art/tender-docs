"""Synthetic byte-level parser checks, not a business accuracy benchmark."""

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from packages.parsing.core import LocalParser, ParseError, bbox


@pytest.fixture(scope="module")
def parser():
    return LocalParser()


def pdf_bytes(draw):
    result = io.BytesIO()
    page = canvas.Canvas(result, pagesize=(420, 595))
    draw(page)
    page.save()
    return result.getvalue()


def test_actual_bytes_page_rotation_text_and_blank(parser):
    def draw(page):
        page.drawString(30, 550, "1. Project requirements")
        page.drawString(30, 520, "Project duration 120 days")
        page.showPage()
        page.showPage()
        page.setPageRotation(90)
        page.drawString(30, 250, "Rotated physical page")
        page.showPage()

    data = pdf_bytes(draw)
    result = parser.parse(data, hashlib.sha256(data).hexdigest())
    assert result["page_count"] == 3
    assert [page["physical_page"] for page in result["pages"]] == [1, 2, 3]
    assert "120 days" in result["pages"][0]["text"]
    assert result["pages"][1]["method"] == "blank"
    assert not result["pages"][1]["gaps"]
    assert result["pages"][2]["rotation"] == 90
    assert result["chapters"][0]["title"].startswith("1.")
    for page in result["pages"]:
        for block in page["blocks"]:
            assert block["bbox"] == bbox(block["bbox"], page["width"], page["height"])
            assert block["digest"] == hashlib.sha256(block["text_content"].encode()).hexdigest()
    with pytest.raises(ParseError, match="PDF_DIGEST_MISMATCH"):
        parser.parse(data, "0" * 64)


def test_chinese_scanned_page_real_ocr_and_explicit_visual_gaps(parser):
    image = Image.new("RGB", (1400, 1800), "white")
    painter = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 56)
    painter.text((100, 160), "施工工期 120 天", font=font, fill="black")
    painter.text((100, 280), "质量要求：合格", font=font, fill="black")
    data = pdf_bytes(lambda page: page.drawImage(ImageReader(image), 0, 0, width=420, height=595))
    result = parser.parse(data)
    page = result["pages"][0]
    assert page["method"] == "tesseract_ocr"
    assert "120" in page["text"]
    assert "施工" in page["text"].replace(" ", "")
    assert "SCAN_LAYOUT_TABLES_AND_VISUALS_UNVERIFIED" in page["gaps"]
    assert result["coverage"] == "partial"
    assert page["blocks"] and all(block["bbox"] for block in page["blocks"])


def test_ruled_grid_has_cells_and_bbox(parser):
    def draw(page):
        for x in (30, 180, 330):
            page.line(x, 400, x, 500)
        for y in (400, 450, 500):
            page.line(30, y, 330, y)
        for x, y, text in (
            (40, 470, "Item"),
            (190, 470, "Days"),
            (40, 420, "Road"),
            (190, 420, "120"),
        ):
            page.drawString(x, y, text)

    result = parser.parse(pdf_bytes(draw))
    table = result["pages"][0]["tables"][0]
    assert table["structure"] == "regular_grid"
    assert table["rows"] == [["Item", "Days"], ["Road", "120"]]
    assert len(table["cells"]) == 4
    assert all(
        cell["bbox"] and cell["row_span"] == cell["column_span"] == 1 for cell in table["cells"]
    )


def test_vector_diagram_is_not_complete_text_read(parser):
    def draw(page):
        page.drawString(30, 550, "Construction diagram")
        page.circle(200, 200, 70)
        page.line(50, 200, 350, 200)

    result = parser.parse(pdf_bytes(draw))
    assert "VECTOR_GRAPHICS_NOT_UNDERSTOOD" in result["pages"][0]["gaps"]
    assert result["coverage"] == "partial"


def test_model_asset_tamper_rejected(tmp_path):
    source = Path(__file__).resolve().parents[1] / ".local/parsing-models"
    for name in ("eng", "chi_sim"):
        (tmp_path / (name + ".traineddata")).write_bytes(
            (source / (name + ".traineddata")).read_bytes()
        )
    with (tmp_path / "chi_sim.traineddata").open("ab") as target:
        target.write(b"tamper")
    with pytest.raises(ParseError, match="PARSING_ASSET_DIGEST_MISMATCH"):
        LocalParser(tmp_path)


def test_broken_pdf_and_bbox_are_rejected(parser):
    with pytest.raises(ParseError):
        parser.parse(b"%PDF-broken")
    assert bbox([0, 0, float("nan"), 10], 420, 595) is None
    assert bbox([0, 0, 421, 595], 420, 595) is None


def test_invisible_text_layer_does_not_claim_complete_read(parser):
    def draw(page):
        text = page.beginText(30, 300)
        text.setTextRenderMode(3)
        text.textLine("Hidden tender requirement")
        page.drawText(text)

    result = parser.parse(pdf_bytes(draw))
    assert result["coverage"] == "partial"
    assert "TEXT_LAYER_NOT_VISIBLE" in result["pages"][0]["gaps"]


def test_table_does_not_hide_unrelated_diagram(parser):
    def draw(page):
        for x in (30, 180, 330):
            page.line(x, 400, x, 500)
        for y in (400, 450, 500):
            page.line(30, y, 330, y)
        page.drawString(40, 470, "Item")
        page.drawString(190, 420, "120 days")
        page.rect(30, 50, 100, 100)

    result = parser.parse(pdf_bytes(draw))
    assert result["pages"][0]["tables"]
    assert "VECTOR_GRAPHICS_NOT_UNDERSTOOD" in result["pages"][0]["gaps"]
