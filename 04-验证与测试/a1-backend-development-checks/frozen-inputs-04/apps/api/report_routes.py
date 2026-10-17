"""报告控制面：显式读取展示身份，冻结后Kafka导出，不触发任何模型。"""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.human_routes import check_tag, command, saved
from apps.api.views import operation_view
from packages.authoring.build_repository import configured_render_identity
from packages.domain.reports import context, report_view
from packages.domain.review_data import save_json
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
    uid,
    validate,
)

router = APIRouter(prefix="/api/v1", tags=["review reports"])


@router.get("/reviews/{review_id}/report-exports")
def list_reports(
    review_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"report-exports:{review_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        fetch(c, "SELECT id FROM review_runs WHERE id=%s", (review_id,))
        query = "SELECT id,created_at,payload FROM audit_entries WHERE event_type='report.requested' AND payload->>'review_id'=%s"
        parameters: list = [str(review_id)]
        if cursor:
            at, identity = decode_cursor(cursor, scope)
            query += " AND(created_at,id)<(%s,%s)"
            parameters += [at, identity]
        rows = c.execute(
            query + " ORDER BY created_at DESC,id DESC LIMIT %s", parameters + [limit + 1]
        ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [report_view(c, row["payload"]["report_id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope) if more else None,
        }


@router.get("/reviews/{review_id}/report-context")
def get_report_context(review_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value, _ = context(c, review_id)
        return JSONResponse(value, headers={"ETag": etag("report-context", review_id, sha(value))})


@router.post("/reviews/{review_id}/report-exports")
def create_report(review_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("ReportRequest", body)
    if body["artifact_kind"] == "annotated_review_copy" and body["format"] != "pdf":
        raise DomainError(
            "REPORT_FORMAT_UNSUPPORTED", "原页批注副本当前支持PDF，请选择PDF格式", 422
        )
    with connection() as c:
        cmd = command(c, r, actor, "create_report", review_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        value, frozen = context(c, review_id)
        check_tag(r, etag("report-context", review_id, sha(value)))
        if any(
            body[key] != value[key] for key in ("presentation_revision", "source_impact_revision")
        ):
            raise DomainError(
                "REPORT_PRESENTATION_CHANGED", "展示政策或来源影响已更新，请重新读取后生成", 412
            )
        c.rollback()
    # 本地转换能力与对象写入均不持数据库连接/业务锁。
    renderer = (
        configured_render_identity(["pdf"])
        if body["format"] == "pdf" and body["artifact_kind"] == "review_report"
        else None
    )
    if body["format"] == "pdf" and body["artifact_kind"] == "review_report" and not renderer:
        raise DomainError(
            "REPORT_RENDERER_NOT_CONFIGURED", "PDF报告转换能力暂未就绪，可选择Word报告", 503
        )
    frozen_blob = save_json(
        {
            "content": frozen,
            "context": value,
            "request": body,
            "renderer_identity": renderer,
            "actor_id": str(actor["member_id"]),
        }
    )
    with connection() as c:
        cmd = command(c, r, actor, "create_report", review_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        bid = fetch(c, "SELECT bid_id FROM review_runs WHERE id=%s", (review_id,))["bid_id"]
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        current, _ = context(c, review_id)
        check_tag(r, etag("report-context", review_id, sha(current)))
        if current != value:
            raise DomainError("REPORT_PRESENTATION_CHANGED", "展示内容已更新，请刷新后生成", 412)
        prior = c.execute(
            "SELECT a.payload,o.id FROM audit_entries a JOIN operations o ON o.id::text=a.payload->>'operation_id' WHERE a.bid_id=%s AND a.event_type='report.requested' AND a.payload->>'review_id'=%s AND a.payload->>'context_digest'=%s AND a.payload->>'artifact_kind'=%s AND a.payload->>'format'=%s AND (a.payload->>'presentation_revision')::bigint=%s AND (a.payload->>'source_impact_revision')::bigint=%s AND o.state IN('queued','running','succeeded') ORDER BY a.created_at DESC,a.id DESC LIMIT 1",
            (
                bid,
                str(review_id),
                value["context_digest"],
                body["artifact_kind"],
                body["format"],
                value["presentation_revision"],
                value["source_impact_revision"],
            ),
        ).fetchone()
        if prior:
            reused = accepted(
                operation_view(c, prior["id"]), prior["payload"]["report_id"], "report"
            )
            reused["disposition"] = "reused"
            return saved(
                cmd.finish(
                    reused,
                    202,
                    {"Location": f"/api/v1/report-exports/{prior['payload']['report_id']}"},
                )
            )
        c.execute(
            "UPDATE bids SET presentation_revision=%s WHERE id=%s",
            (value["presentation_revision"], bid),
        )
        audit(
            c,
            actor["member_id"],
            bid,
            "report.presentation_allocated",
            {
                "presentation_revision": value["presentation_revision"],
                "context_digest": value["context_digest"],
            },
            cmd.id,
        )
        identity = uid()
        operation = enqueue_plan(
            c,
            bid,
            cmd.id,
            "report",
            {"id": identity, "blob_digest": frozen_blob["digest"]},
            stages=[{"key": "render-report", "depends_on": []}],
            handler_version="report/1",
            topic="tender.task.export.v1",
        )
        audit(
            c,
            actor["member_id"],
            bid,
            "report.requested",
            {
                "report_id": str(identity),
                "review_id": str(review_id),
                "operation_id": operation["id"],
                "frozen_blob_id": str(frozen_blob["id"]),
                "frozen_blob_digest": frozen_blob["digest"],
                "context_digest": value["context_digest"],
                "presentation_revision": value["presentation_revision"],
                "source_impact_revision": value["source_impact_revision"],
                "artifact_kind": body["artifact_kind"],
                "format": body["format"],
                "actor_id": str(actor["member_id"]),
            },
            cmd.id,
        )
        return saved(
            cmd.finish(
                accepted(operation, identity, "report"),
                202,
                {"Location": f"/api/v1/report-exports/{identity}"},
            )
        )


@router.get("/report-exports/{report_id}")
def get_report(report_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = report_view(c, report_id)
        return JSONResponse(value, headers={"ETag": etag("report", report_id, sha(value))})
