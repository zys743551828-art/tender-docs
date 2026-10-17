"""服务器可恢复的构建历史；刷新发现不依赖浏览器记住构建ID。"""

from uuid import UUID

from fastapi import APIRouter, Query, Request

from apps.api.auth import auth
from packages.domain.build_followups import follow_up_view
from packages.domain.runtime import clean, connection, decode_cursor, encode_cursor, fetch, validate

router = APIRouter(prefix="/api/v1", tags=["history"])


def build_view(c, build_id):
    row = fetch(
        c,
        "SELECT b.*,i.draft_snapshot_id,i.manifest FROM builds b JOIN build_inputs i ON i.id=b.input_id WHERE b.id=%s",
        (build_id,),
    )
    outputs = c.execute(
        "SELECT format,state,document_version_id,gaps FROM build_outputs WHERE build_id=%s ORDER BY format",
        (build_id,),
    ).fetchall()
    by_format = {item["format"]: dict(item) for item in outputs}
    for fmt in list(dict.fromkeys(["docx"] + row["manifest"]["formats"])):
        by_format.setdefault(
            fmt, {"format": fmt, "state": "pending", "document_version_id": None, "gaps": []}
        )
    value = clean(
        {
            "id": row["id"],
            "draft_snapshot_id": row["draft_snapshot_id"],
            "operation_id": row["operation_id"],
            "outputs": list(by_format.values()),
        }
    )
    validate("BuildView", value)
    return value | {"follow_up": follow_up_view(c, build_id, row["manifest"])}


@router.get("/bids/{bid_id}/builds")
def list_builds(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"builds:{bid_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        if cursor:
            at, identity = decode_cursor(cursor, scope=scope)
            rows = c.execute(
                "SELECT b.id,o.created_at FROM builds b JOIN operations o ON o.id=b.operation_id WHERE b.bid_id=%s AND(o.created_at,b.id)<(%s,%s) ORDER BY o.created_at DESC,b.id DESC LIMIT %s",
                (bid_id, at, identity, limit + 1),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT b.id,o.created_at FROM builds b JOIN operations o ON o.id=b.operation_id WHERE b.bid_id=%s ORDER BY o.created_at DESC,b.id DESC LIMIT %s",
                (bid_id, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [build_view(c, row["id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope=scope) if more else None,
        }
