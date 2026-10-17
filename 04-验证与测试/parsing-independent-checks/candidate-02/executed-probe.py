"""隐藏文字修复独立增量：真实可见原页控制与常见不可见形式。"""

import hashlib
import io
import json
import os
from pathlib import Path

import httpx
from reportlab.pdfgen import canvas


def main():
    out = Path(os.environ["QA_PARSING_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    candidate = Path(
        "/workspace/tender-docs/04-验证与测试/parsing-development-checks/candidate-02/candidate.json"
    )
    assert (
        hashlib.sha256(candidate.read_bytes()).hexdigest()
        == "43d857f3ed5a0952d549bf795d110323596eabbe96288d8a53d3a09edb463da7"
    )
    declaration = json.loads(candidate.read_text())
    frozen = out / "frozen-inputs"
    frozen.mkdir()
    for item in declaration["sources"]:
        source = Path(item["path"])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]
        target = frozen / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    data = io.BytesIO()
    pdf = canvas.Canvas(data, pagesize=(595, 842))

    def visible():
        pdf.setFont("Helvetica", 22)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.drawString(60, 740, "VISIBLE_BODY duration 90 days.")

    visible()
    text = pdf.beginText(60, 680)
    text.setFont("Helvetica", 18)
    text.setTextRenderMode(3)
    text.textLine("INVISIBLE_RULE 999")
    pdf.drawText(text)
    pdf.showPage()
    visible()
    pdf.saveState()
    pdf.setFillAlpha(0)
    pdf.setFont("Helvetica", 18)
    pdf.drawString(60, 680, "ALPHA_ZERO_RULE 888")
    pdf.restoreState()
    pdf.showPage()
    visible()
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica", 18)
    pdf.drawString(60, 680, "WHITE_ON_WHITE_RULE 777")
    pdf.showPage()
    # 真正可见的白色字不能因颜色被误删：黑背景上的白字是正常控制。
    pdf.setFillColorRGB(0, 0, 0)
    pdf.rect(40, 640, 500, 120, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica", 22)
    pdf.drawString(60, 700, "WHITE_VISIBLE_RULE 30 days.")
    pdf.showPage()
    visible()
    pdf.showPage()
    pdf.save()
    payload = data.getvalue()
    (out / "visibility-source.pdf").write_bytes(payload)
    with httpx.Client(timeout=90, trust_env=False) as client:
        identity = client.get(
            "http://127.0.0.1:8812/identity",
            headers={"Authorization": "Bearer " + os.environ["PARSING_RUNTIME_TOKEN"]},
        ).json()
        assert identity["parser_digest"] == declaration["parser_digest"]
        response = client.post(
            "http://127.0.0.1:8812/parse",
            content=payload,
            headers={
                "Authorization": "Bearer " + os.environ["PARSING_RUNTIME_TOKEN"],
                "Content-Type": "application/pdf",
                "X-Source-Sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
    assert response.status_code == 200
    result = response.json()
    (out / "actual-response.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    checks = []
    for index, marker in [
        (0, "INVISIBLE_RULE"),
        (1, "ALPHA_ZERO_RULE"),
        (2, "WHITE_ON_WHITE_RULE"),
    ]:
        page = result["pages"][index]
        checks.append(
            dict(
                name=marker,
                passed=marker not in page["text"]
                and page["quality"] == "partial"
                and bool(page["gaps"]),
                marker_in_text=marker in page["text"],
                quality=page["quality"],
                gaps=page["gaps"],
            )
        )
    checks.append(
        dict(
            name="normal-visible-text-kept",
            passed="90" in result["pages"][4]["text"] and result["pages"][4]["quality"] == "usable",
        )
    )
    checks.append(
        dict(name="white-visible-on-black-kept", passed="30" in result["pages"][3]["text"])
    )
    record = dict(
        scope="解析隐藏字受影响增量真实TCP，不包含稳定鉴权/损坏全量重跑",
        passed=all(c["passed"] for c in checks),
        checks=checks,
        paid_model_requests=0,
        candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        actual_parser_digest=identity["parser_digest"],
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(record)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
