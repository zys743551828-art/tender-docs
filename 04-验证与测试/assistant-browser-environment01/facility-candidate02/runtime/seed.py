"""One new clearly synthetic project and actual PDFium-read source, API LOGIN only."""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4


def seed(value):
    import pypdfium2

    from packages.domain import storage
    from packages.domain.runtime import connection, sha
    from packages.identity.repository import record_binding, register_bidder
    from packages.rag.indexing import ensure_parse_sources

    output = Path(value["evidence"]) / "synthetic-source.json"
    if output.exists():
        raise RuntimeError("Seed is one-shot; retained existing source evidence")
    path = Path(
        "/workspace/tender-docs/04-验证与测试/authoring-development-checks/candidate03-evidence/synthetic-body.pdf"
    )
    original = path.read_bytes()
    pdf = pypdfium2.PdfDocument(original)
    if len(pdf) != 1:
        raise RuntimeError("Known one-page synthetic source required")
    page = pdf[0]
    textpage = page.get_textpage()
    text = textpage.get_text_bounded()
    width, height = page.get_size()
    textpage.close()
    page.close()
    pdf.close()
    if not text.strip():
        raise RuntimeError("Synthetic PDF has no actual readable text")
    digest = sha(original)
    key = "assistant-fixture/blobs/" + digest
    storage.put(key, original, "application/pdf")
    actor = UUID(value["member_ids"][0])
    bid, project, round_id, blob, version, parse, evidence = [uuid4() for _ in range(7)]
    with connection() as c:
        members = c.execute(
            "SELECT id FROM members WHERE id=ANY(%s) AND active",
            ([UUID(item) for item in value["member_ids"]],),
        ).fetchall()
        if len(members) != 2:
            raise RuntimeError("Dedicated browser members are not active")
        bidder = UUID(
            register_bidder(
                c, actor, "显式合成浏览器测试企业", "仅为夹具登记，不授资质或业绩资格", None
            )["id"]
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'显式合成助手浏览器项目','{}',%s)",
            (bid, actor),
        )
        c.execute(
            "INSERT INTO engineering_projects(id,title,region,engineering_type) VALUES(%s,'显式合成助手浏览器项目','浙江','市政道路')",
            (project,),
        )
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
        record_binding(
            c, bid, actor, None, reason="显式合成夹具身份登记，不是真实业务核验", confirmed=True
        )
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",
            (blob, digest, key, len(original)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'显式合成助手原文.pdf',clock_timestamp(),'source_original',%s)",
            (version, bid, blob, actor),
        )
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
            (parse, bid, version, sha("explicit-fixture-actual-PDFium"), sha({})),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,%s,%s,0,'usable','[]')",
            (bid, parse, width, height),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
            (evidence, bid, parse, text, sha(text)),
        )
        root, source = ensure_parse_sources(c, bid, parse, publish_readable=True)
    result = {
        "project_id": str(bid),
        "project_title": "显式合成助手浏览器项目",
        "document_version_id": str(version),
        "document_name": "显式合成助手原文.pdf",
        "parse_id": str(parse),
        "original_source_id": str(root),
        "parse_source_id": str(source),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "actual_pdfium_text_sha256": sha(text),
        "fixture_parser_records": True,
        "native_upload_kafka_accepted": False,
        "real_provider_attempts": 0,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print({"seeded": True, "project_title": result["project_title"], "real_provider_attempts": 0})
