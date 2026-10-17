"""上传布局不含票据；后续授权仍受上传强ETag和Cookie/CSRF保护。"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import batch_view
from packages.domain.runtime import connection, decode_cursor, encode_cursor, etag, fetch
from packages.uploads.read_model import layout_view

router = APIRouter(prefix="/api/v1", tags=["upload recovery"])


@router.get("/bids/{bid_id}/upload-batches")
def list_batches(
    bid_id: UUID,
    r: Request,
    cursor: str | None = None,
    state: str | None = Query(None, pattern="^(open|verifying|committed|failed)$"),
    limit: int = Query(20, ge=1, le=100),
):
    """服务器发现未完成批次；状态筛选参与游标身份，跨浏览器无需本地ID。"""
    auth(r)
    scope = f"upload-batches:{bid_id}:{state or 'all'}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        # 目标表无创建时点列；按真实UUID稳定分页，不伪造时间排序。
        query = "SELECT id,to_timestamp(0) AS created_at FROM upload_batches WHERE bid_id=%s"
        parameters: list = [bid_id]
        if state:
            query += " AND state=%s"
            parameters.append(state)
        if cursor:
            _, identity = decode_cursor(cursor, scope)
            query += " AND id<%s"
            parameters.append(identity)
        rows = c.execute(query + " ORDER BY id DESC LIMIT %s", parameters + [limit + 1]).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [batch_view(c, bid_id, row["id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope) if more else None,
        }


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
