"""Build freeze/compute/publish ports for the fenced export Worker."""

from __future__ import annotations

import os
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from packages.authoring.build import (
    OPTIONS,
    TEMPLATE_DIGEST,
    WRITER_DIGEST,
    assemble_docx,
    render_identity,
    render_pdf,
)
from packages.authoring.repository import assert_sources, lock_draft, snapshot_content
from packages.domain import storage
from packages.domain.runtime import DomainError, audit, clean, fetch, sha, uid

DEFAULT_TEMPLATE_ID = uuid5(NAMESPACE_URL, "tender-workbench/authoring/layout/" + TEMPLATE_DIGEST)


def ensure_template(c):
    c.execute(
        """INSERT INTO template_versions(id,name,kind,schema_digest,config,fontset_digest)
      VALUES(%s,'内置A4正文','layout',%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            DEFAULT_TEMPLATE_ID,
            TEMPLATE_DIGEST,
            Jsonb(OPTIONS),
            sha({"font": OPTIONS["font"], "availability": "frozen_per_build"}),
        ),
    )


def configured_render_identity(formats):
    if "pdf" not in formats:
        return None
    try:
        return render_identity(
            os.environ.get("AUTHORING_RENDERER", "soffice"),
            os.environ.get(
                "AUTHORING_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
            ),
        )
    except DomainError:
        return None


def freeze_build(c, bid, body, actor, renderer_identity=None):
    draft = lock_draft(c, bid, UUID(body["draft_id"]))
    if draft["revision"] != body["draft_revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "生成前请等待全部保存成功并核对版本", 412)
    if UUID(body["template_version_id"]) != DEFAULT_TEMPLATE_ID:
        raise DomainError("BUILD_TEMPLATE_UNSUPPORTED", "当前仅支持内置A4正文模板", 422)
    ensure_template(c)
    snap = fetch(
        c,
        "SELECT id FROM draft_snapshots WHERE draft_id=%s AND revision=%s",
        (draft["id"], draft["revision"]),
    )
    snapshot = snapshot_content(c, bid, snap["id"])
    for section in snapshot["sections"]:
        assert_sources(c, bid, section)
    manifest = {
        "snapshot_digest": snapshot["snapshot_digest"],
        "writer_digest": WRITER_DIGEST,
        "formats": body["formats"],
        "follow_up": body["follow_up"],
        "template_version_id": str(DEFAULT_TEMPLATE_ID),
        "renderer_identity": renderer_identity,
        "created_by": str(actor),
        "render_options": OPTIONS,
        "artifact_kind": "bid_body",
    }
    identity_id = uid()
    c.execute(
        "INSERT INTO build_inputs(id,bid_id,draft_snapshot_id,template_digest,render_digest,manifest) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identity_id,
            bid,
            snap["id"],
            TEMPLATE_DIGEST,
            sha({"renderer": renderer_identity, "options": OPTIONS}),
            Jsonb(manifest),
        ),
    )
    return identity_id, manifest


def prepare_build(c, bid, input_id):
    """Read-only preparation; close this transaction before rendering/object I/O."""
    info = fetch(c, "SELECT * FROM build_inputs WHERE bid_id=%s AND id=%s", (bid, input_id))
    snapshot = snapshot_content(c, bid, info["draft_snapshot_id"])
    if info["manifest"]["snapshot_digest"] != snapshot["snapshot_digest"]:
        raise DomainError("BUILD_INPUT_CORRUPT", "生成输入校验失败", 409)
    return {"input_id": str(input_id), "info": clean(info), "snapshot": snapshot}


def compute_prepared_build(prepared):
    """No DB connection, row lock or API credential is needed by this computation."""
    input_id, info, snapshot = prepared["input_id"], prepared["info"], prepared["snapshot"]
    if info["manifest"]["writer_digest"] != WRITER_DIGEST:
        raise DomainError("BUILD_WRITER_CHANGED", "正文生成器已更新，请创建新的构建", 409)
    docx = assemble_docx(snapshot)
    placeholder_gaps = (
        ["PLACEHOLDER_CONTENT_PRESENT"]
        if re.search(r"\{\{[^}]*\}\}|待补充|待填写|\bTODO\b", str(snapshot["sections"]))
        else []
    )
    result = {
        "input_id": str(input_id),
        "snapshot_id": str(info["draft_snapshot_id"]),
        "snapshot_digest": snapshot["snapshot_digest"],
        "manifest": info["manifest"],
        "outputs": [],
    }
    # PDF always derives from this exact DOCX, even when only PDF was requested.
    formats = list(dict.fromkeys(["docx"] + info["manifest"]["formats"]))
    rendered = None
    if "pdf" in formats:
        if info["manifest"]["renderer_identity"] is None:
            from packages.authoring.build import RenderResult

            rendered = RenderResult(
                "failed", None, {"source_docx_digest": sha(docx)}, ("RENDERER_UNAVAILABLE",)
            )
        else:
            rendered = render_pdf(
                docx,
                executable=os.environ.get("AUTHORING_RENDERER", "soffice"),
                font_path=os.environ.get(
                    "AUTHORING_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
                ),
                expected_identity=info["manifest"]["renderer_identity"],
                sandbox_attested=False,
            )
    result["conversion_chain"] = rendered.chain if rendered else {"source_docx_digest": sha(docx)}
    for fmt in formats:
        data: bytes | None
        if fmt == "docx":
            data, gaps = docx, list(placeholder_gaps)
        else:
            if rendered is None:
                raise DomainError("BUILD_FORMAT_INVALID", "生成格式未准备", 409)
            data, gaps = rendered.pdf, list(rendered.gaps) + placeholder_gaps
        if data is None:
            result["outputs"].append({"format": fmt, "state": "failed", "gaps": gaps})
            continue
        media = (
            "application/pdf"
            if fmt == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        digest = sha(data)
        key = "blobs/" + digest
        storage.put(key, data, media)
        if sha(storage.get(key)) != digest:
            raise DomainError("BUILD_OBJECT_CORRUPT", "生成产物存储校验失败", 503)
        result["outputs"].append(
            {
                "format": fmt,
                "state": "succeeded",
                "gaps": gaps,
                "object_key": key,
                "digest": digest,
                "byte_size": len(data),
                "media_type": media,
            }
        )
    return result


def publish_build(c, bid, build_id, computed, actor=None):
    """Call inside engine.finish fencing transaction; never move current body/review heads."""
    build = fetch(c, "SELECT * FROM builds WHERE id=%s AND bid_id=%s", (build_id, bid))
    if str(build["input_id"]) != computed["input_id"]:
        raise DomainError("BUILD_RESULT_IDENTITY_MISMATCH", "生成结果身份不匹配", 409)
    info = fetch(
        c, "SELECT * FROM build_inputs WHERE id=%s AND bid_id=%s", (build["input_id"], bid)
    )
    if info["manifest"] != computed["manifest"]:
        raise DomainError("BUILD_RESULT_IDENTITY_MISMATCH", "生成清单身份不匹配", 409)
    frozen_actor = UUID(info["manifest"]["created_by"])
    if actor is not None and UUID(str(actor)) != frozen_actor:
        raise DomainError("BUILD_ACTOR_IDENTITY_MISMATCH", "生成发起人身份不匹配", 409)
    actor = frozen_actor
    snapshot = snapshot_content(c, bid, info["draft_snapshot_id"])
    for section in snapshot["sections"]:
        assert_sources(c, bid, section)
    versions = {}
    for output in computed["outputs"]:
        version = None
        if output["state"] == "succeeded":
            blob = uid()
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
                (
                    blob,
                    output["digest"],
                    output["object_key"],
                    output["byte_size"],
                    output["media_type"],
                ),
            )
            blob_row = fetch(c, "SELECT * FROM blobs WHERE digest=%s", (output["digest"],))
            if (
                blob_row["byte_size"] != output["byte_size"]
                or blob_row["media_type"] != output["media_type"]
            ):
                raise DomainError("BUILD_BLOB_IDENTITY_MISMATCH", "文件身份冲突", 409)
            version = uid()
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),'bid_body',%s)",
                (version, bid, blob_row["id"], "投标正文." + output["format"], actor),
            )
            logical_file = uid()
            c.execute(
                "INSERT INTO document_files(id,bid_id,role,revision) VALUES(%s,%s,'body',1)",
                (logical_file, bid),
            )
            c.execute(
                "INSERT INTO document_file_versions(bid_id,file_id,version_id,revision,previous_version_id) VALUES(%s,%s,%s,1,NULL)",
                (bid, logical_file, version),
            )
            c.execute(
                "UPDATE document_files SET head_version_id=%s,revision=2 WHERE id=%s",
                (version, logical_file),
            )
            versions[output["format"]] = version
        c.execute(
            "INSERT INTO build_outputs(id,bid_id,build_id,format,document_version_id,state,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                uid(),
                bid,
                build_id,
                output["format"],
                version,
                output["state"],
                Jsonb(output["gaps"]),
            ),
        )
    if "pdf" in versions and "docx" in versions:
        c.execute(
            "INSERT INTO document_relations(bid_id,document_version_id,source_document_version_id,kind,verification,evidence_ids) VALUES(%s,%s,%s,'controlled_render','verified',%s)",
            (bid, versions["pdf"], versions["docx"], []),
        )
    audit(
        c,
        actor,
        bid,
        "build.outputs_published",
        {
            "build_id": build_id,
            "snapshot_id": info["draft_snapshot_id"],
            "conversion_chain": computed["conversion_chain"],
            "outputs": versions,
        },
    )
    return clean(
        {
            "build_id": build_id,
            "outputs": versions,
            "conversion_chain": computed["conversion_chain"],
            "follow_up": computed["manifest"]["follow_up"],
            "snapshot_id": computed["snapshot_id"],
        }
    )
