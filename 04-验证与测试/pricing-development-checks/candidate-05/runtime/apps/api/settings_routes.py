"""项目开关条件写入；展示变化不触发模型，人工确认不由开关伪造。"""

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from apps.api.auth import auth
from packages.domain.reviews import on_material_commit
from packages.domain.runtime import audit, clean, connection, etag, fetch, match, validate
from packages.domain.work_access import begin

router = APIRouter(prefix="/api/v1", tags=["project settings"])


def _view(row):
    return {
        "bid_id": str(row["id"]),
        "revision": row["settings_revision"],
        "settings": row["settings"],
    }


@router.get("/bids/{bid_id}/settings")
def get_settings(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        row = fetch(c, "SELECT * FROM bids WHERE id=%s", (bid_id,))
        body = _view(row)
        validate("SettingsView", body)
        return JSONResponse(
            body, headers={"ETag": etag("settings", bid_id, row["settings_revision"])}
        )


@router.patch("/bids/{bid_id}/settings")
def patch_settings(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("SettingsPatch", body)
    with connection() as c:
        command = begin(
            c,
            actor["member_id"],
            "update_settings",
            bid_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
            bid=bid_id,
        )
        if command.replay:
            return JSONResponse(
                command.replay["body"],
                status_code=command.replay["status"],
                headers=command.replay["headers"] | {"Idempotent-Replayed": "true"},
            )
        row = fetch(c, "SELECT * FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        match(r.headers.get("if-match"), "settings", bid_id, row["settings_revision"])
        settings = row["settings"] | body
        changed = {key for key in body if body[key] != row["settings"].get(key)}
        if changed:
            display = bool(changed & {"show_prediction", "result_reminder", "pricing"})
            c.execute(
                "UPDATE bids SET settings=%s,settings_revision=settings_revision+1,revision=revision+1,presentation_revision=presentation_revision+%s WHERE id=%s",
                (Jsonb(settings), int(display), bid_id),
            )
            row = fetch(c, "SELECT * FROM bids WHERE id=%s", (bid_id,))
            semantic = bool(changed & {"consistency", "scope_residue"})
            if semantic or "auto_review" in changed and settings["auto_review"]:
                on_material_commit(c, bid_id, command.id)
            audit(
                c,
                actor["member_id"],
                bid_id,
                "settings.updated",
                {"changed": sorted(changed), "content_context_changed": semantic},
                command.id,
            )
        value = _view(row)
        validate("SettingsView", value)
        saved = command.finish(
            clean(value), 200, {"ETag": etag("settings", bid_id, row["settings_revision"])}
        )
        return JSONResponse(saved["body"], headers=saved["headers"])
