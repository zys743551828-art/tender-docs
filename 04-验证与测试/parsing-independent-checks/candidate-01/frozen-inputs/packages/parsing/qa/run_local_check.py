"""Reproducible synthetic PDF and real loopback HTTP smoke evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from packages.parsing.http_port import ParsingHttp


def main():
    output = Path(__file__).resolve().parent
    raw = io.BytesIO()
    page = canvas.Canvas(raw, pagesize=(420, 595))
    page.drawString(30, 550, "1. Project requirements")
    page.drawString(30, 520, "Project duration 120 days")
    page.showPage()
    page.showPage()  # Blank page must stay usable without invented OCR failure.
    image = Image.new("RGB", (1400, 1800), "white")
    painter = ImageDraw.Draw(image)
    font_file = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    font = ImageFont.truetype(str(font_file), 56)
    painter.text((100, 160), "施工工期 120 天", font=font, fill="black")
    painter.text((100, 280), "质量要求：合格", font=font, fill="black")
    page.drawImage(ImageReader(image), 0, 0, width=420, height=595)
    page.showPage()
    for x in (30, 180, 330):
        page.line(x, 400, x, 500)
    for y in (400, 450, 500):
        page.line(30, y, 330, y)
    for x, y, text in ((40, 470, "Item"), (190, 470, "Days"), (40, 420, "Road"), (190, 420, "120")):
        page.drawString(x, y, text)
    page.showPage()
    page.drawString(30, 550, "Engineering diagram remains unread")
    page.circle(200, 200, 70)
    page.showPage()
    page.setPageRotation(90)
    page.drawString(30, 250, "Rotated physical page")
    page.showPage()
    page.save()
    data = raw.getvalue()
    (output / "synthetic-source.pdf").write_bytes(data)
    parser = ParsingHttp()
    result = parser.parse(data)
    assert result["page_count"] == 6
    assert result["pages"][1]["method"] == "blank"
    assert not result["pages"][1]["gaps"]
    assert "施工" in result["pages"][2]["text"].replace(" ", "")
    assert "120" in result["pages"][2]["text"]
    assert result["pages"][3]["tables"][0]["rows"] == [["Item", "Days"], ["Road", "120"]]
    assert result["pages"][5]["rotation"] == 90
    assert result["coverage"] == "partial"
    with httpx.Client(trust_env=False, timeout=5) as client:
        unauthorized = client.get(parser.url + "/identity").status_code
        assert unauthorized == 401
        rejected = client.post(
            parser.url + "/parse",
            content=data,
            headers={
                "Authorization": "Bearer " + os.environ["PARSING_RUNTIME_TOKEN"],
                "X-Source-Sha256": "0" * 64,
            },
        ).status_code
        assert rejected == 422
    evidence = {
        "status": "passed_synthetic_scope",
        "transport": "real_loopback_http",
        "source_digest": hashlib.sha256(data).hexdigest(),
        "source_bytes": len(data),
        "parser_digest": parser.parser_digest,
        "config_digest": parser.config_digest,
        "identity": parser.identity,
        "font_fixture_digest": hashlib.sha256(font_file.read_bytes()).hexdigest(),
        "business_documents": 0,
        "business_accuracy_accepted": False,
        "target_baseline_qualified": False,
        "unauthorized_status": unauthorized,
        "wrong_source_digest_status": rejected,
        "coverage": result["coverage"],
        "pages": [
            {
                "physical_page": row["physical_page"],
                "rotation": row["rotation"],
                "width": row["width"],
                "height": row["height"],
                "method": row["method"],
                "text_digest": hashlib.sha256(row["text"].encode()).hexdigest(),
                "block_count": len(row["blocks"]),
                "table_count": len(row["tables"]),
                "quality": row["quality"],
                "gaps": row["gaps"],
            }
            for row in result["pages"]
        ],
    }
    (output / "real-http-result.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "pages": len(result["pages"]),
                "coverage": result["coverage"],
                "parser_digest": parser.parser_digest,
            }
        )
    )


if __name__ == "__main__":
    main()
