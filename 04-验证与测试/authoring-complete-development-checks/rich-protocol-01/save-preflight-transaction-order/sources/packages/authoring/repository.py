"""Append-only drafts, CAS saves, source authority and frozen build inputs."""

from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring.content import (
    SCHEMA_DIGEST,
    empty_content,
    has_rich,
    schema_digest_for,
    validate_content,
    validate_for_schema,
)
from packages.domain.runtime import (
    DomainError,
    clean,
    etag,
    fetch,
    match,
    require_read_committed,
    sha,
    uid,
)
from packages.lifecycle.service import assert_work_writable
from packages.rag.indexing import original_source_id, parse_source_id


def lock_draft(c, bid, draft):
    require_read_committed(c)
    assert_work_writable(c, bid)
    return fetch(c, "SELECT * FROM drafts WHERE bid_id=%s AND id=%s FOR UPDATE", (bid, draft))


def assert_sources(c, bid, content, target=None):
    """Final-use check; indexed publication flags never grant authority."""
    validate_content(content)
    attrs = content["attrs"]
    rule_ids = list(dict.fromkeys(attrs["rule_ids"]))
    rules = c.execute(
        """SELECT r.id FROM rule_items r JOIN bid_profiles p ON p.bid_id=r.bid_id
           WHERE r.bid_id=%s AND r.id=ANY(%s) AND r.rule_set_id=p.current_rule_set_id
           AND r.provenance_status='effective'""",
        (bid, rule_ids),
    ).fetchall()
    if len(rules) != len(rule_ids):
        raise DomainError("AUTHORING_RULE_STALE", "所选要求已失效，请重新核对", 409)
    refs = attrs["source_refs"]
    source_ids = sorted({UUID(ref["source_id"]) for ref in refs}, key=str)
    # Same graph mutex as lineage and source mutation; then deterministic ancestor locks.
    if source_ids:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            """SELECT h.source_id FROM retrieval.source_heads h
           WHERE h.source_id IN (SELECT ancestor_id FROM retrieval.source_ancestors WHERE source_id=ANY(%s))
           ORDER BY h.source_id FOR UPDATE""",
            (source_ids,),
        ).fetchall()
    for ref in refs:
        binding = c.execute(
            """SELECT p.id FROM parse_runs p
               JOIN evidence_blocks e ON e.parse_id=p.id AND e.bid_id=p.bid_id
               JOIN pages page ON page.parse_id=e.parse_id AND page.bid_id=e.bid_id
                 AND page.physical_page=e.physical_page
               WHERE p.bid_id=%s AND p.document_version_id=%s AND e.id=%s
                 AND p.coverage<>'failed'
                 AND p.id=(SELECT latest.id FROM parse_runs latest
                   WHERE latest.document_version_id=p.document_version_id
                   ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)""",
            (bid, UUID(ref["document_version_id"]), UUID(ref["evidence_id"])),
        ).fetchone()
        if binding is None or UUID(ref["source_id"]) != parse_source_id(binding["id"]):
            raise DomainError(
                "AUTHORING_SOURCE_STALE",
                "引用来源与当前解析证据不对应，请重新查证",
                409,
            )
        row = c.execute(
            """SELECT n.id,h.revision,e.id AS evidence_id FROM retrieval.source_nodes n
               JOIN retrieval.source_heads h ON h.source_id=n.id
               JOIN parse_runs p ON p.document_version_id=n.document_version_id AND p.bid_id=%s
               JOIN evidence_blocks e ON e.parse_id=p.id AND e.bid_id=p.bid_id
               JOIN bid_profiles bp ON bp.bid_id=p.bid_id
               JOIN document_versions v ON v.id=p.document_version_id AND v.bid_id=p.bid_id
               JOIN blobs blob ON blob.id=v.blob_id AND blob.digest=n.digest
               WHERE n.id=%s AND h.revision=%s AND n.kind='document'
                 AND n.document_version_id=%s AND e.id=%s
                 AND p.id=%s AND EXISTS(SELECT 1 FROM retrieval.lineage_edges root_edge
                   JOIN retrieval.source_nodes root ON root.id=root_edge.parent_id
                   WHERE root_edge.child_id=n.id AND root.id=%s
                     AND root.kind='document' AND root.document_version_id=v.id
                     AND root.digest=blob.digest)
                 AND p.id=(SELECT latest.id FROM parse_runs latest WHERE latest.document_version_id=p.document_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
                 AND (n.document_version_id=bp.current_body_id OR EXISTS(
                   SELECT 1 FROM material_assignments ma WHERE ma.bid_id=bp.bid_id AND ma.snapshot_id=bp.current_material_id
                     AND ma.document_version_id=n.document_version_id AND ma.role IN ('tender','supplement','drawing','boq','body','other')))
                 AND NOT EXISTS(SELECT 1 FROM retrieval.source_ancestors a
                   JOIN retrieval.source_nodes ancestor ON ancestor.id=a.ancestor_id
                   LEFT JOIN retrieval.source_heads ah ON ah.source_id=ancestor.id
                   WHERE a.source_id=n.id AND (ancestor.policy_class<>'prebid_content'
                     OR ancestor.kind<>'document'
                     OR NOT EXISTS(SELECT 1 FROM document_versions av WHERE av.id=ancestor.document_version_id AND av.bid_id=bp.bid_id
                       AND (av.id=bp.current_body_id OR EXISTS(SELECT 1 FROM material_assignments am
                         WHERE am.bid_id=bp.bid_id AND am.snapshot_id=bp.current_material_id AND am.document_version_id=av.id
                         AND am.role IN ('tender','supplement','drawing','boq','body','other'))))
                     OR ancestor.known_at>now() OR ah.status IS DISTINCT FROM 'published'
                     OR ah.valid_from>now() OR ah.valid_until<=now()))""",
            (
                bid,
                UUID(ref["source_id"]),
                ref["source_revision"],
                UUID(ref["document_version_id"]),
                UUID(ref["evidence_id"]),
                binding["id"],
                original_source_id(UUID(ref["document_version_id"])),
            ),
        ).fetchone()
        if row is None:
            raise DomainError(
                "AUTHORING_SOURCE_STALE",
                "引用来源已失效、用途不符或不属于当前项目，请重新查证",
                409,
            )
        if target is not None:
            c.execute(
                """INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id)
               VALUES(%s,%s,%s,%s,'authoring',%s,%s)""",
                (
                    uid(),
                    bid,
                    UUID(ref["source_id"]),
                    ref["source_revision"],
                    UUID(ref["evidence_id"]),
                    target,
                ),
            )


def latest_sections(c, bid, draft, *, structure_state=None, include_deleted=False):
    rows = c.execute(
        """SELECT r.* FROM sections s JOIN section_revisions r
       ON r.section_id=s.id AND r.revision=s.head_revision WHERE s.bid_id=%s AND s.draft_id=%s
       ORDER BY (r.content->'attrs'->>'ordinal')::int,s.id""",
        (bid, draft),
    ).fetchall()
    if include_deleted:
        return rows
    from packages.authoring.snapshot_structure import active_sections

    return active_sections(c, bid, draft, rows, structure_state)


def freeze(c, bid, draft, revision, title=None, *, actor=None, structure_state=None):
    from packages.authoring.snapshot_structure import persist, prepare

    sections = latest_sections(c, bid, draft, structure_state=structure_state)
    extension = prepare(c, bid, draft, sections, actor, structure_state)
    ordinals = [s["content"]["attrs"]["ordinal"] for s in sections]
    if len(set(ordinals)) != len(ordinals):
        raise DomainError("SECTION_ORDER_CONFLICT", "章节顺序重复，请重新排列", 409)
    snapshot = uid()
    manifest = {
        "draft_id": str(draft),
        "revision": revision,
        "sections": [
            {
                "id": str(s["section_id"]),
                "revision_id": str(s["id"]),
                "revision": s["revision"],
                "digest": s["digest"],
                "ordinal": s["content"]["attrs"]["ordinal"],
            }
            for s in sections
        ],
    }
    if title is None:
        previous = c.execute(
            "SELECT manifest FROM draft_snapshots WHERE draft_id=%s ORDER BY revision DESC LIMIT 1",
            (draft,),
        ).fetchone()
        title = previous["manifest"].get("title", "投标正文") if previous else "投标正文"
    manifest["title"] = title
    if extension is not None:
        manifest["authoring_structure"] = extension
    if any(has_rich(s["content"]) or s["schema_digest"] != SCHEMA_DIGEST for s in sections):
        from packages.authoring.rich_content import validate_content as validate_rich
        from packages.authoring.rich_snapshots import prepare as prepare_images
        from packages.authoring.templates import DEFAULT_STYLE

        style: dict = (
            extension["metadata"].get("effective_style", extension["style_template"]["config"])
            if extension is not None
            else DEFAULT_STYLE
        )
        for section in sections:
            if has_rich(section["content"]):
                validate_rich(
                    section["content"],
                    available_width_twips=11906
                    - style["margin_left_twips"]
                    - style["margin_right_twips"],
                    available_height_twips=16838
                    - style["margin_top_twips"]
                    - style["margin_bottom_twips"],
                )

        manifest["authoring_images"] = prepare_images(c, bid, [s["content"] for s in sections])
    digest = sha(manifest)
    c.execute(
        "INSERT INTO draft_snapshots(id,bid_id,draft_id,revision,manifest,digest) VALUES(%s,%s,%s,%s,%s,%s)",
        (snapshot, bid, draft, revision, Jsonb(manifest), digest),
    )
    for i, section in enumerate(sections):
        c.execute(
            "INSERT INTO draft_snapshot_sections(snapshot_id,bid_id,draft_id,section_revision_id,ordinal) VALUES(%s,%s,%s,%s,%s)",
            (snapshot, bid, draft, section["id"], i),
        )
    persist(c, bid, draft, revision, snapshot, extension, actor)
    return {
        "draft_id": str(draft),
        "draft_revision": revision,
        "snapshot_id": str(snapshot),
        "snapshot_digest": digest,
    }


def append_section(c, bid, draft, section, revision, content, actor, *, schema_digest=None):
    schema_digest = schema_digest or schema_digest_for(content)
    validate_for_schema(content, schema_digest)
    if schema_digest != SCHEMA_DIGEST:
        from packages.authoring.rich_assets import require_available

        require_available(c)
    assert_sources(c, bid, content)
    identity = uid()
    c.execute(
        """INSERT INTO section_revisions(id,bid_id,draft_id,section_id,revision,schema_digest,content,digest,created_by)
       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            identity,
            bid,
            draft,
            section,
            revision,
            schema_digest,
            Jsonb(content),
            sha(content),
            actor,
        ),
    )
    c.execute("UPDATE sections SET head_revision=%s WHERE id=%s", (revision, section))
    assert_sources(c, bid, content, identity)


def create_draft(c, bid, actor, title, outline=None):
    require_read_committed(c)
    assert_work_writable(c, bid)
    from packages.authoring.snapshot_structure import available
    from packages.authoring.templates import ensure_defaults

    if available(c):
        ensure_defaults(c, actor)
        if outline:
            config = fetch(
                c, "SELECT config FROM template_versions WHERE id=%s AND kind='outline'", (outline,)
            )["config"]
            if "nodes" in config:
                from packages.authoring.structure_repository import create_from_directory

                return create_from_directory(c, bid, actor, title, outline)
    draft = uid()
    c.execute("INSERT INTO drafts(id,bid_id,revision) VALUES(%s,%s,1)", (draft, bid))
    titles = ["技术方案"]
    if outline:
        template = fetch(
            c, "SELECT config FROM template_versions WHERE id=%s AND kind='outline'", (outline,)
        )
        titles = template["config"].get("sections", [])
        if not titles or len(titles) > 100 or any(not isinstance(t, str) for t in titles):
            raise DomainError("OUTLINE_INVALID", "目录模板结构不受支持", 422)
    for ordinal, name in enumerate(titles):
        section = uid()
        c.execute(
            "INSERT INTO sections(id,bid_id,draft_id,head_revision) VALUES(%s,%s,%s,1)",
            (section, bid, draft),
        )
        append_section(c, bid, draft, section, 1, empty_content(name, ordinal), actor)
    saved = freeze(c, bid, draft, 1, title, actor=actor)
    c.execute("UPDATE bid_profiles SET current_draft_id=%s WHERE bid_id=%s", (draft, bid))
    return draft, saved


def save_draft(c, bid, draft, actor, body, condition):
    from packages.authoring.snapshot_structure import assert_active, latest

    row = lock_draft(c, bid, draft)
    match(condition, "draft", draft, row["revision"])
    if body["base_draft_revision"] != row["revision"]:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草稿已被更新，请保留本地改动并核对", 412)
    ids = [change["section_id"] for change in body["changes"]]
    if len(ids) != len(set(ids)):
        raise DomainError("SECTION_DUPLICATE", "同一章节不能重复提交", 422)
    expected_ordinals = (
        {str(s["section_id"]): i for i, s in enumerate(latest_sections(c, bid, draft))}
        if latest(c, bid, draft)
        else None
    )
    for change in body["changes"]:
        assert_active(c, bid, draft, UUID(change["section_id"]))
        validate_for_schema(change["content"], change["schema_digest"])
        if expected_ordinals is not None and change["content"]["attrs"][
            "ordinal"
        ] != expected_ordinals.get(change["section_id"]):
            raise DomainError(
                "AUTHORING_TREE_ORDER_REQUIRED", "章节顺序请通过树变更预览后确认", 422
            )
        current = fetch(
            c,
            "SELECT * FROM sections WHERE id=%s AND bid_id=%s AND draft_id=%s FOR UPDATE",
            (UUID(change["section_id"]), bid, draft),
        )
        if current["head_revision"] != change["base_section_revision"]:
            raise DomainError("SECTION_REVISION_CONFLICT", "章节已被修改，请核对两份内容", 412)
        append_section(
            c,
            bid,
            draft,
            current["id"],
            current["head_revision"] + 1,
            change["content"],
            actor,
            schema_digest=change["schema_digest"],
        )
    revision = row["revision"] + 1
    c.execute("UPDATE drafts SET revision=%s WHERE id=%s", (revision, draft))
    return freeze(c, bid, draft, revision, actor=actor)


def draft_view(c, bid, draft):
    row = fetch(c, "SELECT * FROM drafts WHERE bid_id=%s AND id=%s", (bid, draft))
    snapshot = fetch(
        c,
        "SELECT id FROM draft_snapshots WHERE draft_id=%s AND revision=%s",
        (draft, row["revision"]),
    )
    sections = latest_sections(c, bid, draft)
    return {
        "id": str(draft),
        "bid_id": str(bid),
        "revision": row["revision"],
        "snapshot_id": str(snapshot["id"]),
        "sections": [
            {
                "id": str(s["section_id"]),
                "title": s["content"]["attrs"]["title"],
                "ordinal": i,
                "revision": s["revision"],
                "rule_ids": s["content"]["attrs"]["rule_ids"],
                "save_state": "saved",
            }
            for i, s in enumerate(sections)
        ],
    }


def snapshot_content(c, bid, snapshot):
    from packages.authoring.snapshot_structure import check_snapshot_sections, snapshot_extension

    row = fetch(c, "SELECT * FROM draft_snapshots WHERE bid_id=%s AND id=%s", (bid, snapshot))
    sections = c.execute(
        """SELECT r.* FROM draft_snapshot_sections ss JOIN section_revisions r ON r.id=ss.section_revision_id
       WHERE ss.snapshot_id=%s ORDER BY ss.ordinal""",
        (snapshot,),
    ).fetchall()
    if (
        sha(row["manifest"]) != row["digest"]
        or [s["digest"] for s in sections] != [s["digest"] for s in row["manifest"]["sections"]]
        or any(sha(s["content"]) != s["digest"] for s in sections)
    ):
        raise DomainError("DRAFT_SNAPSHOT_CORRUPT", "草稿快照校验失败", 409)
    extension = snapshot_extension(row["manifest"])
    check_snapshot_sections(extension, sections)
    result = {
        "snapshot_id": str(snapshot),
        "snapshot_digest": row["digest"],
        "title": row["manifest"]["title"],
        "sections": [clean(s["content"]) for s in sections],
    }
    if extension is not None:
        result["authoring_structure"] = extension
    from packages.authoring.rich_snapshots import checked as checked_images

    images = checked_images(row["manifest"].get("authoring_images"), result["sections"])
    if images is not None:
        result["authoring_images"] = images
    return result


def draft_etag(draft, revision):
    return etag("draft", draft, revision)


def source_authority_manifest(c, refs):
    ids = sorted({UUID(ref["source_id"]) for ref in refs}, key=str)
    if not ids:
        return []
    rows = c.execute(
        """SELECT a.source_id,a.ancestor_id,n.digest,n.known_at,n.policy_class,
       h.revision,h.status,h.valid_from,h.valid_until FROM retrieval.source_ancestors a
       JOIN retrieval.source_nodes n ON n.id=a.ancestor_id
       LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
       WHERE a.source_id=ANY(%s) ORDER BY a.source_id,a.ancestor_id""",
        (ids,),
    ).fetchall()
    return clean(rows)
