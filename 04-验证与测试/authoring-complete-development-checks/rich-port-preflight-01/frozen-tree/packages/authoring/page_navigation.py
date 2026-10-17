"""Measured navigation for owned generated PDFs; no renderer, SQL or safety grant.

Only direct internal TOC destinations are supported. This is not a general PDF
active-content/visibility scanner or evidence that rich DOCX rendering succeeded.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import math
import re
import unicodedata
from typing import Any, NoReturn
from uuid import UUID

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from packages.domain.runtime import DomainError, sha

PROTOCOL = "authoring-page-navigation/1"
MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 2000
MAX_LINKS = 4000
MAX_PAGE_CHARS = 200000
MAX_TOTAL_CHARS = 2000000
MODES = {"chapter_start_new_page", "continuous"}


def _invalid(message) -> NoReturn:
    raise DomainError("AUTHORING_NAVIGATION_INPUT_INVALID", message, 422)


def anchor_for(node_id):
    try:
        value = UUID(node_id)
    except (ValueError, TypeError, AttributeError):
        _invalid("章节身份必须是规范UUID")
    if str(value) != node_id:
        _invalid("章节身份必须是规范UUID")
    return "sec_" + value.hex


def _normalized(value):
    return "".join(unicodedata.normalize("NFC", value).split())


def _entries(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        _invalid("实页定位要求1至500个完整活动章节")
    seen: dict[str, Any] = {}
    stack: list[str] = []
    required = {"node_id", "parent_id", "anchor", "title", "expected_start_page"}
    for item in entries:
        if not isinstance(item, dict) or set(item) != required:
            _invalid("实页定位输入字段不符合冻结协议")
        identity = item["node_id"]
        if item["anchor"] != anchor_for(identity) or identity in seen:
            _invalid("章节锚点须与唯一稳定身份对应")
        parent = item["parent_id"]
        if parent is not None and parent not in stack:
            _invalid("章节必须是完整先序树且父章位于当前祖先链")
        if parent is None:
            stack = []
        else:
            stack = stack[: stack.index(parent) + 1]
        stack.append(identity)
        if len(stack) > 6:
            _invalid("章节树最多六层")
        title, page = item["title"], item["expected_start_page"]
        if not isinstance(title, str) or not _normalized(title) or len(title) > 1000:
            _invalid("实际显示章节标题无效")
        if page is not None and (type(page) is not int or not 1 <= page <= MAX_PAGES):
            _invalid("目录输入页码只能是实际物理页或未定值")
        seen[identity] = item
    return entries


def _dest(document, destination):
    if not destination:
        return None
    page = raw.FPDFDest_GetDestPageIndex(document, destination)
    has_x, has_y, has_zoom = C.c_int(), C.c_int(), C.c_int()
    x, y, zoom = C.c_float(), C.c_float(), C.c_float()
    valid = raw.FPDFDest_GetLocationInPage(destination, has_x, has_y, has_zoom, x, y, zoom)
    top = float(y.value) if valid and has_y.value and math.isfinite(y.value) else None
    return {"page": page + 1, "top": top}


def _style(style):
    if not isinstance(style, dict):
        _invalid("实页定位须提供冻结版式边距对象")
    result = {}
    for name in ("top", "bottom", "left", "right"):
        value = style.get("margin_" + name + "_twips")
        if type(value) is not int or not 0 <= value <= 5000:
            _invalid("实页定位须提供冻结版式的四边距twips")
        result[name] = value / 20
    return result


def _first_heading(textpage, width, height, margins, title, image):
    chars = []
    for index in range(textpage.count_chars()):
        code = raw.FPDFText_GetUnicode(textpage, index)
        if not code or code > 0x10FFFF or chr(code).isspace():
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            continue
        if (
            left >= margins["left"] - 3
            and right <= width - margins["right"] + 3
            and bottom >= margins["bottom"] - 3
            and top <= height - margins["top"] + 3
        ):
            origin_x, origin_y = C.c_double(), C.c_double()
            if not raw.FPDFText_GetCharOrigin(textpage, index, origin_x, origin_y):
                continue
            obj = raw.FPDFText_GetTextObject(textpage, index)
            red, green, blue, alpha = (C.c_uint() for _ in range(4))
            if (
                not obj
                or raw.FPDFTextObj_GetTextRenderMode(obj) != 0
                or not raw.FPDFPageObj_GetFillColor(obj, red, green, blue, alpha)
                or alpha.value != 255
            ):
                return None
            crop = image.crop(
                (
                    max(0, math.floor(left) - 1),
                    max(0, math.floor(height - top) - 1),
                    min(image.width, math.ceil(right) + 1),
                    min(image.height, math.ceil(height - bottom) + 1),
                )
            ).convert("L")
            low, high = crop.getextrema()
            if high - low < 32:
                return None
            chars.append((chr(code), left, bottom, right, top, origin_y.value))
    lines: list[tuple[float, list[Any]]] = []
    for char in sorted(chars, key=lambda c: (-c[5], c[1])):
        middle = char[5]
        if not lines or abs(middle - lines[-1][0]) > 3:
            lines.append((middle, [char]))
        else:
            lines[-1][1].append(char)
    expected, accumulated, used = _normalized(title), "", []
    for _, line in lines[:12]:
        line.sort(key=lambda c: c[1])
        accumulated += _normalized("".join(c[0] for c in line))
        used.extend(line)
        if accumulated == expected:
            return [
                min(c[1] for c in used),
                min(c[2] for c in used),
                max(c[3] for c in used),
                max(c[4] for c in used),
            ]
        if not expected.startswith(accumulated):
            break
    return None


def inspect_navigation(pdf_bytes, entries, *, mode="chapter_start_new_page", style=None):
    """Measure actual 1-based physical pages and verify printed TOC inputs.

    entries is the complete frozen active preorder. Four exact layout margins are
    required; TOC origin pages are measured, never supplied as prior-round facts.
    An expected_start_page of None means a blank or em-dash placeholder this round.
    """
    entries = _entries(entries)
    margins = _style(style)
    if mode not in MODES:
        _invalid("章页模式必须显式冻结")
    result = {
        "protocol": PROTOCOL,
        "pdf_digest": sha(pdf_bytes)
        if isinstance(pdf_bytes, bytes) and len(pdf_bytes) <= MAX_BYTES
        else None,
        "mode": mode,
        "page_count": None,
        "observed_toc_pages": [],
        "entries": [],
        "navigation_coverage": "partial",
        "toc_values_verified": False,
        "coverage": "partial",
        "gaps": [],
    }
    gaps = set()
    rows = [
        {
            **item,
            "start_page": None,
            "end_page": None,
            "mapping_method": None,
            "title_bbox": None,
            "toc_origin_page": None,
            "printed_toc_page": None,
            "gaps": [],
        }
        for item in entries
    ]
    result["entries"] = rows
    if not isinstance(pdf_bytes, bytes) or not 0 < len(pdf_bytes) <= MAX_BYTES:
        result["gaps"] = ["NAV_PDF_BYTES_INVALID"]
        return result
    try:
        document = pdfium.PdfDocument(pdf_bytes)
    except pdfium.PdfiumError:
        result["gaps"] = ["NAV_PDF_UNREADABLE"]
        return result
    pages, texts = {}, {}
    try:
        count = len(document)
        result["page_count"] = count
        if not 1 <= count <= MAX_PAGES:
            result["gaps"] = ["NAV_PAGE_LIMIT"]
            return result
        if raw.FPDFDoc_GetJavaScriptActionCount(document):
            gaps.add("NAV_DOCUMENT_JAVASCRIPT_UNSUPPORTED")
        groups: dict[tuple[int, float], Any] = {}
        total_chars, total_links, total_annotations = 0, 0, 0
        rendered_pixels = 0
        for index in range(count):
            page = document[index]
            pages[index + 1] = page
            if page.get_rotation() != 0:
                gaps.add("NAV_ROTATED_PAGE_UNSUPPORTED")
            text = page.get_textpage()
            texts[index + 1] = text
            chars = text.count_chars()
            total_chars += chars
            if chars > MAX_PAGE_CHARS or total_chars > MAX_TOTAL_CHARS:
                gaps.add("NAV_TEXT_RESOURCE_LIMIT")
                break
            annotation_count = raw.FPDFPage_GetAnnotCount(page)
            total_annotations += annotation_count
            if annotation_count < 0 or total_annotations > MAX_LINKS:
                gaps.add("NAV_ANNOTATION_RESOURCE_LIMIT")
                break
            for annot_index in range(annotation_count):
                annotation = raw.FPDFPage_GetAnnot(page, annot_index)
                if annotation:
                    try:
                        if raw.FPDFAnnot_GetSubtype(annotation) != raw.FPDF_ANNOT_LINK:
                            gaps.add("NAV_NONLINK_ANNOTATION_UNSUPPORTED")
                    finally:
                        raw.FPDFPage_CloseAnnot(annotation)
            cursor, link = C.c_int(), raw.FPDF_LINK()
            while raw.FPDFLink_Enumerate(page, cursor, link):
                total_links += 1
                if total_links > MAX_LINKS:
                    gaps.add("NAV_LINK_LIMIT")
                    break
                action = raw.FPDFLink_GetAction(link)
                if action:
                    gaps.add(
                        "NAV_GOTO_ACTION_UNSUPPORTED"
                        if raw.FPDFAction_GetType(action) == raw.PDFACTION_GOTO
                        else "NAV_NONLOCAL_OR_UNKNOWN_ACTION"
                    )
                    continue
                destination = _dest(document, raw.FPDFLink_GetDest(document, link))
                if (
                    not destination
                    or not 1 <= destination["page"] <= count
                    or destination["top"] is None
                ):
                    gaps.add("NAV_DIRECT_DESTINATION_UNKNOWN")
                    continue
                rect = raw.FS_RECTF()
                if not raw.FPDFLink_GetAnnotRect(link, rect):
                    gaps.add("NAV_LINK_RECT_UNKNOWN")
                    continue
                box = [float(rect.left), float(rect.bottom), float(rect.right), float(rect.top)]
                if not all(math.isfinite(v) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
                    gaps.add("NAV_LINK_RECT_UNKNOWN")
                    continue
                key = (destination["page"], round(destination["top"], 2))
                group = groups.setdefault(key, {"destination": destination, "rects": []})
                record = (index + 1, *[round(v, 2) for v in box])
                if record not in group["rects"]:
                    group["rects"].append(record)
            if total_links > MAX_LINKS:
                break
        candidates = []
        for group in groups.values():
            rects = sorted(group["rects"], key=lambda v: (v[0], -v[4], v[1]))
            if len({r[0] for r in rects}) != 1:
                gaps.add("NAV_TOC_ENTRY_MULTIPAGE_UNSUPPORTED")
                continue
            label = "".join(texts[r[0]].get_text_bounded(r[1], r[2], r[3], r[4]) for r in rects)
            candidates.append({**group, "rects": rects, "label": _normalized(label)})
        used = set()
        for row in rows:
            named = _dest(
                document, raw.FPDF_GetNamedDestByName(document, row["anchor"].encode("ascii"))
            )
            matches = [
                (i, c) for i, c in enumerate(candidates) if c["label"] == _normalized(row["title"])
            ]
            if named:
                matches = [
                    (i, c)
                    for i, c in matches
                    if c["destination"]["page"] == named["page"]
                    and named["top"] is not None
                    and abs(c["destination"]["top"] - named["top"]) <= 1
                ]
            if len(matches) != 1 or matches[0][0] in used:
                row["gaps"].append("NAV_TOC_TARGET_MISSING_OR_AMBIGUOUS")
                continue
            i, candidate = matches[0]
            used.add(i)
            destination = candidate["destination"]
            row["start_page"] = destination["page"]
            row["mapping_method"] = (
                "named_destination_and_toc_link" if named else "unique_toc_link_title"
            )
            row["toc_origin_page"] = candidate["rects"][0][0]
            target = pages.get(row["start_page"])
            if target is None:
                row["gaps"].append("NAV_TARGET_PAGE_NOT_INSPECTED")
                continue
            width, height = target.get_size()
            if (
                width <= 0
                or height <= 0
                or width > 5000
                or height > 5000
                or width * height > 8_000_000
                or margins["left"] + margins["right"] >= width
                or margins["top"] + margins["bottom"] >= height
            ):
                row["gaps"].append("NAV_PAGE_GEOMETRY_UNSUPPORTED")
                continue
            rendered_pixels += width * height
            if rendered_pixels > 500_000_000:
                row["gaps"].append("NAV_RASTER_RESOURCE_LIMIT")
                continue
            bitmap = target.render(scale=1)
            image = None
            try:
                image = bitmap.to_pil()
                bbox = _first_heading(
                    texts[row["start_page"]], width, height, margins, row["title"], image
                )
            finally:
                if image is not None:
                    image.close()
                bitmap.close()
            if bbox is None:
                row["gaps"].append("NAV_TARGET_PAGE_FIRST_HEADING_MISMATCH")
            elif not bbox[1] - 4 <= destination["top"] <= bbox[3] + 24:
                row["gaps"].append("NAV_DESTINATION_NOT_AT_HEADING")
            else:
                row["title_bbox"] = [round(v, 2) for v in bbox]
            last = candidate["rects"][-1]
            origin = pages[last[0]]
            suffix = texts[last[0]].get_text_bounded(
                last[3] + 0.5, last[2] - 1, origin.get_width() - margins["right"], last[4] + 1
            )
            literal = _normalized(suffix)
            expected = row["expected_start_page"]
            if expected is None:
                if literal not in {"", "—"}:
                    row["gaps"].append("NAV_TOC_PRINTED_VALUE_MISMATCH")
            else:
                numeric = re.fullmatch(r"[.·…]*([1-9][0-9]{0,3})", literal)
                row["printed_toc_page"] = int(numeric[1]) if numeric else None
                if row["printed_toc_page"] != expected:
                    row["gaps"].append("NAV_TOC_PRINTED_VALUE_MISMATCH")
        if len(used) != len(candidates):
            gaps.add("NAV_UNEXPECTED_INTERNAL_LINK")
        starts = [row["start_page"] for row in rows]
        if all(page is not None for page in starts):
            if mode == "chapter_start_new_page" and any(
                a >= b for a, b in zip(starts, starts[1:], strict=False)
            ):
                gaps.add("NAV_CHAPTERS_NOT_ON_DISTINCT_INCREASING_PAGES")
            elif any(a > b for a, b in zip(starts, starts[1:], strict=False)):
                gaps.add("NAV_CHAPTER_ORDER_MISMATCH")
            if any(row["toc_origin_page"] >= min(starts) for row in rows):
                gaps.add("NAV_TOC_NOT_BEFORE_CHAPTERS")
        result["observed_toc_pages"] = sorted(
            {row["toc_origin_page"] for row in rows if row["toc_origin_page"] is not None}
        )
        for row in rows:
            gaps.update(row["gaps"])
        navigation_gaps = gaps - {"NAV_TOC_PRINTED_VALUE_MISMATCH"}
        result["navigation_coverage"] = "complete" if not navigation_gaps else "partial"
        result["toc_values_verified"] = not any(
            "NAV_TOC_PRINTED_VALUE_MISMATCH" in row["gaps"] for row in rows
        ) and len(used) == len(rows)
        if result["navigation_coverage"] == "complete" and mode == "chapter_start_new_page":
            parents = {row["node_id"]: row["parent_id"] for row in rows}
            for index, row in enumerate(rows):
                end = count
                for following in rows[index + 1 :]:
                    ancestor = following["parent_id"]
                    while ancestor is not None and ancestor != row["node_id"]:
                        ancestor = parents[ancestor]
                    if ancestor != row["node_id"]:
                        end = following["start_page"] - 1
                        break
                row["end_page"] = end
        if mode == "continuous":
            gaps.add("NAV_CONTINUOUS_END_PAGES_UNKNOWN")
        if any(row["expected_start_page"] != row["start_page"] for row in rows):
            gaps.add("NAV_TOC_NUMBERS_NOT_AT_FIXED_POINT")
        result["gaps"] = sorted(gaps)
        result["coverage"] = "complete" if not gaps else "partial"
        return result
    except (pdfium.PdfiumError, ValueError, OverflowError, OSError):
        result["coverage"] = "partial"
        result["navigation_coverage"] = "partial"
        result["toc_values_verified"] = False
        result["gaps"] = sorted(gaps | {"NAV_PDF_INSPECTION_FAILED"})
        return result
    finally:
        for text in texts.values():
            text.close()
        for page in pages.values():
            page.close()
        document.close()


def converge_navigation(iterations, *, context_digest, mode="chapter_start_new_page", style=None):
    """Verify up to three supplied actual render results; never invokes a renderer.

    Each iteration supplies actual pdf_bytes/docx_bytes and its frozen entries.
    Context digest binds the caller's same snapshot/layout/renderer identity.
    Actual DOCX bytes are hashed here; OOXML generation validation remains separate.
    """
    if not isinstance(context_digest, str) or not re.fullmatch("[0-9a-f]{64}", context_digest):
        _invalid("目录固定点须绑定冻结快照／版式／renderer上下文摘要")
    if not isinstance(iterations, list) or not 1 <= len(iterations) <= 3:
        return {
            "protocol": PROTOCOL,
            "coverage": "partial",
            "fixed_point": False,
            "gaps": ["NAV_TOC_RENDER_LIMIT_EXCEEDED"],
            "iterations": [],
        }
    proofs: list[dict[str, Any]] = []
    gaps: set[str] = set()
    previous, identity = None, None
    final = None
    for iteration in iterations:
        if not isinstance(iteration, dict) or set(iteration) != {
            "pdf_bytes",
            "docx_bytes",
            "entries",
        }:
            _invalid("每轮目录验证须含实际DOCX／PDF字节与冻结entries")
        docx = iteration["docx_bytes"]
        if not isinstance(docx, bytes) or not 0 < len(docx) <= 100 * 1024 * 1024:
            _invalid("目录验证缺少实际DOCX字节")
        entries = _entries(iteration["entries"])
        current_identity = [
            {k: v for k, v in entry.items() if k != "expected_start_page"} for entry in entries
        ]
        if identity is not None and current_identity != identity:
            gaps.add("NAV_TOC_STRUCTURE_CHANGED_BETWEEN_RENDERS")
        identity = current_identity
        inputs = {entry["node_id"]: entry["expected_start_page"] for entry in entries}
        if previous is not None and inputs != previous:
            gaps.add("NAV_TOC_ITERATION_NOT_DERIVED_FROM_PREVIOUS_MEASUREMENT")
        final = inspect_navigation(iteration["pdf_bytes"], entries, mode=mode, style=style)
        measured = {entry["node_id"]: entry["start_page"] for entry in final["entries"]}
        if final["navigation_coverage"] != "complete" or not final["toc_values_verified"]:
            gaps.update(final["gaps"] or ["NAV_TOC_MEASUREMENT_INCOMPLETE"])
        previous = measured
        proofs.append(
            {
                "pdf_digest": final["pdf_digest"],
                "docx_digest": hashlib.sha256(docx).hexdigest(),
                "toc_input": inputs,
                "measured_starts": measured,
                "page_count": final["page_count"],
                "gaps": final["gaps"],
            }
        )
    if final is None:
        _invalid("目录验证缺少实际轮次")
    if final["coverage"] != "complete":
        gaps.update(final["gaps"])
    fixed = not gaps
    result = {
        "protocol": PROTOCOL,
        "context_digest": context_digest,
        "mode": mode,
        "coverage": "complete" if fixed else "partial",
        "fixed_point": fixed,
        "gaps": sorted(gaps),
        "iterations": proofs,
        "final_navigation": final,
    }
    result["proof_digest"] = sha(result)
    return result
