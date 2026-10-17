"""Actual business result facts and FR011 projections, no paid analysis or qualification grants."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, connection, etag
from packages.metrics import results, views
from packages.metrics.contracts import validate

router = APIRouter(prefix="/api/v1", tags=["business results and metrics"])


def _command(c, r, actor, kind, target, body):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match"),
    )


def _saved(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


@router.get("/bids/{bid_id}/submissions/{submission_id}/business-results")
def business_results(
    bid_id: UUID,
    submission_id: UUID,
    r: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = results.list_for_submission(c, bid_id, submission_id, limit=limit, offset=offset)
        return JSONResponse(
            value,
            headers={"ETag": etag("business-result-head", submission_id, value["head_revision"])},
        )


@router.post("/bids/{bid_id}/submissions/{submission_id}/business-results")
def register_result(bid_id: UUID, submission_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("BusinessResultRequest", body)
    with connection() as c:
        cmd = _command(c, r, actor, "business_result.register", submission_id, body)
        if cmd.replay:
            return _saved(cmd.replay, True)
        value, tag = results.register(
            c,
            bid=bid_id,
            submission_id=submission_id,
            actor=actor["member_id"],
            command=cmd.id,
            body=body,
            condition=r.headers.get("if-match"),
        )
        return _saved(cmd.finish(value, 201, {"ETag": tag}))


@router.get("/business-results/{result_id}")
def get_business_result(result_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = results.get(c, result_id)
        return JSONResponse(
            value,
            headers={
                "ETag": etag("business-result-head", value["submission_id"], value["head_revision"])
            },
        )


@router.post("/business-results/{result_id}/corrections")
def correct_business_result(result_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("BusinessResultCorrectionRequest", body)
    with connection() as c:
        cmd = _command(c, r, actor, "business_result.correct", result_id, body)
        if cmd.replay:
            return _saved(cmd.replay, True)
        previous = results.get(c, result_id)
        value, tag = results.register(
            c,
            bid=UUID(previous["bid_id"]),
            submission_id=UUID(previous["submission_id"]),
            actor=actor["member_id"],
            command=cmd.id,
            body=body,
            previous=result_id,
            condition=r.headers.get("if-match"),
        )
        return _saved(cmd.finish(value, 201, {"ETag": tag}))


@router.post("/business-results/{result_id}/verification-decisions")
def verify_business_result(result_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("BusinessResultVerificationRequest", body)
    with connection() as c:
        cmd = _command(c, r, actor, "business_result.verify", result_id, body)
        if cmd.replay:
            return _saved(cmd.replay, True)
        value, tag = results.verify(
            c,
            result_id=result_id,
            actor=actor["member_id"],
            command=cmd.id,
            body=body,
            condition=r.headers.get("if-match"),
        )
        return _saved(cmd.finish(value, 200, {"ETag": tag}))


@router.get("/metrics")
def get_metrics(
    r: Request,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
    as_of: datetime | None = None,
    bid_ids: list[UUID] | None = Query(None, max_length=20),
    verification_scope: str = Query("ledger", pattern="^(ledger|verified_only)$"),
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = views.metrics(
            c,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
            bid_ids=bid_ids,
            verification_scope=verification_scope,
        )
        return value


@router.get("/bids/{bid_id}/round-trends")
def get_round_trends(
    bid_id: UUID, r: Request, limit: int = Query(30, ge=1, le=100), after: UUID | None = None
):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return views.round_trends(c, bid_id, limit=limit, after=after)
