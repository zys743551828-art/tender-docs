"""Build freeze/compute/publish ports for the fenced export Worker."""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from packages.authoring.build import (
    OPTIONS,
    TEMPLATE_DIGEST,
    WRITER_DIGEST,
    assemble_docx,
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
    from packages.word_intake.renderer import configured_profile

    try:
        profile = configured_profile()
        return profile if profile["identity"].get("enabled", True) else None
    except DomainError:
        return None


def freeze_build(c, bid, body, actor, renderer_identity=None):
    draft = lock_draft(c, bid, UUID(body["draft_id"]))
    if draft["revision"] != body["draft_revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "生成前请等待全部保存成功并核对版本", 412)
    structured = "style_template_version_id" in body
    if not structured and UUID(body["template_version_id"]) != DEFAULT_TEMPLATE_ID:
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
    from packages.authoring import snapshot_structure, structured_docx

    extension = snapshot.get("authoring_structure")
    if extension is not None and not structured:
        raise DomainError(
            "BUILD_TWO_TEMPLATES_REQUIRED", "当前草稿已使用目录与版式，请明确选择两个冻结版本", 422
        )
    if structured:
        if extension is None or any(
            body[key] != extension[name]["id"]
            for key, name in [
                ("directory_template_version_id", "directory_template"),
                ("style_template_version_id", "style_template"),
            ]
        ):
            raise DomainError(
                "BUILD_TEMPLATE_SNAPSHOT_MISMATCH", "所选双模板与该草稿修订快照不一致", 409
            )
        authority = snapshot_structure.frozen_authority(c, bid, extension, snapshot["sections"])
    heads = fetch(
        c,
        """SELECT b.revision AS bid_revision,b.material_revision,b.settings_revision,
           p.current_material_id,p.current_body_id,p.current_draft_id,p.current_rule_set_id,
           f.fact_set_id,coalesce(f.revision,0) AS fact_revision
           FROM bids b JOIN bid_profiles p ON p.bid_id=b.id
           LEFT JOIN fact_heads f ON f.bid_id=b.id WHERE b.id=%s""",
        (bid,),
    )
    manifest = {
        "snapshot_digest": snapshot["snapshot_digest"],
        "writer_digest": sha({"base": WRITER_DIGEST, "structured": structured_docx.WRITER_DIGEST})
        if structured
        else WRITER_DIGEST,
        "writer_kind": "structured_text/1" if structured else "strict_text/1",
        "render_protocol": "word-docker-render/1",
        "authoring_structure_digest": extension["digest"] if extension else None,
        "authoring_authority": authority if structured else None,
        "directory_template_version_id": extension["directory_template"]["id"]
        if extension
        else None,
        "style_template_version_id": extension["style_template"]["id"] if extension else None,
        "actual_page_ranges": None,
        "formats": body["formats"],
        "follow_up": body["follow_up"],
        "template_version_id": extension["style_template"]["id"]
        if extension
        else str(DEFAULT_TEMPLATE_ID),
        "renderer_identity": renderer_identity,
        "created_by": str(actor),
        "render_options": OPTIONS,
        "artifact_kind": "bid_body",
        "follow_up_context": clean(
            dict(heads)
            | {
                "enabled": body["follow_up"] == "review",
                "draft_id": str(draft["id"]),
                "draft_revision": draft["revision"],
                "draft_snapshot_id": str(snap["id"]),
                "draft_snapshot_digest": snapshot["snapshot_digest"],
            }
        ),
    }
    identity_id = uid()
    c.execute(
        "INSERT INTO build_inputs(id,bid_id,draft_snapshot_id,template_digest,render_digest,manifest) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identity_id,
            bid,
            snap["id"],
            sha(
                {
                    "directory": extension["directory_template"],
                    "style": extension["style_template"],
                    "metadata": extension["metadata"],
                }
            )
            if extension
            else TEMPLATE_DIGEST,
            sha(
                {
                    "renderer": renderer_identity,
                    "options": extension["metadata"].get(
                        "effective_style", extension["style_template"]["config"]
                    )
                    if extension
                    else OPTIONS,
                }
            ),
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
    from packages.authoring import structured_docx

    structured = info["manifest"].get("writer_kind") == "structured_text/1"
    expected_writer = (
        sha({"base": WRITER_DIGEST, "structured": structured_docx.WRITER_DIGEST})
        if structured
        else WRITER_DIGEST
    )
    if info["manifest"]["writer_digest"] != expected_writer:
        raise DomainError("BUILD_WRITER_CHANGED", "正文生成器已更新，请创建新的构建", 409)
    if (
        structured
        and info["manifest"]["authoring_structure_digest"]
        != snapshot["authoring_structure"]["digest"]
    ):
        raise DomainError("BUILD_INPUT_CORRUPT", "双模板正文结构摘要不匹配", 409)
    docx = structured_docx.assemble(snapshot) if structured else assemble_docx(snapshot)
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
        elif info["manifest"].get("render_protocol") != "word-docker-render/1" or not isinstance(
            info["manifest"]["renderer_identity"].get("identity"), dict
        ):
            from packages.authoring.build import RenderResult

            rendered = RenderResult(
                "failed", None, {"source_docx_digest": sha(docx)}, ("RENDER_PROTOCOL_UNSUPPORTED",)
            )
        else:
            from packages.authoring.build import RenderResult
            from packages.word_intake.renderer import render

            value = render(docx, info["manifest"]["renderer_identity"])
            rendered = RenderResult(
                value["state"], value["pdf"], value["chain"], tuple(value["gaps"])
            )
            result["render_coverage"] = value["coverage"]
            result["render_isolation"] = value["isolation"]
    if structured:
        config = snapshot["authoring_structure"]["metadata"].get(
            "effective_style", snapshot["authoring_structure"]["style_template"]["config"]
        )
        if config["page_number"]:
            placeholder_gaps.append("PAGE_NUMBER_FIELD_UNSUPPORTED")
        placeholder_gaps.append("ACTUAL_SECTION_PAGE_RANGES_UNKNOWN")

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
    from packages.domain.runtime import require_read_committed
    from packages.lifecycle.service import assert_work_writable

    require_read_committed(c)
    assert_work_writable(c, bid, worker=True)
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
    if info["manifest"].get("writer_kind") == "structured_text/1":
        from packages.authoring.snapshot_structure import frozen_authority

        if (
            frozen_authority(c, bid, snapshot["authoring_structure"], snapshot["sections"])
            != info["manifest"]["authoring_authority"]
        ):
            raise DomainError(
                "AUTHORING_AUTHORITY_CHANGED",
                "冻结正文/计划/格式依据效力已变化，保留构建历史但未发布",
                409,
            )
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
            "INSERT INTO document_relations(bid_id,document_version_id,source_document_version_id,kind,verification,evidence_ids) VALUES(%s,%s,%s,'controlled_render',%s,%s)",
            (
                bid,
                versions["pdf"],
                versions["docx"],
                "verified" if computed.get("render_coverage") == "complete" else "unverified",
                [],
            ),
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
