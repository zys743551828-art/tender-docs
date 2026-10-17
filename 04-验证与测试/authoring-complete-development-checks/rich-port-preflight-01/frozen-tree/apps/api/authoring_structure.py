"""Complete-A2 tree/plan/template API. Include this router separately from text editing."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.authoring import response
from packages.authoring import structure_repository as service
from packages.authoring import structure_schema, templates
from packages.authoring.repository import draft_etag
from packages.domain.runtime import Command, clean, connection, fetch
from packages.domain.work_access import begin

router = APIRouter(prefix="/api/v1", tags=["authoring-structure"])


@router.get("/bids/{bid_id}/drafts/{draft_id}/structure")
def tree(bid_id: UUID, draft_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = service.view(c, bid_id, draft_id)
        return JSONResponse(
            clean(value), headers={"ETag": draft_etag(draft_id, value["draft_revision"])}
        )


def begin_draft(c, r, actor, bid, draft, kind, body):
    fetch(c, "SELECT id FROM drafts WHERE id=%s AND bid_id=%s", (draft, bid))
    return begin(
        c,
        actor["member_id"],
        kind,
        draft,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match"),
        bid=bid,
    )


@router.post("/bids/{bid_id}/drafts/{draft_id}/structure/preview")
def preview(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    structure_schema.validate("AuthoringTreeChangeRequest", body)
    with connection() as c:
        cmd = begin_draft(c, r, actor, bid_id, draft_id, "preview-authoring-structure", body)
        if cmd.replay:
            return response(cmd.replay, True)
        value = service.preview(
            c, bid_id, draft_id, actor["member_id"], body, r.headers.get("if-match")
        )
        return response(cmd.finish(value, 201))


@router.post("/bids/{bid_id}/authoring-structure-actions/{action_id}/resolve")
def resolve(bid_id: UUID, action_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    structure_schema.validate("AuthoringStructureResolve", body)
    with connection() as c:
        fetch(c, "SELECT id FROM proposed_actions WHERE id=%s AND bid_id=%s", (action_id, bid_id))
        # Receipts and rejection are allowed after closure; service guards fresh confirms.
        cmd = begin(
            c,
            actor["member_id"],
            "resolve-authoring-structure",
            action_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
            bid=bid_id,
            work=False,
        )
        if cmd.replay:
            return response(cmd.replay, True)
        value = service.resolve(
            c,
            bid_id,
            action_id,
            actor["member_id"],
            cmd.id,
            body["decision"],
            r.headers.get("if-match"),
        )
        headers = (
            {"ETag": draft_etag(UUID(value["draft_id"]), value["draft_revision"])}
            if "draft_revision" in value
            else {}
        )
        return response(cmd.finish(value, headers=headers))


@router.put("/bids/{bid_id}/drafts/{draft_id}/sections/{section_id}/plan")
def plan(bid_id: UUID, draft_id: UUID, section_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    structure_schema.validate("AuthoringPlanSave", body)
    with connection() as c:
        fetch(
            c,
            "SELECT id FROM sections WHERE id=%s AND bid_id=%s AND draft_id=%s",
            (section_id, bid_id, draft_id),
        )
        cmd = begin_draft(
            c,
            r,
            actor,
            bid_id,
            draft_id,
            "save-authoring-plan",
            body | {"section_id": str(section_id)},
        )
        if cmd.replay:
            return response(cmd.replay, True)
        value = service.save_plan(
            c, bid_id, draft_id, section_id, actor["member_id"], body, r.headers.get("if-match")
        )
        return response(
            cmd.finish(value, headers={"ETag": draft_etag(draft_id, value["draft_revision"])})
        )


@router.get("/authoring/templates")
def catalog(
    r: Request,
    kind: Literal["outline", "layout"] | None = None,
    after: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    auth(r)
    with connection() as c:
        return templates.catalog(c, kind, after, limit)


@router.get("/authoring/template-versions/{version_id}")
def template(version_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        value = templates.view(c, version_id)
        return JSONResponse(clean(value), headers={"ETag": templates.family_tag(value)})


@router.post("/authoring/template-families")
def create_template(r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    structure_schema.validate("AuthoringTemplateCreate", body)
    with connection() as c:
        cmd = Command(
            c,
            actor["member_id"],
            "create-authoring-template",
            actor["member_id"],
            r.headers.get("idempotency-key"),
            body,
            None,
        )
        if cmd.replay:
            return response(cmd.replay, True)
        value = templates.create(c, actor["member_id"], body)
        return response(cmd.finish(value, 201, {"ETag": templates.family_tag(value)}))


@router.post("/authoring/template-families/{family_id}/versions")
def append_template(family_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    structure_schema.validate("AuthoringTemplateVersion", body)
    with connection() as c:
        cmd = Command(
            c,
            actor["member_id"],
            "append-authoring-template",
            family_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            return response(cmd.replay, True)
        value = templates.append(c, family_id, actor["member_id"], body, r.headers.get("if-match"))
        return response(cmd.finish(value, 201, {"ETag": templates.family_tag(value)}))


def preview_mode(bid, draft, r, body, schema, kind, handler):
    actor = auth(r, True)
    structure_schema.validate(schema, body)
    with connection() as c:
        cmd = begin_draft(c, r, actor, bid, draft, kind, body)
        if cmd.replay:
            return response(cmd.replay, True)
        value = handler(c, bid, draft, actor["member_id"], body, r.headers.get("if-match"))
        return response(cmd.finish(value, 201 if value.get("action_id") else 200))


@router.post("/bids/{bid_id}/drafts/{draft_id}/directory-template/preview")
def directory_preview(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    return preview_mode(
        bid_id,
        draft_id,
        r,
        body,
        "AuthoringDirectoryApplyPreview",
        "preview-authoring-directory",
        service.directory_preview,
    )


@router.post("/bids/{bid_id}/drafts/{draft_id}/style-template/preview")
def style_preview(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    return preview_mode(
        bid_id,
        draft_id,
        r,
        body,
        "AuthoringStyleApplyPreview",
        "preview-authoring-style",
        service.style_preview,
    )


@router.post("/bids/{bid_id}/drafts/{draft_id}/structure/restore-preview")
def restore_preview(bid_id: UUID, draft_id: UUID, r: Request, body: dict = Body(...)):
    return preview_mode(
        bid_id,
        draft_id,
        r,
        body,
        "AuthoringStructureRestorePreview",
        "preview-authoring-structure-restore",
        service.restore_preview,
    )


@router.get("/bids/{bid_id}/drafts/{draft_id}/structure/history")
def history(
    bid_id: UUID,
    draft_id: UUID,
    r: Request,
    before_revision: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    auth(r)
    with connection() as c:
        return service.history(c, bid_id, draft_id, before_revision, limit)
