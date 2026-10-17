"""上传布局不含票据；后续授权仍受上传强ETag和Cookie/CSRF保护。"""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import connection, etag
from packages.uploads.read_model import layout_view

router = APIRouter(prefix="/api/v1", tags=["upload recovery"])


@router.get("/uploads/{upload_id}/layout")
def get_layout(upload_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = layout_view(c, upload_id)
        return JSONResponse(
            value,
            headers={
                "ETag": etag("upload", upload_id, value["revision"]),
                "Cache-Control": "private,no-store",
            },
        )
