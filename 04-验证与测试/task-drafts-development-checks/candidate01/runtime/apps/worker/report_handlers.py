"""真实Word/PDF报告和PDF原页文本批注；计算不持PG连接，发布受fence约束。"""

import ctypes
import io
import os
from contextlib import closing

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from packages.authoring.build import WRITER_DIGEST, assemble_docx, render_pdf
from packages.domain import storage
from packages.domain.reports import REPORT_METHOD_DIGEST, context, request_row
from packages.domain.review_data import read_json_blob
from packages.domain.runtime import DomainError, audit, clean, connection, fetch, sha, uid


def paragraphs(lines):
    result: list[dict] = []
    for line in lines:
        text = "".join(ch for ch in str(line) if ord(ch) >= 32 or ch in "\n\t")
        for start in range(0, len(text), 4000):
            result.append(
                {
                    "type": "paragraph",
                    "attrs": {"id": "report-paragraph-" + str(len(result))},
                    "content": [{"type": "text", "text": text[start : start + 4000]}],
                }
            )
    return result or [{"type": "paragraph", "attrs": {"id": "report-empty"}, "content": []}]


def report_document(plan, evidence_manifest):
    value = plan["content"]
    manifest = value["manifest"]
    lines = [
        "评审报告副本，不是提交正文",
        f"受检文件：{value['body']['original_name']}；原件ID：{manifest['body_version_id']}",
        f"本轮评审：{value['review']['id']}；规则包：{manifest['rule_set_id']}；资料版本：{manifest['material_revision']}",
        f"冻结证据：{manifest['evidence_manifest_digest']}；业务评估时点：{manifest['as_of']}",
        f"投标主体（本轮冻结）：{evidence_manifest.get('identity', {}).get('bidder_name', '历史冻结资料未提供，不按当前主体补填')}",
        f"本轮当前性：{'当前' if value['review']['current'] else '历史输入'}；来源状态：{value['source_state']}",
        *value["warnings"],
        "评分显示：已隐藏"
        if value["prediction_policy"] == "hidden"
        else "正式预测分尚不可用，不以核心核对推算分数",
    ]
    for name in ("source_scope", "core_extraction", "judgment", "scoring"):
        layer = value["review"]["coverage_layers"].get(name, {})
        lines.append(f"覆盖 {name}：{layer.get('state', 'unknown')}；缺口：{layer.get('gaps', [])}")
    for check in value.get("content_checks", {}).get("checks", []):
        label = "跨章节矛盾" if check["kind"] == "consistency" else "范围残留"
        lines.append(
            f"独立检测 {label}：{check['state']}；已读证据块{check['read_evidence_count'] if check['read_evidence_count'] is not None else '未知'}/{check['planned_evidence_count'] if check['planned_evidence_count'] is not None else '未知'}；缺口：{check['gaps']}"
        )
    lines.append(
        f"人工确认待处理：{value['review']['coverage_layers'].get('human_confirmation_pending', '未知')}"
    )
    evidence = {item["id"]: item for item in value["evidence"]}
    for row in value["matrix"]["rows"]:
        rule = row["rule"]
        lines.extend(
            [
                f"条款 {rule['id']}（{rule['kind']}）：{rule['statement']}",
                f"条件：{rule['conditions']}；例外：{rule['exceptions']}；适用：{rule['scope']}",
                f"条款效力：{rule['provenance_status']}；人工确认：{rule['confirmation']}；核对状态：{row['check_status']}；结论：{row['verdict'] or '未判断'}",
            ]
        )
        for identity in rule["evidence_ids"]:
            item = evidence.get(identity)
            if item:
                lines.append(
                    f"原页：{item['original_name']} 第{item['physical_page']}物理页；证据：{identity}；逐字内容：{item['text_content']}"
                )
    lines.append("风险与问题（包括先前已记录仍未解决的关键问题）")
    for finding in value["findings"]:
        lines.extend(
            [
                f"{finding['id']} [{finding['severity']}/{finding['lifecycle']}]：{finding['statement']}",
                "先前检测发现，本轮未运行"
                if finding["detection_run_state"] == "not_run_this_round"
                else "本轮存在核对记录",
                f"人工决定：{finding['decision'] or '尚无'}；结论：{finding['verdict']}；已检范围：{finding['searched_ranges']}",
            ]
        )
        for identity in finding["evidence_ids"]:
            item = evidence.get(identity)
            if item:
                lines.append(
                    f"证据 {identity}：{item['original_name']} 第{item['physical_page']}物理页；{item['text_content']}"
                )
    nodes = paragraphs(lines)
    snapshot = {
        "sections": [
            {
                "type": "doc",
                "attrs": {
                    "title": "评审报告" if ordinal == 0 else "评审报告（续）",
                    "ordinal": ordinal,
                    "rule_ids": [],
                    "source_refs": [],
                },
                "content": nodes[start : start + 40],
            }
            for ordinal, start in enumerate(range(0, len(nodes), 40))
        ]
    }
    return assemble_docx(snapshot)


def note(page, text, index=0):
    annotation = pdfium_c.FPDFPage_CreateAnnot(page, pdfium_c.FPDF_ANNOT_TEXT)
    if not annotation:
        raise DomainError("PDF_ANNOTATION_UNAVAILABLE", "PDF批注创建失败，原件保持不变", 503)
    try:
        encoded = (text + "\0").encode("utf-16-le")
        units = (ctypes.c_ushort * (len(encoded) // 2)).from_buffer_copy(encoded)
        width, height = page.get_size()
        x, y = min(20 + index % 8 * 20, max(0, width - 20)), max(0, height - 40 - index // 8 * 20)
        rectangle = pdfium_c.FS_RECTF(x, y + 16, x + 16, y)
        if not pdfium_c.FPDFAnnot_SetRect(
            annotation, ctypes.byref(rectangle)
        ) or not pdfium_c.FPDFAnnot_SetStringValue(annotation, b"Contents", units):
            raise DomainError("PDF_ANNOTATION_UNAVAILABLE", "PDF批注写入失败，原件保持不变", 503)
    finally:
        pdfium_c.FPDFPage_CloseAnnot(annotation)


def annotated_pdf(raw, value):
    gaps = []
    with closing(pdfium.PdfDocument(raw)) as document:
        if len(document) == 0:
            raise DomainError("PDF_EMPTY", "受检PDF无可批注页面", 409)
        evidence = {item["id"]: item for item in value["evidence"]}
        with closing(document[0]) as page:
            note(
                page,
                "评审批注副本，不是提交正文。注释标记只代表物理页级定位，不代表逐字范围高亮。正式预测分未提供。",
            )
        for index, finding in enumerate(value["findings"]):
            locations = [
                evidence[identity]
                for identity in finding["evidence_ids"]
                if identity in evidence
                and evidence[identity]["document_version_id"]
                == value["manifest"]["body_version_id"]
            ]
            number = locations[0]["physical_page"] if locations else 1
            if not 1 <= number <= len(document):
                raise DomainError("PDF_EVIDENCE_PAGE_INVALID", "冻结原页证据与受检PDF不匹配", 409)
            location_note = (
                "正文证据物理页"
                if locations
                else "正文无已定位片段，置于首页问题索引，不能视为该页正文违反"
            )
            if not locations:
                gaps.append("ANNOTATION_WITHOUT_BODY_RANGE")
            with closing(document[number - 1]) as page:
                note(
                    page,
                    f"[{finding['severity']}/{finding['lifecycle']}] {finding['statement']}\n{location_note}\n证据ID：{finding['evidence_ids']}\n已检范围：{finding['searched_ranges']}",
                    index + 1,
                )
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue(), sorted(set(gaps))


def compute(message, lease=None):
    if message["step_key"] != "render-report":
        raise DomainError("UNKNOWN_REPORT_STAGE", "报告任务阶段不合法", 409)
    with connection(True) as c:
        record = request_row(c, message["payload_ref"])
        if (
            record["operation_id"] != message["operation_id"]
            or sha({"id": record["report_id"], "blob_digest": record["frozen_blob_digest"]})
            != message["input_digest"]
        ):
            raise DomainError("REPORT_TASK_INPUT_CHANGED", "报告冻结任务身份不一致", 409)
        blob = fetch(c, "SELECT * FROM blobs WHERE id=%s", (record["frozen_blob_id"],))
    plan = read_json_blob(blob)
    with connection(True) as c:
        evidence_blob = fetch(
            c,
            "SELECT b.* FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN blobs b ON b.id=i.evidence_manifest_blob_id WHERE r.id=%s AND r.bid_id=%s",
            (record["review_id"], message["bid_id"]),
        )
    evidence_manifest = read_json_blob(evidence_blob)
    if (
        plan["content"]["writer_digest"] != WRITER_DIGEST
        or plan["content"]["report_method_digest"] != REPORT_METHOD_DIGEST
        or plan["context"]["context_digest"] != record["context_digest"]
        or sha(plan["content"]) != record["context_digest"]
    ):
        raise DomainError("REPORT_PROFILE_CHANGED", "报告冻结内容或生成方法已变化，请重新生成", 409)
    request = plan["request"]
    gaps, chain = [], {}
    if request["artifact_kind"] == "annotated_review_copy":
        body = plan["content"]["body"]
        raw = storage.get(body["object_key"])
        if len(raw) != body["byte_size"] or sha(raw) != body["digest"]:
            raise DomainError("REPORT_ORIGINAL_CORRUPTED", "受检原件真实字节核验失败", 409)
        data, gaps = annotated_pdf(raw, plan["content"])
        chain = {
            "original_digest": body["digest"],
            "annotation_method": "pdfium-text-notes/1",
            "page_scope": "physical_page_only",
        }
    else:
        data = report_document(plan, evidence_manifest)
        if request["format"] == "pdf":
            rendered = render_pdf(
                data,
                executable=os.environ.get("AUTHORING_RENDERER", "soffice"),
                font_path=os.environ.get(
                    "AUTHORING_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
                ),
                expected_identity=plan["renderer_identity"],
                sandbox_attested=False,
            )
            if rendered.pdf is None:
                raise DomainError(
                    "REPORT_PDF_CONVERSION_FAILED",
                    "PDF报告转换未完成，可重试或选择Word报告",
                    503,
                    {"gaps": list(rendered.gaps)},
                )
            data, gaps, chain = rendered.pdf, list(rendered.gaps), rendered.chain
    media = (
        "application/pdf"
        if request["format"] == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    digest = sha(data)
    key = "blobs/" + digest
    storage.put(key, data, media)
    if sha(storage.get(key)) != digest:
        raise DomainError("REPORT_OBJECT_CORRUPTED", "报告对象写入后核验失败", 503)
    return {
        "report_id": record["report_id"],
        "context_digest": record["context_digest"],
        "digest": digest,
        "byte_size": len(data),
        "object_key": key,
        "media_type": media,
        "gaps": gaps,
        "conversion_chain": chain,
    }


def publish(c, message, outcome):
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    record = request_row(c, message["payload_ref"])
    current, _ = context(c, record["review_id"])
    if (
        outcome["report_id"] != record["report_id"]
        or outcome["context_digest"] != record["context_digest"]
        or current["context_digest"] != record["context_digest"]
        or current["presentation_revision"] != record["presentation_revision"]
        or current["source_impact_revision"] != record["source_impact_revision"]
    ):
        raise DomainError(
            "REPORT_PRESENTATION_CHANGED",
            "展示政策、人工状态或来源效力已变化，请按当前状态重新生成",
            409,
        )
    blob_id = uid()
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
        (
            blob_id,
            outcome["digest"],
            outcome["object_key"],
            outcome["byte_size"],
            outcome["media_type"],
        ),
    )
    blob = fetch(c, "SELECT * FROM blobs WHERE digest=%s", (outcome["digest"],))
    existing = c.execute(
        "SELECT id,blob_id FROM artifact_presentations WHERE review_id=%s AND artifact_kind=%s AND format=%s AND presentation_revision=%s AND source_impact_revision=%s",
        (
            record["review_id"],
            record["artifact_kind"],
            record["format"],
            record["presentation_revision"],
            record["source_impact_revision"],
        ),
    ).fetchone()
    if existing and existing["blob_id"] != blob["id"]:
        raise DomainError(
            "REPORT_CACHE_IDENTITY_CONFLICT",
            "已有副本的内容身份不同，请重新读取展示状态后生成",
            409,
        )
    presentation = existing["id"] if existing else uid()
    if not existing:
        c.execute(
            "INSERT INTO artifact_presentations(id,bid_id,review_id,artifact_kind,format,presentation_revision,source_impact_revision,blob_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                presentation,
                message["bid_id"],
                record["review_id"],
                record["artifact_kind"],
                record["format"],
                record["presentation_revision"],
                record["source_impact_revision"],
                blob["id"],
            ),
        )
    # document_versions统一取件身份，绝不将报告注册成bid_body。
    actor = record["actor_id"]
    version = uid()
    filename = (
        ("评审批注副本" if record["artifact_kind"] == "annotated_review_copy" else "评审报告")
        + "."
        + record["format"]
    )
    c.execute(
        "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,clock_timestamp(),%s,%s)",
        (version, message["bid_id"], blob["id"], filename, record["artifact_kind"], actor),
    )
    logical = uid()
    c.execute(
        "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,'other',1)",
        (logical, message["bid_id"]),
    )
    c.execute(
        "INSERT INTO document_file_versions(bid_id,file_id,version_id,revision,previous_version_id) VALUES(%s,%s,%s,1,NULL)",
        (message["bid_id"], logical, version),
    )
    c.execute(
        "UPDATE document_files SET head_version_id=%s,revision=2 WHERE id=%s", (version, logical)
    )
    audit(
        c,
        actor,
        message["bid_id"],
        "report.published",
        {
            "report_id": record["report_id"],
            "document_version_id": str(version),
            "presentation_id": str(presentation),
            "digest": outcome["digest"],
            "context_digest": record["context_digest"],
            "gaps": outcome["gaps"],
            "conversion_chain": outcome["conversion_chain"],
        },
    )
    return clean(
        {
            "result_ref": f"/api/v1/report-exports/{record['report_id']}",
            "document_version_id": version,
            "artifact_kind": record["artifact_kind"],
            "gaps": outcome["gaps"],
        }
    )
