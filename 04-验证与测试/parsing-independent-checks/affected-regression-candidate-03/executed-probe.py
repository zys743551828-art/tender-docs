"""Visibility fallback delta: reuse independent scan/grid/rotation physical pages."""

import hashlib
import json
import math
import os
from pathlib import Path

import httpx
import pypdfium2 as pdfium


def main():
    out = Path(os.environ["QA_PARSING_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    candidate = Path(
        "/workspace/tender-docs/04-验证与测试/parsing-development-checks/candidate-03/candidate.json"
    )
    candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert candidate_sha == "2c94690cabd1a29fdf461d2879b8000d527fa404d3b1249d4082d5ba66e9309f"
    declared = json.loads(candidate.read_text())
    for item in declared["sources"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
    source = Path(
        "/workspace/tender-docs/04-验证与测试/parsing-independent-checks/candidate-01/independent-source.pdf"
    )
    payload = source.read_bytes()
    (out / "executed-source.pdf").write_bytes(payload)
    headers = {"Authorization": "Bearer " + os.environ["PARSING_RUNTIME_TOKEN"]}
    with httpx.Client(timeout=90, trust_env=False) as client:
        identity = client.get("http://127.0.0.1:8812/identity", headers=headers).json()
        assert identity["parser_digest"] == declared["parser_digest"]
        response = client.post(
            "http://127.0.0.1:8812/parse",
            content=payload,
            headers={
                **headers,
                "Content-Type": "application/pdf",
                "X-Source-Sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
    assert response.status_code == 200
    parsed = response.json()
    (out / "actual-response.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    )
    pages = parsed["pages"]
    checks = []

    def check(name, value, observation=None):
        checks.append(dict(name=name, passed=bool(value), observation=observation))

    check(
        "same_original_digest_and_physical_pages",
        parsed["source_digest"] == hashlib.sha256(payload).hexdigest()
        and [p["physical_page"] for p in pages] == list(range(1, 8)),
    )
    scan = "".join(pages[0]["text"].split())
    check(
        "real_chinese_scan_still_partial",
        "市政" in scan
        and "90" in scan
        and pages[0]["method"] == "tesseract_ocr"
        and pages[0]["quality"] == "partial"
        and bool(pages[0]["gaps"]),
    )
    check(
        "blank_still_has_no_evidence",
        pages[2]["method"] == "blank" and not pages[2]["text"] and not pages[2]["blocks"],
    )
    check(
        "grid_numbers_retained_with_honest_scope",
        "90" in pages[3]["text"]
        and "120" in pages[3]["text"]
        and (
            bool(pages[3]["tables"]) or pages[3]["quality"] == "partial" and bool(pages[3]["gaps"])
        ),
        {
            "method": pages[3]["method"],
            "quality": pages[3]["quality"],
            "tables": len(pages[3]["tables"]),
            "gaps": pages[3]["gaps"],
        },
    )
    valid = True
    for p in pages:
        for block in p["blocks"]:
            box = block["bbox"]
            valid &= (
                block["physical_page"] == p["physical_page"]
                and block["digest"] == hashlib.sha256(block["text_content"].encode()).hexdigest()
            )
            valid &= (
                box is None
                or len(box) == 4
                and all(math.isfinite(v) for v in box)
                and 0 <= box[0] < box[2] <= p["width"]
                and 0 <= box[1] < box[3] <= p["height"]
            )
    check("blocks_preserve_sha_page_and_finite_point_bbox", valid)
    document = pdfium.PdfDocument(payload)
    page = document[4]
    bitmap = page.render(scale=1)
    ink = bitmap.to_pil().convert("L").point(lambda v: 255 if v < 200 else 0).getbbox()
    x, y = (ink[0] + ink[2]) / 2, (ink[1] + ink[3]) / 2
    check(
        "rotated_physical_page_matches_independent_render",
        pages[4]["rotation"] == 90
        and (pages[4]["width"], pages[4]["height"]) == page.get_size()
        and any(
            b["bbox"]
            and b["bbox"][0] - 3 <= x <= b["bbox"][2] + 3
            and b["bbox"][1] - 3 <= y <= b["bbox"][3] + 3
            for b in pages[4]["blocks"]
        ),
        {"render_ink_bbox": ink, "width": pages[4]["width"], "height": pages[4]["height"]},
    )
    bitmap.close()
    page.close()
    document.close()
    result = dict(
        passed=all(c["passed"] for c in checks),
        checks=checks,
        candidate_sha256=candidate_sha,
        parser_digest=identity["parser_digest"],
        paid_model_requests=0,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope="Visibility/OCR/bbox affected regression; unchanged authorization/limits retain original candidate01 evidence",
    )
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
