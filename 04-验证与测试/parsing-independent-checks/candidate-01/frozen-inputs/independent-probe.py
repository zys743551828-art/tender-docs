"""自制PDF夹具→实际8812服务，独立断言可见文字/旋转/表格/限制。"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixtures(out):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    raster = Image.new("RGB", (2200, 1100), "white")
    draw = ImageDraw.Draw(raster)
    face = ImageFont.truetype(str(font), 88)
    for index, line in enumerate(["市政道路施工组织设计", "本项目工期90天", "安全文明施工措施"]):
        draw.text((100, 150 + index * 230), line, font=face, fill="black")
    raster.save(out / "independent-chinese-scan.png")
    data = io.BytesIO()
    pdf = canvas.Canvas(data, pagesize=(595, 842))
    # 1: 整页图像，无文字层；真实中文OCR。
    pdf.drawImage(ImageReader(raster), 30, 280, width=535, height=268)
    pdf.showPage()
    # 2: 可见正文与不可见错误文字共存，不能靠全页有墨水证明所有文字可见。
    pdf.setFont("Helvetica", 22)
    pdf.drawString(60, 740, "VISIBLE BODY: duration 90 days.")
    hidden = pdf.beginText(60, 680)
    hidden.setFont("Helvetica", 18)
    hidden.setTextRenderMode(3)
    hidden.textLine("INVISIBLE_RULE: duration 999 days.")
    pdf.drawText(hidden)
    pdf.showPage()
    # 3: 真空白。
    pdf.showPage()
    # 4: 独立有边框规则网格，中文与数字可逐格核对。
    for x in [60, 240, 430]:
        pdf.line(x, 450, x, 630)
    for y in [450, 510, 570, 630]:
        pdf.line(60, y, 430, y)
    pdf.setFont("STSong-Light", 18)
    for x, y, text in [
        (75, 590, "项目"),
        (255, 590, "工期"),
        (75, 530, "道路"),
        (255, 530, "90天"),
        (75, 470, "桥梁"),
        (255, 470, "120天"),
    ]:
        pdf.drawString(x, y, text)
    pdf.showPage()
    # 5: 后续设置PDF物理rotation=90，独立渲染墨迹校验point坐标。
    pdf.setFont("Helvetica", 24)
    pdf.drawString(65, 720, "ROTATION_MARK 90")
    pdf.showPage()
    # 6: 工程图并有可读标题，不能把文字层成功当图理解。
    pdf.setFont("Helvetica", 20)
    pdf.drawString(60, 760, "SYNTHETIC BRIDGE DIAGRAM")
    pdf.circle(250, 500, 65)
    pdf.line(100, 430, 420, 430)
    pdf.line(250, 435, 250, 565)
    pdf.showPage()
    # 7: 完全不可见文字层，视觉空白而提取有字。
    hidden = pdf.beginText(60, 720)
    hidden.setFont("Helvetica", 20)
    hidden.setTextRenderMode(3)
    hidden.textLine("HIDDEN_ONLY_NOT_VISIBLE 555")
    pdf.drawText(hidden)
    pdf.showPage()
    pdf.save()
    writer = PdfWriter()
    for number, page in enumerate(PdfReader(io.BytesIO(data.getvalue())).pages, 1):
        if number == 5:
            page.rotate(90)
        writer.add_page(page)
    final = io.BytesIO()
    writer.write(final)
    payload = final.getvalue()
    (out / "independent-source.pdf").write_bytes(payload)
    return payload, font


def main():
    out = Path(os.environ["QA_PARSING_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    candidate = ROOT / "packages/parsing/qa/candidate.json"
    record = dict(
        scope="独立合成PDF与实际TCP解析端口；非Docling/Paddle或真实业务精度",
        passed=False,
        checks=[],
        failures=[],
        paid_model_requests=0,
    )
    snapshots = out / "frozen-inputs"
    snapshots.mkdir()
    try:
        assert (
            digest(candidate.read_bytes())
            == "47a7720ac0a99f772cbde0cab56ae2a70e647b3527a638deeb0836289ca2bb10"
        )
        declaration = json.loads(candidate.read_text())
        for row in declaration["sources"]:
            source = ROOT / row["path"]
            assert digest(source.read_bytes()) == row["sha256"], row["path"]
            target = snapshots / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        (snapshots / "candidate.json").write_bytes(candidate.read_bytes())
        assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "parsing.env"
        base = os.environ["PARSING_RUNTIME_URL"].rstrip("/")
        assert base == "http://127.0.0.1:8812"
        auth = {"Authorization": "Bearer " + os.environ["PARSING_RUNTIME_TOKEN"]}

        def call(method, path, body=None, headers=None):
            values = dict(auth)
            values.update(headers or {})
            if body is not None:
                values.setdefault("Content-Type", "application/pdf")
                values.setdefault("X-Source-Sha256", digest(body))
            with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
                return client.request(method, base + path, content=body, headers=values)

        def check(name, condition, details=None):
            row = {"name": name, "passed": bool(condition)}
            if details is not None:
                row["observation"] = details
            record["checks"].append(row)
            if not condition:
                record["failures"].append(row)

        identity = call("GET", "/identity")
        assert identity.status_code == 200
        info = identity.json()
        (out / "actual-identity.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n"
        )
        check(
            "实际服务身份与冻结parser/config匹配",
            info["parser_digest"] == declaration["parser_digest"]
            and info["config_digest"] == declaration["config_digest"],
        )
        check(
            "替代profile与目标/业务资格明确",
            info["identity"]["target_baseline_qualified"] is False
            and info["identity"]["visual_understanding"] is False,
        )
        for path in ["/identity", "/parse"]:
            status = call(
                "GET" if path == "/identity" else "POST",
                path,
                b"%PDF-1.4" if path == "/parse" else None,
                {"Authorization": "Bearer invalid-synthetic"},
            ).status_code
            check("错误令牌" + path + "拒绝", status == 401, {"status": status})
        check("服务不暴露OpenAPI", call("GET", "/openapi.json").status_code == 404)
        payload, font = fixtures(out)
        response = call("POST", "/parse", payload)
        check("合成PDF实际HTTP200", response.status_code == 200, {"status": response.status_code})
        assert response.status_code == 200
        parsed = response.json()
        (out / "actual-parse-result.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
        )
        pages = parsed["pages"]
        check(
            "物理页顺序与原件SHA绑定",
            parsed["source_digest"] == digest(payload)
            and [p["physical_page"] for p in pages] == list(range(1, 8)),
        )
        check(
            "图像/扫描范围保持partial且质量未业务验收",
            parsed["coverage"] == "partial"
            and parsed["quality_benchmark"] == "not_business_validated"
            and bool(parsed["gaps"]),
        )
        scan = "".join(pages[0]["text"].split())
        check(
            "自制纯图像真实中文OCR",
            pages[0]["method"] == "tesseract_ocr" and "市政" in scan and "90" in scan,
            {
                "method": pages[0]["method"],
                "contains_chinese": "市政" in scan,
                "contains_90": "90" in scan,
            },
        )
        check(
            "扫描布局/表格/图理解不冒称完成",
            pages[0]["quality"] == "partial" and bool(pages[0]["gaps"]),
        )
        check(
            "空白页真实区分且无伪文字",
            pages[2]["method"] == "blank" and not pages[2]["text"] and not pages[2]["blocks"],
        )
        check(
            "规则表格独立逐格数值和结构",
            any(
                t["structure"] == "regular_grid"
                and any("90" in str(row) for row in t["rows"])
                and any("120" in str(row) for row in t["rows"])
                for t in pages[3]["tables"]
            ),
        )
        check("工程图保留未理解缺口", pages[5]["quality"] == "partial" and bool(pages[5]["gaps"]))
        check(
            "完全隐藏文字不冒称可见读取",
            pages[6]["quality"] == "partial" and "TEXT_LAYER_NOT_VISIBLE" in pages[6]["gaps"],
        )
        mixed = pages[1]
        check(
            "混合页不可见错误条款不得成为无缺口可靠文字",
            "INVISIBLE_RULE" not in mixed["text"]
            or mixed["quality"] != "usable"
            or bool(mixed["gaps"]),
            {
                "hidden_rule_in_text": "INVISIBLE_RULE" in mixed["text"],
                "quality": mixed["quality"],
                "gaps": mixed["gaps"],
            },
        )
        areas_valid = True
        for page in pages:
            for block in page["blocks"]:
                area = block["bbox"]
                areas_valid &= block["physical_page"] == page["physical_page"] and block[
                    "digest"
                ] == digest(block["text_content"].encode())
                areas_valid &= (
                    area is None
                    or len(area) == 4
                    and all(math.isfinite(x) for x in area)
                    and 0 <= area[0] < area[2] <= page["width"]
                    and 0 <= area[1] < area[3] <= page["height"]
                )
        check("全部block页/摘要/旋转后point bbox有限且在页内", areas_valid)
        document = pdfium.PdfDocument(payload)
        page = document[4]
        bitmap = page.render(scale=1)
        image = bitmap.to_pil().convert("L")
        mask = image.point(lambda value: 255 if value < 200 else 0)
        ink = mask.getbbox()
        width, height = page.get_size()
        blocks = pages[4]["blocks"]
        cx = (ink[0] + ink[2]) / 2
        cy = (ink[1] + ink[3]) / 2
        check(
            "rotation90尺寸与独立渲染墨迹point位置一致",
            pages[4]["rotation"] == 90
            and pages[4]["width"] == width
            and pages[4]["height"] == height
            and any(
                b["bbox"]
                and b["bbox"][0] - 3 <= cx <= b["bbox"][2] + 3
                and b["bbox"][1] - 3 <= cy <= b["bbox"][3] + 3
                for b in blocks
            ),
            {
                "independent_ink_bbox": ink,
                "width": width,
                "height": height,
                "rotation": pages[4]["rotation"],
            },
        )
        bitmap.close()
        page.close()
        document.close()
        for name, body, headers, expected in [
            ("错误SHA", payload, {"X-Source-Sha256": "0" * 64}, 422),
            ("缺摘要", payload, {"X-Source-Sha256": ""}, 422),
            ("损坏PDF", b"%PDF-1.7\nThis is not a valid PDF", {}, 422),
            ("非PDF", b"not pdf", {}, 422),
        ]:
            rejected = call("POST", "/parse", body, headers)
            check(
                name + "实际拒绝",
                rejected.status_code == expected,
                {"status": rejected.status_code, "code": rejected.json().get("code")},
            )
        encrypted = PdfWriter()
        encrypted.add_blank_page(100, 100)
        encrypted.encrypt("synthetic-password")
        buffer = io.BytesIO()
        encrypted.write(buffer)
        denied = call("POST", "/parse", buffer.getvalue())
        check(
            "加密PDF实际拒绝",
            denied.status_code == 422,
            {"status": denied.status_code, "code": denied.json().get("code")},
        )
        too_many = PdfWriter()
        for _ in range(1001):
            too_many.add_blank_page(72, 72)
        buffer = io.BytesIO()
        too_many.write(buffer)
        denied = call("POST", "/parse", buffer.getvalue())
        check(
            "1001物理页上限实际拒绝",
            denied.status_code == 422,
            {"status": denied.status_code, "code": denied.json().get("code")},
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(
                pool.map(lambda _: call("POST", "/parse", payload).status_code, [1, 2])
            )
        check("实际同时两文只一解析另一busy", statuses == [200, 429], {"statuses": statuses})
        record["fixture_sha256"] = digest(payload)
        record["font_sha256"] = digest(font.read_bytes())
        record["candidate_sha256"] = digest(candidate.read_bytes())
        record["passed"] = not record["failures"]
    except Exception as error:
        record["fatal"] = dict(
            type=type(error).__name__, message="独立解析验收未完成，不输出token/原始请求"
        )
    record["probe_sha256"] = digest(Path(__file__).read_bytes())
    (snapshots / "independent-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(
        dict(
            passed=record["passed"],
            checks=len(record["checks"]),
            failures=record["failures"],
            fatal=record.get("fatal"),
        )
    )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
