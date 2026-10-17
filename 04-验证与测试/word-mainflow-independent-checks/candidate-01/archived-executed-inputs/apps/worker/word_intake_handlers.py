"""Isolated Word conversion compute and fenced ordinary Worker publication hooks."""

from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.runtime import DomainError, audit, connection, fetch, sha, uid
from packages.word_intake.inspection import DOCX, PDF
from packages.word_intake.renderer import render
from packages.word_intake.repository import controlled_qualified, current_word_adoption


def compute(message):
    if message["step_key"] != "word-render" or message["handler_version"] != "word-render/1":
        raise DomainError("WORD_HANDLER_INVALID", "Word转换阶段身份不匹配", 409)
    with connection(True) as c:
        run = fetch(
            c,
            "SELECT r.*,b.digest,b.object_key,b.media_type,u.role FROM word_render_runs r JOIN document_versions v ON v.id=r.source_document_version_id AND v.bid_id=r.bid_id JOIN blobs b ON b.id=v.blob_id JOIN word_intake_receipts w ON w.document_version_id=v.id JOIN uploads u ON u.id=w.upload_id WHERE r.operation_id=%s AND r.bid_id=%s",
            (message["operation_id"], message["bid_id"]),
        )
    expected = sha(
        {
            "id": run["source_document_version_id"],
            "digest": run["digest"],
            "render_profile_digest": run["profile_digest"],
        }
    )
    if (
        run["media_type"] != DOCX
        or str(run["source_document_version_id"]) != str(message["payload_ref"])
        or expected != message["input_digest"]
    ):
        raise DomainError("WORD_INPUT_IDENTITY_CHANGED", "Word原件或转换方法身份不匹配", 409)
    source = storage.get(run["object_key"])
    if sha(source) != run["digest"]:
        raise DomainError("BLOB_CORRUPTED", "Word原件摘要不一致", 409)
    outcome = render(source, run["frozen_profile"])
    pdf = outcome.pop("pdf")
    key = None
    if outcome["state"] == "succeeded":
        if not pdf or sha(pdf) != outcome["chain"].get("pdf_digest"):
            raise DomainError("WORD_PDF_DIGEST_INVALID", "实际转换PDF摘要不匹配", 409)
        key = "word-rendered/" + sha(pdf)
        storage.put(key, pdf, PDF)
    return {
        "run": run,
        "outcome": outcome,
        "pdf_object_key": key,
        "pdf_byte_size": len(pdf) if pdf else None,
    }


def publish(c, message, computed):
    run = fetch(
        c,
        "SELECT * FROM word_render_runs WHERE operation_id=%s AND bid_id=%s FOR UPDATE",
        (message["operation_id"], message["bid_id"]),
    )
    if run["outcome"] is not None:
        return {
            "result_ref": f"/api/v1/bids/{run['bid_id']}/word-originals/{run['source_document_version_id']}",
            "pdf_document_version_id": str(run["pdf_document_version_id"])
            if run["pdf_document_version_id"]
            else None,
        }
    result = computed["outcome"]
    if result["chain"]["profile_digest"] != run["profile_digest"]:
        raise DomainError("WORD_RENDER_PROFILE_CHANGED", "转换结果与冻结配置不一致", 409)
    original = fetch(
        c,
        "SELECT v.*,b.digest,u.role,x.created_by AS actor_id FROM document_versions v JOIN blobs b ON b.id=v.blob_id JOIN word_intake_receipts w ON w.document_version_id=v.id JOIN uploads u ON u.id=w.upload_id JOIN upload_batches x ON x.id=u.batch_id WHERE v.id=%s AND v.bid_id=%s",
        (run["source_document_version_id"], run["bid_id"]),
    )
    if result["chain"]["source_docx_digest"] != original["digest"]:
        raise DomainError("WORD_INPUT_IDENTITY_CHANGED", "转换原件身份不匹配", 409)
    pdf_version = None
    qualified = controlled_qualified(result)
    if result["state"] == "succeeded":
        blob_id = uid()
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
            (
                blob_id,
                result["chain"]["pdf_digest"],
                computed["pdf_object_key"],
                computed["pdf_byte_size"],
                PDF,
            ),
        )
        blob = fetch(
            c,
            "SELECT id,media_type,byte_size FROM blobs WHERE digest=%s",
            (result["chain"]["pdf_digest"],),
        )
        if blob["media_type"] != PDF or blob["byte_size"] != computed["pdf_byte_size"]:
            raise DomainError("WORD_PDF_BLOB_IDENTITY_INVALID", "转换PDF存储身份不一致", 409)
        pdf_version, file_id = uid(), uid()
        name = original["original_name"].rsplit(".", 1)[0] + ".pdf"
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),%s,%s)",
            (
                pdf_version,
                run["bid_id"],
                blob["id"],
                name,
                "bid_body" if original["role"] == "body" else "source_original",
                original["actor_id"],
            ),
        )
        c.execute(
            "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,%s,1)",
            (file_id, run["bid_id"], original["role"]),
        )
        c.execute(
            "INSERT INTO document_file_versions(bid_id,file_id,version_id,revision,previous_version_id) VALUES(%s,%s,%s,1,NULL)",
            (run["bid_id"], file_id, pdf_version),
        )
        c.execute(
            "UPDATE document_files SET head_version_id=%s,revision=2 WHERE id=%s",
            (pdf_version, file_id),
        )
        if qualified:
            c.execute(
                "INSERT INTO document_relations(bid_id,document_version_id,source_document_version_id,kind,verification,evidence_ids) VALUES(%s,%s,%s,'controlled_render','verified','{}'::uuid[])",
                (run["bid_id"], pdf_version, run["source_document_version_id"]),
            )
    c.execute(
        "UPDATE word_render_runs SET pdf_document_version_id=%s,outcome=%s,finished_at=clock_timestamp() WHERE operation_id=%s AND outcome IS NULL",
        (pdf_version, Jsonb(result), run["operation_id"]),
    )
    audit(
        c,
        original["actor_id"],
        run["bid_id"],
        "word.conversion_recorded",
        {
            "source_document_version_id": run["source_document_version_id"],
            "pdf_document_version_id": pdf_version,
            "conversion_state": result["state"],
            "coverage": result["coverage"],
            "controlled_render_verified": qualified,
            "profile_digest": run["profile_digest"],
        },
        message.get("command_id"),
    )
    return {
        "result_ref": f"/api/v1/bids/{run['bid_id']}/word-originals/{run['source_document_version_id']}",
        "source_document_version_id": str(run["source_document_version_id"]),
        "pdf_document_version_id": str(pdf_version) if pdf_version else None,
        "controlled_render_verified": qualified,
        "conversion_state": result["state"],
        "conversion_coverage": result["coverage"],
        "conversion_gaps": result["gaps"],
        "word_render_adoption": current_word_adoption(
            c, run["bid_id"], run["source_document_version_id"]
        )
        if qualified
        else None,
    }
