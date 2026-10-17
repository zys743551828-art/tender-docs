"""两轮比较异步受理与持久发现；模型重跑能力明确未提供。"""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.human_routes import command, saved
from apps.api.views import match_bid
from packages.domain.comparisons import method_digest, pair_metadata, resource
from packages.domain.review_data import read_json_blob, save_json
from packages.domain.runtime import (
    DomainError,
    accepted,
    audit,
    connection,
    decode_cursor,
    encode_cursor,
    enqueue_plan,
    etag,
    fetch,
    sha,
    validate,
)

router = APIRouter(prefix="/api/v1", tags=["review comparison"])


@router.post("/bids/{bid_id}/comparisons")
def create_comparison(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("ComparisonRequest", body)
    if body["same_method_rerun"]:
        raise DomainError(
            "COMPARISON_RERUN_NOT_CONFIGURED",
            "当前可比较已完成的两轮原始事实；历史方法重跑尚未提供，未发起收费请求",
            409,
        )
    with connection() as c:
        cmd = command(c, r, actor, "compare_reviews", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        match_bid(c, r.headers.get("if-match"), bid_id)
        c.rollback()
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        metadata = pair_metadata(c, bid_id, body["left_review_id"], body["right_review_id"])
        blobs = {
            side: fetch(
                c, "SELECT * FROM blobs WHERE id=%s", (metadata[side]["evidence_manifest_blob_id"],)
            )
            for side in ("left", "right")
        }
    plan = {
        side: metadata[side] | {"root": read_json_blob(blobs[side])} for side in ("left", "right")
    }
    context_digest = sha(metadata)
    plan["context_digest"] = context_digest
    plan["comparison_method_digest"] = method_digest()
    stored = save_json(plan)
    with connection() as c:
        cmd = command(c, r, actor, "compare_reviews", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        match_bid(c, r.headers.get("if-match"), bid_id)
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        actual = pair_metadata(c, bid_id, body["left_review_id"], body["right_review_id"])
        if sha(actual) != context_digest:
            raise DomainError(
                "COMPARISON_CONTEXT_CHANGED", "所选轮次核对结果或来源已变化，请刷新后比较", 412
            )
        operation = enqueue_plan(
            c,
            bid_id,
            cmd.id,
            "compare-reviews",
            {"id": stored["id"]},
            stages=[{"key": "compare-reviews", "depends_on": []}],
            handler_version="comparison/1",
            topic="tender.task.comparison.v1",
        )
        audit(
            c,
            actor["member_id"],
            bid_id,
            "comparison.requested",
            {
                "comparison_id": operation["id"],
                "left_review_id": body["left_review_id"],
                "right_review_id": body["right_review_id"],
                "context_digest": context_digest,
                "comparison_method_digest": plan["comparison_method_digest"],
            },
            cmd.id,
        )
        return saved(cmd.finish(accepted(operation, operation["id"], "comparison"), 202))


@router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        view = resource(c, comparison_id)
        blob = (
            fetch(c, "SELECT * FROM blobs WHERE id=%s", (view["result_blob_id"],))
            if view["result_blob_id"]
            else None
        )
    result = read_json_blob(blob) if blob else None
    value = view | {"result": result}
    return JSONResponse(value, headers={"ETag": etag("comparison", comparison_id, sha(value))})


@router.get("/bids/{bid_id}/comparisons")
def list_comparisons(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"comparisons:{bid_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        query = "SELECT id,created_at,payload FROM audit_entries WHERE bid_id=%s AND event_type='comparison.requested'"
        parameters: list = [bid_id]
        if cursor:
            at, identity = decode_cursor(cursor, scope)
            query += " AND(created_at,id)<(%s,%s)"
            parameters += [at, identity]
        rows = c.execute(
            query + " ORDER BY created_at DESC,id DESC LIMIT %s", parameters + [limit + 1]
        ).fetchall()
        more, items = len(rows) > limit, rows[:limit]
        return {
            "items": [resource(c, row["payload"]["comparison_id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope) if more else None,
        }
