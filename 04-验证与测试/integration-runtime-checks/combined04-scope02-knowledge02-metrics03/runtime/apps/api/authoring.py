"""Actual authoring API. Integrate with app.include_router(router)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.authoring.build_repository import (
    DEFAULT_TEMPLATE_ID,
    configured_render_identity,
    freeze_build,
)
from packages.authoring.content import SCHEMA, SCHEMA_DIGEST, empty_content, validate_content
from packages.authoring.patches import apply_proposal, create_proposal
from packages.authoring.repository import (
    append_section,
    draft_etag,
    draft_view,
    freeze,
    lock_draft,
    save_draft,
    snapshot_content,
)
from packages.domain.build_followups import follow_up_view
from packages.domain.runtime import (
    Command,
    DomainError,
    accepted,
    audit,
    clean,
    connection,
    enqueue_plan,
    fetch,
    match,
    uid,
    validate,
)

router = APIRouter(prefix="/api/v1", tags=["authoring"])


def response(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def command(c, request, actor, kind, target, body, conditional=False):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match") if conditional else None,
    )


@router.get("/authoring/capabilities")
def capabilities(r: Request):
    auth(r)
    return {
        "schema_digest": SCHEMA_DIGEST,
        "schema": SCHEMA,
        "default_template_version_id": str(DEFAULT_TEMPLATE_ID),
        "formats": ["docx", "pdf"],
        "supports_external_docx_import": False,
        "supported_nodes": SCHEMA["nodes"],
        "unsupported": [
            "images",
            "tables",
            "external_links",
            "word_roundtrip",
            "production_renderer_sandbox",
        ],
    }


@router.post("/bids/{bid_id}/drafts")
def create(bid_id: UUID, r: Request, body: dict = Body(...)):
    from packages.authoring.repository import create_draft

    a = auth(r, True)
    validate("CreateDraftRequest", body)
    if not 1 <= len(body["title"].strip()) <= 200:
        raise DomainError("INVALID_REQUEST", "请输入不超过200字的草稿名称", 422)
    with connection() as c:
        cmd = command(c, r, a, "create-draft", bid_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        draft, _ = create_draft(
            c, bid_id, a["member_id"], body["title"], body["outline_template_version_id"]
        )
        view = draft_view(c, bid_id, draft)
        audit(c, a["member_id"], bid_id, "draft.created", {"draft_id": draft}, cmd.id)
        return response(cmd.finish(view, 201, {"ETag": draft_etag(draft, 1)}))


@router.get("/bids/{bid_id}/drafts/{draft_id}")
def get_draft(bid_id: UUID, draft_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        view = draft_view(c, bid_id, draft_id)
        return JSONResponse(clean(view), headers={"ETag": draft_etag(draft_id, view["revision"])})


@router.get("/sections/{section_id}")
def get_section(section_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        row = fetch(
            c,
            "SELECT s.head_revision,r.* FROM sections s JOIN section_revisions r ON r.section_id=s.id AND r.revision=s.head_revision WHERE s.id=%s",
            (section_id,),
        )
        return JSONResponse(
            clean(
                {
                    "id": section_id,
                    "draft_id": row["draft_id"],
                    "revision": row["revision"],
                    "schema_digest": row["schema_digest"],
                    "content": row["content"],
                    "writing_plan_id": None,
                }
            ),
            headers={"ETag": f'"section:{section_id}:{row["revision"]}"'},
        )


@router.patch("/bids/{bid_id}/drafts/{draft_id}")
def save(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("SaveDraft", body)
    for change in body["changes"]:
        validate_content(change["content"])
    if len(body["changes"]) > 500:
        raise DomainError("INVALID_REQUEST", "单次最多保存500个章节", 422)
    with connection() as c:
        cmd = command(c, r, a, "save-draft", draft_id, body, True)
        if cmd.replay:
            return response(cmd.replay, True)
        value = save_draft(c, bid_id, draft_id, a["member_id"], body, r.headers.get("if-match"))
        audit(c, a["member_id"], bid_id, "draft.saved", value, cmd.id)
        return response(
            cmd.finish(value, headers={"ETag": draft_etag(draft_id, value["draft_revision"])})
        )


@router.post("/bids/{bid_id}/drafts/{draft_id}/sections")
def add_section(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    if set(body) != {"title"} or not isinstance(body["title"], str):
        raise DomainError("INVALID_REQUEST", "请输入章节名称", 422)
    with connection() as c:
        cmd = command(c, r, a, "add-section", draft_id, body, True)
        if cmd.replay:
            return response(cmd.replay, True)
        row = lock_draft(c, bid_id, draft_id)
        match(r.headers.get("if-match"), "draft", draft_id, row["revision"])
        count = c.execute(
            "SELECT coalesce(max((r.content->'attrs'->>'ordinal')::int),-1)+1 AS ordinal FROM sections s JOIN section_revisions r ON r.section_id=s.id AND r.revision=s.head_revision WHERE s.draft_id=%s",
            (draft_id,),
        ).fetchone()["ordinal"]
        content = empty_content(body["title"], count)
        validate_content(content)
        section = uid()
        c.execute(
            "INSERT INTO sections(id,bid_id,draft_id,head_revision) VALUES(%s,%s,%s,1)",
            (section, bid_id, draft_id),
        )
        append_section(c, bid_id, draft_id, section, 1, content, a["member_id"])
        c.execute("UPDATE drafts SET revision=revision+1 WHERE id=%s", (draft_id,))
        value = freeze(c, bid_id, draft_id, row["revision"] + 1)
        value["section_id"] = str(section)
        return response(cmd.finish(value, 201, {"ETag": draft_etag(draft_id, row["revision"] + 1)}))


@router.get("/bids/{bid_id}/draft-snapshots/{snapshot_id}")
def snapshot(bid_id: UUID, snapshot_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        return snapshot_content(c, bid_id, snapshot_id)


@router.post("/bids/{bid_id}/builds")
def build(bid_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    validate("BuildRequest", body)
    renderer_identity = configured_render_identity(body["formats"])
    with connection() as c:
        cmd = command(c, r, a, "create-build", bid_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        input_id, manifest = freeze_build(c, bid_id, body, a["member_id"], renderer_identity)
        build_id = uid()
        operation = enqueue_plan(
            c,
            bid_id,
            cmd.id,
            "build",
            {"id": str(input_id), "manifest": manifest},
            stages=[{"key": "build-document", "depends_on": []}],
            handler_version="export/1",
            topic="tender.task.export.v1",
        )
        c.execute(
            "INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (build_id, bid_id, input_id, UUID(operation["id"])),
        )
        audit(
            c,
            a["member_id"],
            bid_id,
            "build.requested",
            {"build_id": build_id, "input_id": input_id},
            cmd.id,
        )
        return response(cmd.finish(accepted(operation, build_id, "build"), 202))


@router.get("/builds/{build_id}")
def get_build(build_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = fetch(
            c,
            "SELECT b.*,i.draft_snapshot_id,i.manifest FROM builds b JOIN build_inputs i ON i.id=b.input_id WHERE b.id=%s",
            (build_id,),
        )
        outputs = c.execute(
            "SELECT format,state,document_version_id,gaps FROM build_outputs WHERE build_id=%s ORDER BY format",
            (build_id,),
        ).fetchall()
        by_format = {o["format"]: dict(o) for o in outputs}
        for fmt in list(dict.fromkeys(["docx"] + row["manifest"]["formats"])):
            by_format.setdefault(
                fmt, {"format": fmt, "state": "pending", "document_version_id": None, "gaps": []}
            )
        return clean(
            {
                "id": build_id,
                "draft_snapshot_id": row["draft_snapshot_id"],
                "operation_id": row["operation_id"],
                "outputs": list(by_format.values()),
                "follow_up": follow_up_view(c, build_id, row["manifest"]),
            }
        )


@router.post("/bids/{bid_id}/sections/{section_id}/patch-previews")
def preview(bid_id: UUID, section_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    expected = {"base_section_revision", "node_id", "before_digest", "replacement", "source_refs"}
    if (
        set(body) != expected
        or type(body["base_section_revision"]) is not int
        or not isinstance(body["replacement"], dict)
        or not isinstance(body["source_refs"], list)
    ):
        raise DomainError("INVALID_REQUEST", "建议预览格式无效", 422)
    with connection() as c:
        cmd = command(c, r, a, "preview-authoring-patch", section_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        value = create_proposal(
            c,
            bid_id,
            section_id,
            body["base_section_revision"],
            body["node_id"],
            body["before_digest"],
            body["replacement"],
            body["source_refs"],
        )
        return response(cmd.finish(value, 201))


@router.post("/bids/{bid_id}/authoring-actions/{action_id}/apply")
def apply(bid_id: UUID, action_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    if body:
        raise DomainError("INVALID_REQUEST", "应用建议不接受额外字段", 422)
    with connection() as c:
        cmd = command(c, r, a, "apply-authoring-patch", action_id, body, True)
        if cmd.replay:
            return response(cmd.replay, True)
        value = apply_proposal(
            c, bid_id, action_id, a["member_id"], cmd.id, r.headers.get("if-match")
        )
        audit(
            c,
            a["member_id"],
            bid_id,
            "authoring.patch_applied",
            {"action_id": action_id, **value},
            cmd.id,
        )
        return response(
            cmd.finish(
                value, headers={"ETag": draft_etag(value["draft_id"], value["draft_revision"])}
            )
        )


@router.get("/bids/{bid_id}/drafts")
def list_drafts(
    bid_id: UUID, r: Request, after: UUID | None = None, limit: int = Query(50, ge=1, le=100)
):
    auth(r)
    with connection() as c:
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        rows = c.execute(
            "SELECT d.id,d.revision,s.manifest->>'title' AS title,s.id AS snapshot_id FROM drafts d JOIN draft_snapshots s ON s.draft_id=d.id AND s.revision=d.revision WHERE d.bid_id=%s AND (%s::uuid IS NULL OR d.id>%s::uuid) ORDER BY d.id LIMIT %s",
            (bid_id, after, after, limit + 1),
        ).fetchall()
        return clean(
            {
                "items": rows[:limit],
                "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            }
        )


@router.get("/sections/{section_id}/revisions")
def section_revisions(
    section_id: UUID,
    r: Request,
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    auth(r)
    with connection() as c:
        fetch(c, "SELECT id FROM sections WHERE id=%s", (section_id,))
        rows = c.execute(
            "SELECT revision,created_at,digest,created_by FROM section_revisions WHERE section_id=%s AND (%s::bigint IS NULL OR revision<%s::bigint) ORDER BY revision DESC LIMIT %s",
            (section_id, before, before, limit + 1),
        ).fetchall()
        return clean(
            {
                "items": rows[:limit],
                "next_cursor": rows[limit - 1]["revision"] if len(rows) > limit else None,
            }
        )


@router.post("/bids/{bid_id}/sections/{section_id}/restore")
def restore(bid_id: UUID, section_id: UUID, r: Request, body: dict = Body(...)):
    a = auth(r, True)
    if set(body) != {"revision", "base_draft_revision", "base_section_revision"} or any(
        type(v) is not int or v < 1 for v in body.values()
    ):
        raise DomainError("INVALID_REQUEST", "恢复章节需要明确原修订与当前修订", 422)
    with connection() as c:
        cmd = command(c, r, a, "restore-authoring-section", section_id, body, True)
        if cmd.replay:
            return response(cmd.replay, True)
        section = fetch(c, "SELECT * FROM sections WHERE id=%s AND bid_id=%s", (section_id, bid_id))
        historical = fetch(
            c,
            "SELECT content FROM section_revisions WHERE section_id=%s AND revision=%s",
            (section_id, body["revision"]),
        )
        value = save_draft(
            c,
            bid_id,
            section["draft_id"],
            a["member_id"],
            {
                "base_draft_revision": body["base_draft_revision"],
                "changes": [
                    {
                        "section_id": str(section_id),
                        "base_section_revision": body["base_section_revision"],
                        "schema_digest": SCHEMA_DIGEST,
                        "content": historical["content"],
                    }
                ],
            },
            r.headers.get("if-match"),
        )
        audit(
            c,
            a["member_id"],
            bid_id,
            "authoring.section_restored",
            {"section_id": section_id, "restored_from_revision": body["revision"], **value},
            cmd.id,
        )
        return response(
            cmd.finish(
                value, headers={"ETag": draft_etag(section["draft_id"], value["draft_revision"])}
            )
        )
