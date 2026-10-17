"""真实评审API；准备请求、冻结轮次及历史分别可恢复读取。"""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.review_views import (
    finding_view,
    matrix_view,
    review_row,
    review_state,
    review_view,
    rule_set_view,
    rule_view,
)
from apps.api.views import evidence_view, match_bid
from packages.domain.finding_lifecycle import visible_rows
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.reviews import _locked, _write_intent, queue_intent
from packages.domain.runtime import (
    Command,
    DomainError,
    audit,
    clean,
    connection,
    decode_cursor,
    encode_cursor,
    etag,
    fetch,
    sha,
    validate,
)

router = APIRouter(prefix="/api/v1", tags=["reviews"])


def _response(body, kind, identity, revision=None):
    return JSONResponse(
        clean(body),
        headers={"ETag": etag(kind, identity, revision if revision is not None else sha(body))},
    )


@router.post("/bids/{bid_id}/reviews")
def request_review(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("ReviewRequest", body)
    DeepSeekClient.from_env()
    with connection() as c:
        cmd = Command(
            c,
            actor["member_id"],
            "request_review",
            bid_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            return JSONResponse(
                cmd.replay["body"],
                status_code=cmd.replay["status"],
                headers=cmd.replay["headers"] | {"Idempotent-Replayed": "true"},
            )
        fetch(c, "SELECT * FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        match_bid(c, r.headers.get("if-match"), bid_id)
        value = queue_intent(c, bid_id, cmd.id, body)
        validate("Accepted", value)
        stored = cmd.finish(value, 202)
        return JSONResponse(stored["body"], status_code=202)


@router.get("/bids/{bid_id}/review-state")
def get_review_state(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return _response(review_state(c, bid_id), "review-state", bid_id)


@router.get("/bids/{bid_id}/reviews")
def list_reviews(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"reviews:{bid_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        if cursor:
            at, identity = decode_cursor(cursor, scope=scope)
            rows = c.execute(
                "SELECT id,created_at FROM review_runs WHERE bid_id=%s AND(created_at,id)<(%s,%s) ORDER BY created_at DESC,id DESC LIMIT %s",
                (bid_id, at, identity, limit + 1),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id,created_at FROM review_runs WHERE bid_id=%s ORDER BY created_at DESC,id DESC LIMIT %s",
                (bid_id, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [review_view(c, row["id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope=scope) if more else None,
        }


@router.get("/reviews/{review_id}")
def get_review(review_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = review_view(c, review_id)
        validate("ReviewView", body)
        return _response(body, "review", review_id)


@router.get("/reviews/{review_id}/manifest")
def get_manifest(review_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        row = review_row(c, review_id)
        validate("ReviewManifest", row["manifest"])
        return _response(row["manifest"], "review-manifest", review_id, row["manifest_digest"])


@router.get("/reviews/{review_id}/matrix")
def get_matrix(review_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = matrix_view(c, review_id)
        validate("MatrixView", body)
        return _response(body, "matrix", review_id)


@router.get("/reviews/{review_id}/findings")
def list_findings(
    review_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)
):
    auth(r)
    scope = f"findings:{review_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        review_row(c, review_id)
        # findings没有created_at，使用不可变UUID有序游标，仍绑定本轮身份。
        last = None
        if cursor:
            _, last = decode_cursor(cursor, scope=scope)
        rows = [
            row for row in visible_rows(c, review_id) if last is None or str(row["id"]) > str(last)
        ]
        more = len(rows) > limit
        items = rows[:limit]
        from datetime import datetime, timezone

        page = {
            "items": [finding_view(c, row["id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(
                {"id": items[-1]["id"], "created_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
                scope=scope,
            )
            if more
            else None,
        }
        validate("FindingPage", page)
        return page


@router.get("/bids/{bid_id}/rules")
def get_rules(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        row = fetch(c, "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid_id,))
        if row["current_rule_set_id"] is None:
            raise DomainError("RULE_SET_NOT_READY", "本轮要求尚未准备完成", 409)
        body = rule_set_view(c, row["current_rule_set_id"])
        validate("RuleSetView", body)
        return _response(body, "rule-set", row["current_rule_set_id"])


@router.get("/rules/{rule_id}")
def get_rule(rule_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = rule_view(c, rule_id)
        validate("RuleView", body)
        return _response(body, "rule", rule_id, body["decision_revision"])


@router.get("/findings/{finding_id}")
def get_finding(finding_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = finding_view(c, finding_id)
        validate("FindingView", body)
        return _response(body, "finding", finding_id)


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        body = evidence_view(c, evidence_id)
        source = c.execute(
            "SELECT h.status FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN retrieval.source_nodes n ON n.document_version_id=p.document_version_id JOIN retrieval.source_heads h ON h.source_id=n.id WHERE e.id=%s AND h.status='withdrawn' LIMIT 1",
            (evidence_id,),
        ).fetchone()
        if source:
            body["source_status"] = "withdrawn"
        validate("EvidenceView", body)
        return _response(body, "evidence", evidence_id)


@router.post("/bids/{bid_id}/review-requests/{request_id}/cancel")
def cancel_review_request(bid_id: UUID, request_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("Cancellation", body)
    with connection() as c:
        cmd = Command(
            c,
            actor["member_id"],
            "cancel_review_request",
            request_id,
            r.headers.get("idempotency-key"),
            body,
            r.headers.get("if-match"),
        )
        if cmd.replay:
            return JSONResponse(
                cmd.replay["body"],
                status_code=cmd.replay["status"],
                headers=cmd.replay["headers"] | {"Idempotent-Replayed": "true"},
            )
        _, slot = _locked(c, bid_id)
        before = review_state(c, bid_id)
        tag = etag("review-state", bid_id, sha(before))
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请读取当前评审请求后取消", 428)
        if r.headers["if-match"] != tag:
            raise DomainError("REVISION_CONFLICT", "评审请求或状态已变化，请核对后重试", 412)
        intent = slot["pending_intent"]
        if not intent or intent["request_id"] != str(request_id):
            raise DomainError("REVIEW_REQUEST_CHANGED", "该请求已被更新，请读取当前请求", 409)
        if intent.get("review_id"):
            raise DomainError("REVIEW_ALREADY_FROZEN", "本轮已冻结，请从本轮操作取消核对", 409)
        if intent["state"] not in ("cancelled", "superseded"):
            if intent["preparation_kind"] == "rules":
                op = fetch(
                    c,
                    "SELECT * FROM operations WHERE id=%s FOR UPDATE",
                    (intent["prepare_operation_id"],),
                )
                # 共用准备和解析不因单个用户意图取消而中断。
                if str(op["command_id"]) == intent["command_id"] and op["state"] in (
                    "queued",
                    "running",
                ):
                    c.execute(
                        "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                        (op["id"],),
                    )
                    c.execute(
                        "UPDATE step_receipts SET lease_until=now() WHERE operation_id=%s AND state='running'",
                        (op["id"],),
                    )
            intent.update(
                state="cancelled",
                error_code="REVIEW_REQUEST_CANCELLED",
                gaps=["本次评审请求已取消；资料和已产生的准备记录保留"],
            )
            _write_intent(c, bid_id, intent)
            audit(
                c,
                actor["member_id"],
                bid_id,
                "review.request_cancelled",
                {"request_id": str(request_id)},
                cmd.id,
            )
        value = review_state(c, bid_id)
        stored = cmd.finish(value, headers={"ETag": etag("review-state", bid_id, sha(value))})
        return JSONResponse(stored["body"], headers=stored["headers"])


@router.get("/evidence/{evidence_id}/authoring-reference")
def get_authoring_reference(evidence_id: UUID, r: Request):
    from packages.authoring.content import empty_content
    from packages.authoring.repository import assert_sources
    from packages.rag.indexing import parse_source_id

    auth(r)
    with connection() as c:
        evidence = fetch(
            c,
            "SELECT e.id,e.bid_id,e.parse_id,e.physical_page,p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id WHERE e.id=%s",
            (evidence_id,),
        )
        # 与最终使用路径同锁序和同一资格函数；GET不发布来源、不登记citation或决定。
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (evidence["bid_id"],))
        source_id = parse_source_id(evidence["parse_id"])
        head = c.execute(
            "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (source_id,)
        ).fetchone()
        reference = None
        reasons = ["AUTHORING_SOURCE_NOT_PUBLISHED"]
        if head:
            proposed = clean(
                {
                    "source_id": source_id,
                    "source_revision": head["revision"],
                    "evidence_id": evidence_id,
                    "document_version_id": evidence["document_version_id"],
                }
            )
            content = empty_content("引用资格核验", 0)
            content["attrs"]["source_refs"] = [proposed]
            try:
                assert_sources(c, evidence["bid_id"], content)
            except DomainError as error:
                reasons = [error.code]
            else:
                reference, reasons = proposed, []
        body = clean(
            {
                "evidence_id": evidence_id,
                "bid_id": evidence["bid_id"],
                "parse_run_id": evidence["parse_id"],
                "document_version_id": evidence["document_version_id"],
                "physical_page": evidence["physical_page"],
                "authoring_reference": reference,
                "reasons": reasons,
            }
        )
        return _response(body, "authoring-reference", evidence_id)
