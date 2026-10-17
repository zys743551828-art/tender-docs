"""Bounded CPU fallback profile: PDFium + pdfplumber + real Tesseract OCR.

This profile does not claim Docling/PaddleOCR equivalence or visual understanding.
All bboxes use the rotated physical page's top-left point coordinate system.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import shutil

# Fixed OCR executable/argv, no shell, private generated image path.
import subprocess  # nosec B404
import tempfile
import time
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from PIL import ImageOps

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 1000
MAX_PIXELS = 12_000_000
MAX_DOCUMENT_SECONDS = 300
HEADING = re.compile(r"^(?:第[一二三四五六七八九十百\d]+[章节篇]|\d+(?:\.\d+){0,3}[、.\s])")


class ParseError(Exception):
    """Public error codes never include document text or filesystem details."""


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def bbox(value, width, height):
    if value is None or len(value) != 4 or any(not math.isfinite(float(x)) for x in value):
        return None
    x0, y0, x1, y1 = (float(x) for x in value)
    # Tiny floating point errors may occur at page edges; never invent a location.
    if x0 < -0.01 or y0 < -0.01 or x1 > width + 0.01 or y1 > height + 0.01:
        return None
    if x0 >= x1 or y0 >= y1:
        return None
    return [max(0, x0), max(0, y0), min(width, x1), min(height, y1)]


class LocalParser:
    def __init__(self, assets: Path | None = None):
        self.assets = assets or Path(
            os.environ.get("PARSING_ASSET_DIRECTORY", str(ROOT / ".local/parsing-models"))
        )
        executable = shutil.which("tesseract")
        manifest = json.loads(Path(__file__).with_name("assets.json").read_text())
        for language, record in manifest.items():
            path = self.assets / (language + ".traineddata")
            if not path.is_file() or digest(path.read_bytes()) != record["sha256"]:
                raise ParseError("PARSING_ASSET_DIGEST_MISMATCH")
        if not executable:
            raise ParseError("OCR_EXECUTABLE_UNAVAILABLE")
        self.tesseract: str = executable
        binary = Path(self.tesseract).read_bytes()
        font_root = Path("/usr/share/fonts")
        font_digests = {
            str(path.relative_to(font_root)): digest(path.read_bytes())
            for path in sorted(font_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in {".ttf", ".ttc", ".otf"}
        }
        self.identity = {
            "profile": "pdfium-pdfplumber-tesseract-cpu-v1",
            "packages": {
                name: importlib.metadata.version(name)
                for name in ["pypdfium2", "pdfplumber", "Pillow"]
            },
            "ocr_binary_digest": digest(binary),
            "parser_source_digest": digest(Path(__file__).read_bytes()),
            "font_manifest_digest": digest(font_digests),
            "assets": manifest,
            "preprocessing": {
                "dpi": 200,
                "psm": 3,
                "languages": "chi_sim+eng",
                "max_pixels": MAX_PIXELS,
            },
            "layout": "ruled-table-grid-and-pdf-word-lines",
            "visual_understanding": False,
            "target_baseline_qualified": False,
        }
        self.parser_digest = digest(self.identity)
        self.config_digest = digest(
            {
                "max_pages": MAX_PAGES,
                "max_bytes": MAX_BYTES,
                "max_document_seconds": MAX_DOCUMENT_SECONDS,
                "coordinate_system": "rotated_page_top_left_points",
            }
        )

    def _ocr(self, image, width, height):
        with tempfile.TemporaryDirectory(prefix="tender-ocr-") as directory:
            source = Path(directory) / "page.png"
            image.save(source)
            source.chmod(0o600)
            environment = dict(os.environ, OMP_THREAD_LIMIT="2")
            try:
                # argv is fixed except local trusted asset/temp paths; shell is disabled.
                result = subprocess.run(  # nosec B603
                    [
                        self.tesseract,
                        str(source),
                        "stdout",
                        "--tessdata-dir",
                        str(self.assets),
                        "-l",
                        "chi_sim+eng",
                        "--psm",
                        "3",
                        "--dpi",
                        "200",
                        "-c",
                        "tessedit_create_tsv=1",
                    ],
                    capture_output=True,
                    timeout=45,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as error:
                raise ParseError("OCR_TIMEOUT") from error
            if result.returncode != 0:
                raise ParseError("OCR_FAILED")
            lines: dict[tuple[str, str, str], list[tuple[dict, float]]] = {}
            for row in csv.DictReader(
                io.StringIO(result.stdout.decode("utf-8", errors="strict")),
                delimiter="\t",
                quoting=csv.QUOTE_NONE,
            ):
                if row.get("level") != "5" or not row.get("text", "").strip():
                    continue
                confidence = float(row["conf"])
                key = (row["block_num"], row["par_num"], row["line_num"])
                lines.setdefault(key, []).append((row, confidence))
            blocks: list[dict[str, Any]] = []
            for values in lines.values():
                rows = [item[0] for item in values]
                left = min(int(row["left"]) for row in rows)
                top = min(int(row["top"]) for row in rows)
                right = max(int(row["left"]) + int(row["width"]) for row in rows)
                bottom = max(int(row["top"]) + int(row["height"]) for row in rows)
                blocks.append(
                    {
                        "kind": "text",
                        "text_content": " ".join(row["text"] for row in rows),
                        "bbox": bbox(
                            [
                                left * width / image.width,
                                top * height / image.height,
                                right * width / image.width,
                                bottom * height / image.height,
                            ],
                            width,
                            height,
                        ),
                        "confidence": sum(item[1] for item in values) / len(values) / 100,
                    }
                )
            return blocks

    def parse(self, data: bytes, expected_digest: str | None = None):
        started = time.monotonic()
        source_digest = digest(data)
        if expected_digest is not None and expected_digest != source_digest:
            raise ParseError("PDF_DIGEST_MISMATCH")
        if not data.startswith(b"%PDF-") or len(data) > MAX_BYTES:
            raise ParseError("INVALID_PDF_OR_SIZE")
        try:
            document = pdfium.PdfDocument(data)
            structured = pdfplumber.open(io.BytesIO(data))
        except Exception as error:
            raise ParseError("PDF_OPEN_FAILED_OR_ENCRYPTED") from error
        try:
            if not 0 < len(document) <= MAX_PAGES or len(structured.pages) != len(document):
                raise ParseError("PDF_PAGE_LIMIT_OR_MISMATCH")
            pages = []
            for index, layout in enumerate(structured.pages):
                physical = document[index]
                try:
                    if time.monotonic() - started > MAX_DOCUMENT_SECONDS:
                        width, height = physical.get_size()
                        pages.append(
                            {
                                "physical_page": index + 1,
                                "width": width,
                                "height": height,
                                "rotation": physical.get_rotation(),
                                "text": "",
                                "blocks": [],
                                "tables": [],
                                "quality": "failed",
                                "method": "not_read",
                                "gaps": ["DOCUMENT_PARSE_TIME_LIMIT"],
                            }
                        )
                    else:
                        pages.append(self._page(physical, layout, index + 1, source_digest))
                except Exception:
                    width, height = physical.get_size()
                    pages.append(
                        {
                            "physical_page": index + 1,
                            "width": width,
                            "height": height,
                            "rotation": physical.get_rotation(),
                            "text": "",
                            "blocks": [],
                            "tables": [],
                            "quality": "failed",
                            "method": "failed",
                            "gaps": ["PAGE_PARSE_FAILED"],
                        }
                    )
                finally:
                    physical.close()
            gaps = [
                {"physical_page": p["physical_page"], "code": code}
                for p in pages
                for code in p["gaps"]
            ]
            chapters = []
            current = []
            for page in pages:
                for block in page["blocks"]:
                    text = block["text_content"].strip()
                    if len(text) < 120 and HEADING.match(text):
                        block["kind"] = "heading"
                        current = [text]
                        chapters.append(
                            {
                                "title": text,
                                "evidence_id": block["id"],
                                "physical_page": page["physical_page"],
                                "inferred": True,
                            }
                        )
                    block["chapter_path"] = list(current)
            coverage = "usable" if not gaps else "partial"
            if all(page["quality"] == "failed" for page in pages):
                coverage = "failed"
            return {
                "protocol_version": 1,
                "source_digest": source_digest,
                "parser_digest": self.parser_digest,
                "config_digest": self.config_digest,
                "profile": self.identity["profile"],
                "page_count": len(pages),
                "pages": pages,
                "chapters": chapters,
                "coverage": coverage,
                "gaps": gaps,
                "coordinate_system": "rotated_page_top_left_points",
                "quality_benchmark": "not_business_validated",
            }
        finally:
            structured.close()
            document.close()

    def _page(self, physical, layout, number, source_digest):
        width, height = (float(x) for x in physical.get_size())
        if not math.isfinite(width * height) or min(width, height) <= 0:
            raise ParseError("PAGE_DIMENSIONS_INVALID")
        scale = min(200 / 72, math.sqrt(MAX_PIXELS / (width * height)))
        bitmap = physical.render(scale=scale)
        try:
            image = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
        try:
            gray = ImageOps.grayscale(image)
            histogram = gray.histogram()
            ink_fraction = sum(histogram[:240]) / (image.width * image.height)
            gray.close()
            gaps = []
            tables = []
            blocks: list[dict[str, Any]] = []
            geometry_valid = (
                abs(float(layout.width) - width) < 0.1 and abs(float(layout.height) - height) < 0.1
            )
            if not geometry_valid:
                gaps.append("LAYOUT_COORDINATE_MISMATCH")
            words = layout.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
            text = "".join(word["text"] for word in words)
            reliable = len(text.strip()) >= 3 and "\ufffd" not in text and text.isprintable()
            if words and ink_fraction < 0.00001:
                gaps.append("TEXT_LAYER_NOT_VISIBLE")
            method = "pdf_text"
            if ink_fraction < 0.00001 and not words:
                method = "blank"
            elif reliable:
                # Reading order is deterministic; only ruled tables claim row/column structure.
                groups: list[list[dict[str, Any]]] = []
                for word in sorted(
                    words, key=lambda item: (round(float(item["top"]) / 3), item["x0"])
                ):
                    if not groups or abs(float(groups[-1][0]["top"]) - float(word["top"])) > 3:
                        groups.append([])
                    groups[-1].append(word)
                for group in groups:
                    group.sort(key=lambda item: item["x0"])
                    area = [
                        min(w["x0"] for w in group),
                        min(w["top"] for w in group),
                        max(w["x1"] for w in group),
                        max(w["bottom"] for w in group),
                    ]
                    blocks.append(
                        {
                            "kind": "text",
                            "text_content": " ".join(word["text"] for word in group),
                            "bbox": bbox(area, width, height) if geometry_valid else None,
                            "confidence": None,
                        }
                    )
            else:
                method = "tesseract_ocr"
                try:
                    blocks = self._ocr(image, width, height)
                    if not blocks:
                        gaps.append("OCR_NO_TEXT_NONBLANK_PAGE")
                    elif any(block["confidence"] < 0.75 for block in blocks):
                        gaps.append("OCR_LOW_CONFIDENCE")
                except ParseError as error:
                    gaps.append(str(error))
                gaps.append("SCAN_LAYOUT_TABLES_AND_VISUALS_UNVERIFIED")
            if reliable:
                for table in layout.find_tables():
                    rows = table.extract()
                    rectangular = (
                        bool(rows)
                        and len({len(row) for row in rows}) == 1
                        and all(cell is not None for row in rows for cell in row)
                    )
                    cells = []
                    for row_index, row in enumerate(table.rows):
                        for column_index, cell_area in enumerate(row.cells):
                            if cell_area is not None:
                                cells.append(
                                    {
                                        "row": row_index,
                                        "column": column_index,
                                        "row_span": 1 if rectangular else None,
                                        "column_span": 1 if rectangular else None,
                                        "text_content": rows[row_index][column_index] or "",
                                        "bbox": bbox(cell_area, width, height)
                                        if geometry_valid
                                        else None,
                                    }
                                )
                    tables.append(
                        {
                            "bbox": bbox(table.bbox, width, height) if geometry_valid else None,
                            "rows": rows,
                            "cells": cells,
                            "structure": "regular_grid" if rectangular else "unverified_merges",
                            "cross_page_joined": False,
                        }
                    )
                    blocks.append(
                        {
                            "kind": "table",
                            "text_content": "\n".join(
                                "\t".join(cell or "" for cell in row) for row in rows
                            ),
                            "bbox": bbox(table.bbox, width, height) if geometry_valid else None,
                            "confidence": None,
                        }
                    )
                    if not rectangular:
                        gaps.append("TABLE_MERGES_UNVERIFIED")
                if layout.images:
                    gaps.append("IMAGE_CONTENT_NOT_UNDERSTOOD")
                # Curves and non-table drawings may convey engineering meaning.
                uncovered_drawings = any(
                    not any(
                        area[0] - 0.1 <= item["x0"] <= item["x1"] <= area[2] + 0.1
                        and area[1] - 0.1 <= item["top"] <= item["bottom"] <= area[3] + 0.1
                        for area in [table["bbox"] for table in tables if table["bbox"]]
                    )
                    for item in [*layout.lines, *layout.rects]
                )
                if layout.curves or uncovered_drawings:
                    gaps.append("VECTOR_GRAPHICS_NOT_UNDERSTOOD")
                if (
                    len(
                        {
                            round(float(char.get("matrix", (1, 0, 0, 1, 0, 0))[1]), 2)
                            for char in layout.chars
                        }
                    )
                    > 1
                ):
                    gaps.append("MIXED_TEXT_ORIENTATION_REQUIRES_REVIEW")
            for ordinal, block in enumerate(blocks):
                block.update(
                    {
                        "ordinal": ordinal,
                        "physical_page": number,
                        "digest": digest(block["text_content"].encode()),
                        "method": method,
                    }
                )
                block["id"] = digest(
                    {
                        "source": source_digest,
                        "parser": self.parser_digest,
                        "page": number,
                        "ordinal": ordinal,
                        "text": block["digest"],
                    }
                )
                if block["bbox"] is None:
                    gaps.append("BLOCK_LOCATION_UNAVAILABLE")
            gaps = list(dict.fromkeys(gaps))
            return {
                "physical_page": number,
                "width": width,
                "height": height,
                "rotation": physical.get_rotation(),
                "text": "\n".join(block["text_content"] for block in blocks),
                "blocks": blocks,
                "tables": tables,
                "method": method,
                "quality": "partial" if gaps else "usable",
                "gaps": gaps,
                "ink_fraction": ink_fraction,
                "render_digest": digest(image.tobytes()),
            }
        finally:
            image.close()
