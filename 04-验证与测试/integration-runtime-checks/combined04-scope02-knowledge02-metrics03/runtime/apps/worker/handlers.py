"""PDF真实字节核验及保守的物理页文字提取。"""

import os
import threading
from datetime import timedelta

import pypdfium2 as pdfium
from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.parsing_profile import configured_parser
from packages.domain.parsing_profile import profile as parser_profile
from packages.domain.runtime import DomainError, connection, fetch, sha, uid
from packages.parsing.http_port import ParsingUnavailable

PDFIUM_LOCK = threading.Lock()

PARSER_DIGEST = sha(
    {"parser": "pypdfium2", "version": "4.30.1", "purpose": "physical_page_text_only"}
)
CONFIG_DIGEST = sha({"bbox": None, "ocr": False, "tables": False, "visual": False})


def compute(message):
    if message["step_key"] == "verify-upload":
        with connection(True) as c:
            u = fetch(c, "SELECT * FROM uploads WHERE id=%s", (message["payload_ref"],))
        response = storage.client().get_object(Bucket=storage.bucket(), Key=u["staging_key"])
        try:
            if response["ContentLength"] != u["declared_size"] or response["ContentLength"] > int(
                os.environ.get("MAX_UPLOAD_BYTES", "104857600")
            ):
                raise DomainError("UPLOAD_SIZE_MISMATCH", "原件大小不匹配", 422)
            data = response["Body"].read(u["declared_size"] + 1)
        finally:
            response["Body"].close()
        if len(data) != u["declared_size"] or sha(data) != u["declared_digest"]:
            raise DomainError("UPLOAD_DIGEST_MISMATCH", "原件摘要不匹配", 422)
        if not data.startswith(b"%PDF-"):
            raise DomainError("INVALID_PDF", "文件并非PDF", 422)
        # 正式内容对象不再暴露PUT授权，临时签名不能覆盖正式原件。
        object_key = "blobs/" + u["declared_digest"]
        storage.put(object_key, data, "application/pdf")
        return {
            "upload": u,
            "object_key": object_key,
            "digest": u["declared_digest"],
            "byte_size": len(data),
        }
    if message["step_key"] == "parse-materials":
        parser = configured_parser() if message["handler_version"] == "document/2" else None
        if message["handler_version"] == "document/2" and parser is None:
            raise DomainError("PARSING_RUNTIME_NOT_CONFIGURED", "本轮指定的解析能力未配置", 503)
        parser_digest = parser.parser_digest if parser else PARSER_DIGEST
        config_digest = parser.config_digest if parser else CONFIG_DIGEST
        with connection(True) as c:
            if parser:
                snapshot = fetch(
                    c,
                    "SELECT digest FROM material_snapshots WHERE id=%s AND bid_id=%s",
                    (message["payload_ref"], message["bid_id"]),
                )
                expected = sha(
                    {
                        "id": message["payload_ref"],
                        "digest": snapshot["digest"],
                        "parse_profile": parser_profile(parser),
                    }
                )
                if expected != message["input_digest"]:
                    raise DomainError(
                        "PARSING_PROFILE_CHANGED", "解析方法已变化，请以当前方法重新安排解析", 409
                    )
            rows = c.execute(
                "SELECT v.*,b.object_key FROM material_assignments a JOIN document_versions v ON v.id=a.document_version_id JOIN blobs b ON b.id=v.blob_id WHERE a.snapshot_id=%s ORDER BY a.role,a.ordinal",
                (message["payload_ref"],),
            ).fetchall()
            existing = {
                str(x["document_version_id"])
                for x in c.execute(
                    "SELECT document_version_id FROM parse_runs WHERE bid_id=%s AND parser_digest=%s AND config_digest=%s",
                    (message["bid_id"], parser_digest, config_digest),
                ).fetchall()
            }
        parsed = []
        for doc in rows:
            if str(doc["id"]) in existing:
                continue
            data = storage.get(doc["object_key"])
            if sha(data) != c_digest(doc["blob_id"]):
                raise DomainError("BLOB_CORRUPTED", "原件摘要不一致")
            if parser:
                try:
                    result = parser.parse(data)
                except ParsingUnavailable as error:
                    raise DomainError(
                        "PARSING_RUNTIME_UNAVAILABLE",
                        "解析服务未完成本轮读取，原件已保留",
                        503,
                        retryable=True,
                    ) from error
                parsed.append(
                    {
                        "doc": doc,
                        **result,
                        "coverage": "complete"
                        if result["coverage"] == "usable"
                        else result["coverage"],
                        "gaps": [
                            f"物理页{gap['physical_page']}:{gap['code']}" for gap in result["gaps"]
                        ],
                    }
                )
                continue
            try:
                with PDFIUM_LOCK:
                    pdf = pdfium.PdfDocument(data)
                    count = len(pdf)
                    if count > int(os.environ.get("MAX_PAGES", "1000")):
                        raise DomainError("PDF_PAGE_LIMIT", "PDF页数超限", 413)
                    pages = []
                    gaps = ["结构化表格与图页内容尚未核验；本切片仅提取物理页文字"]
                    for i in range(count):
                        page = pdf[i]
                        width, height = page.get_size()
                        textpage = page.get_textpage()
                        text = textpage.get_text_range()
                        textpage.close()
                        scan = len(text.strip()) < 20
                        page_gaps = (
                            ["OCR_NOT_CONFIGURED：该页文字不足，扫描/图页尚未识别"] if scan else []
                        )
                        if scan:
                            gaps.append(f"第{i + 1}页OCR/视觉范围未覆盖")
                        pages.append(
                            {
                                "physical_page": i + 1,
                                "width": width,
                                "height": height,
                                "rotation": page.get_rotation(),
                                "text": text,
                                "gaps": page_gaps,
                            }
                        )
                        page.close()
                    pdf.close()
                parsed.append({"doc": doc, "pages": pages, "coverage": "partial", "gaps": gaps})
            except DomainError:
                raise
            except Exception:
                parsed.append(
                    {
                        "doc": doc,
                        "pages": [],
                        "coverage": "failed",
                        "gaps": ["PDF_PARSE_FAILED：原件无法解析，未获得对应检查资格"],
                    }
                )
        return parsed
    raise DomainError("UNKNOWN_HANDLER", "不支持的处理器")


def c_digest(blob):
    with connection(True) as c:
        return fetch(c, "SELECT digest FROM blobs WHERE id=%s", (blob,))["digest"]


def publish(c, message, outcome):
    if message["step_key"] == "verify-upload":
        u = outcome["upload"]
        row = fetch(c, "SELECT * FROM uploads WHERE id=%s FOR UPDATE", (u["id"],))
        if row["state"] != "verifying":
            raise DomainError("UPLOAD_STATE_CHANGED", "上传状态已经变化")
        blob = c.execute("SELECT id FROM blobs WHERE digest=%s", (outcome["digest"],)).fetchone()
        if blob is None:
            blob_id = uid()
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
                (
                    blob_id,
                    outcome["digest"],
                    outcome["object_key"],
                    outcome["byte_size"],
                    "application/pdf",
                ),
            )
            blob = fetch(c, "SELECT id FROM blobs WHERE digest=%s", (outcome["digest"],))
        actor = fetch(c, "SELECT created_by FROM upload_batches WHERE id=%s", (u["batch_id"],))[
            "created_by"
        ]
        version = uid()
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                version,
                u["bid_id"],
                blob["id"],
                u["filename"],
                u["expires_at"] - timedelta(hours=24),
                "bid_body" if u["role"] == "body" else "source_original",
                actor,
            ),
        )
        c.execute(
            "UPDATE uploads SET state='verified',verified_version_id=%s,verified_retain_until=now()+interval '7 days',error_code=NULL WHERE id=%s",
            (version, u["id"]),
        )
        c.execute("UPDATE upload_transfers SET revision=revision+1 WHERE upload_id=%s", (u["id"],))
        return {"result_ref": f"/api/v1/uploads/{u['id']}", "document_version_id": str(version)}
    evidence_ids = []
    for parsed in outcome:
        doc = parsed["doc"]
        parse_id = uid()
        parser_digest = parsed.get("parser_digest", PARSER_DIGEST)
        config_digest = parsed.get("config_digest", CONFIG_DIGEST)
        existing = c.execute(
            "SELECT id FROM parse_runs WHERE document_version_id=%s AND parser_digest=%s AND config_digest=%s",
            (doc["id"], parser_digest, config_digest),
        ).fetchone()
        if existing:
            if message["handler_version"] == "document/2":
                publish_readable_source(c, doc["bid_id"], existing["id"])
            continue
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                parse_id,
                doc["bid_id"],
                doc["id"],
                parser_digest,
                config_digest,
                len(parsed["pages"]),
                parsed["coverage"],
                Jsonb(parsed["gaps"]),
            ),
        )
        for page in parsed["pages"]:
            c.execute(
                "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    doc["bid_id"],
                    parse_id,
                    page["physical_page"],
                    page["width"],
                    page["height"],
                    page["rotation"],
                    page.get("quality", "partial" if page["gaps"] else "usable"),
                    Jsonb(page["gaps"]),
                ),
            )
            blocks = page.get("blocks")
            if blocks is None:
                blocks = (
                    [
                        {
                            "ordinal": 0,
                            "kind": "text",
                            "text_content": page["text"],
                            "bbox": None,
                            "digest": sha(page["text"].encode()),
                        }
                    ]
                    if page["text"].strip()
                    else []
                )
            for block in blocks:
                eid = uid()
                evidence_ids.append(str(eid))
                structure = {
                    key: block[key]
                    for key in ("method", "chapter_path", "confidence")
                    if key in block
                }
                if block["kind"] == "table":
                    structure["tables"] = [
                        table for table in page.get("tables", []) if table["bbox"] == block["bbox"]
                    ]
                c.execute(
                    "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest,bbox,structure) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        eid,
                        doc["bid_id"],
                        parse_id,
                        page["physical_page"],
                        block["ordinal"],
                        block["kind"],
                        block["text_content"],
                        block["digest"],
                        Jsonb(block["bbox"]) if block["bbox"] else None,
                        Jsonb(structure) if structure else None,
                    ),
                )
        if message["handler_version"] == "document/2":
            publish_readable_source(c, doc["bid_id"], parse_id)
    return {
        "result_ref": f"/api/v1/bids/{message['bid_id']}/materials",
        "evidence_ids": evidence_ids,
        "coverage": "failed"
        if any(item["coverage"] == "failed" for item in outcome)
        else "partial"
        if any(item["coverage"] == "partial" for item in outcome)
        else "complete",
        "model_review": "not_run",
    }


def publish_readable_source(c, bid_id, parse_id):
    """只发布有真实可读证据的解析；向量索引状态不参与原页可用性。"""
    from packages.rag.indexing import ensure_parse_sources

    readable = fetch(
        c,
        "SELECT p.coverage<>'failed' AND EXISTS(SELECT 1 FROM evidence_blocks e WHERE e.bid_id=p.bid_id AND e.parse_id=p.id AND length(btrim(e.text_content))>0) AS value FROM parse_runs p WHERE p.bid_id=%s AND p.id=%s",
        (bid_id, parse_id),
    )["value"]
    if not readable:
        return {"original_source_id": None, "parse_source_id": None, "readable": False}
    try:
        root, source = ensure_parse_sources(c, bid_id, parse_id, publish_readable=readable)
    except ValueError:
        raise DomainError(
            "PARSE_SOURCE_AUTHORITY_CHANGED",
            "原件或解析来源已撤回或身份变化，未重新发布；请核对来源效力",
            409,
        ) from None
    return {"original_source_id": str(root), "parse_source_id": str(source), "readable": readable}
