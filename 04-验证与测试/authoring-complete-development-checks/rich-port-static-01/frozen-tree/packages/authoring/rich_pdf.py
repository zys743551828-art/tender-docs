"""Bounded actual PDF image correspondence, separate from text/navigation proof."""

from __future__ import annotations

import io
import math

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageStat

from packages.authoring import rich_images
from packages.domain.runtime import sha

PROTOCOL = "authoring-PDF-raster-correspondence/1"


def expected_images(snapshot, materials):
    catalog = {(m["asset_id"], m["asset_revision"], m["asset_version_id"]): m for m in materials}
    result = []
    decoded: dict[str, tuple] = {}
    total_pixels = 0
    for section in snapshot["sections"]:
        for block in section["content"]:
            if block["type"] != "image":
                continue
            attrs = block["attrs"]
            material = catalog[
                (attrs["asset_id"], attrs["asset_revision"], attrs["asset_version_id"])
            ]
            rich_images.verify_normalized(material["bytes"], material["normalization_proof"])
            proof = material["normalization_proof"]
            digest = proof["output_digest"]
            if digest in decoded:
                rgb, alpha, pixel_digest = decoded[digest]
            else:
                total_pixels += proof["width_px"] * proof["height_px"]
                if total_pixels > 32_000_000:
                    from packages.domain.runtime import DomainError

                    for pixels, _, _ in decoded.values():
                        pixels.close()
                    raise DomainError(
                        "AUTHORING_IMAGE_PIXEL_BUDGET",
                        "全稿不同图片累计超过3200万像素，请压缩后生成",
                        422,
                    )
                with Image.open(io.BytesIO(material["bytes"])) as source:
                    rgb = source.convert("RGB")
                    if source.mode == "RGBA":
                        alpha_channel = source.getchannel("A")
                        try:
                            alpha = alpha_channel.getextrema()
                        finally:
                            alpha_channel.close()
                    else:
                        alpha = (255, 255)
                pixel_digest = sha(rgb.tobytes())
                decoded[digest] = (rgb, alpha, pixel_digest)
            result.append(
                {
                    "node_id": attrs["id"],
                    "asset_version_id": attrs["asset_version_id"],
                    "width": attrs["width_twips"] / 20,
                    "height": attrs["height_twips"] / 20,
                    "pixels": rgb,
                    "pixel_digest": pixel_digest,
                    "opaque": alpha == (255, 255),
                }
            )
    return result


def inspect_images(data, snapshot, materials, style):
    expected = expected_images(snapshot, materials)
    records: list[dict] = []
    gaps: list[str] = []
    matched: set[int] = set()
    total_actual_pixels = 0
    pdf = pdfium.PdfDocument(data)
    try:
        for page_index in range(len(pdf)):
            if total_actual_pixels > 32_000_000:
                break
            page = pdf[page_index]
            rendered = None
            page_pixels = None
            try:
                width, height = page.get_size()
                count = 0
                for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE], max_depth=2):
                    count += 1
                    if len(records) >= 500 or count > 500:
                        gaps.append("AUTHORING_PDF_IMAGE_COUNT_UNVERIFIED")
                        break
                    size = image.get_size()
                    total_actual_pixels += size[0] * size[1]
                    if total_actual_pixels > 32_000_000:
                        gaps.append("AUTHORING_PDF_IMAGE_PIXEL_BUDGET")
                        break
                    if (
                        not 0 < size[0] <= 10000
                        or not 0 < size[1] <= 10000
                        or size[0] * size[1] > 16_000_000
                    ):
                        gaps.append("AUTHORING_PDF_IMAGE_PIXEL_LIMIT")
                        continue
                    left, bottom, right, top = image.get_pos()
                    if (
                        not all(math.isfinite(v) for v in [left, bottom, right, top])
                        or not left < right
                        or not bottom < top
                    ):
                        gaps.append("AUTHORING_PDF_IMAGE_GEOMETRY_UNVERIFIED")
                        continue
                    bitmap = image.get_bitmap(render=False)
                    try:
                        actual_pixels = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                    try:
                        digest = sha(actual_pixels.tobytes())
                        candidates = [
                            index
                            for index, item in enumerate(expected)
                            if index not in matched
                            and item["pixel_digest"] == digest
                            and item["pixels"].size == actual_pixels.size
                            and abs(item["width"] - (right - left)) <= 1
                            and abs(item["height"] - (top - bottom)) <= 1
                        ]
                    finally:
                        actual_pixels.close()
                    if not candidates:
                        records.append(
                            {
                                "physical_page": page_index + 1,
                                "matched": False,
                                "decoded_pixel_digest": digest,
                            }
                        )
                        gaps.append("AUTHORING_PDF_IMAGE_PIXELS_OR_SIZE_UNVERIFIED")
                        continue
                    index = candidates[0]
                    matched.add(index)
                    item = expected[index]
                    local_gaps = []
                    if not item["opaque"]:
                        local_gaps.append("AUTHORING_ALPHA_IMAGE_CORRESPONDENCE_UNVERIFIED")
                    if (
                        left < style["margin_left_twips"] / 20 - 3
                        or right > width - style["margin_right_twips"] / 20 + 3
                        or bottom < style["margin_bottom_twips"] / 20 - 3
                        or top > height - style["margin_top_twips"] / 20 + 3
                    ):
                        local_gaps.append("AUTHORING_IMAGE_OUTSIDE_BODY")
                    error = None
                    if page.get_rotation() != 0 or width > 1000 or height > 1500:
                        local_gaps.append("AUTHORING_IMAGE_VISIBILITY_UNVERIFIED")
                    else:
                        if rendered is None:
                            rendered = page.render(scale=1)
                            page_pixels = rendered.to_pil().convert("RGB")
                        if page_pixels is None:
                            raise ValueError("PDF_PAGE_RASTER_UNAVAILABLE")
                        box = (
                            max(0, round(left)),
                            max(0, round(height - top)),
                            min(page_pixels.width, round(right)),
                            min(page_pixels.height, round(height - bottom)),
                        )
                        if box[2] <= box[0] or box[3] <= box[1]:
                            local_gaps.append("AUTHORING_IMAGE_VISIBILITY_UNVERIFIED")
                        else:
                            crop = page_pixels.crop(box)
                            reference = item["pixels"].resize(crop.size, Image.Resampling.BICUBIC)
                            try:
                                difference = ImageChops.difference(crop, reference)
                                try:
                                    error = sum(ImageStat.Stat(difference).mean) / 3
                                    # Exact source pixels plus actual page visibility. A flat
                                    # source uses a tighter threshold to reject covered white.
                                    extrema = reference.getextrema()
                                    flat = all(high - low <= 1 for low, high in extrema)
                                    if error > (1 if flat else 3):
                                        local_gaps.append("AUTHORING_IMAGE_VISIBILITY_UNVERIFIED")
                                finally:
                                    difference.close()
                            finally:
                                crop.close()
                                reference.close()
                    gaps.extend(local_gaps)
                    records.append(
                        {
                            "physical_page": page_index + 1,
                            "matched": True,
                            "node_id": item["node_id"],
                            "asset_version_id": item["asset_version_id"],
                            "decoded_pixel_digest": digest,
                            "bbox": [left, bottom, right, top],
                            "page_pixel_mean_error": error,
                            "gaps": local_gaps,
                        }
                    )
            except (pdfium.PdfiumError, OSError, ValueError, OverflowError):
                gaps.append("AUTHORING_IMAGE_INSPECTION_INCOMPLETE")
            finally:
                if page_pixels is not None:
                    page_pixels.close()
                if rendered is not None:
                    rendered.close()
                page.close()
    finally:
        pdf.close()
        closed = set()
        for item in expected:
            if id(item["pixels"]) not in closed:
                closed.add(id(item["pixels"]))
                item["pixels"].close()
    if len(matched) != len(expected) or len(records) != len(expected):
        gaps.append("AUTHORING_IMAGE_COUNT_OR_MATCH_UNVERIFIED")
    return {
        "protocol": PROTOCOL,
        "coverage": "partial" if gaps else "complete",
        "gaps": list(dict.fromkeys(gaps)),
        "expected_drawings": len(expected),
        "matched_drawings": len(matched),
        "images": records,
    }
