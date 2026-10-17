"""Independent technical denominator registry, no score/model execution effect."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection
from packages.model_governance import scale_repository as repo
from packages.model_governance.scale_evidence import SUPPORTED_SCALE_DESCRIPTIONS
from packages.model_governance.scale_requests import (
    TechnicalScaleCorrection,
    TechnicalScaleRequest,
    TechnicalScaleWithdrawal,
)

router = APIRouter(prefix="/api/v1", tags=["technical scale"])


def reply(value, tag):
    return JSONResponse(value, headers={"ETag": tag})


def result(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


def command(c, request, actor, kind, target, body):
    if not request.headers.get("if-match") or not request.headers.get("idempotency-key"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前满分用途上下文并提供幂等键", 428)
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body.model_dump(mode="json"),
        request.headers.get("if-match"),
    )


@router.get("/technical-scale-capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        ready = repo.configured(c)
    return {
        "schema_configured": ready,
        "sourced_denominator_registration": ready,
        "supported_declaration_method": "explicit-whole-technical-total-and-scale/1",
        "supported_scale_descriptions": list(SUPPORTED_SCALE_DESCRIPTIONS),
        "executable_aggregation": False,
        "score_input_integration": False,
        "formal_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }


@router.get("/bids/{bid_id}/technical-scale-context")
def context(
    bid_id: UUID,
    request: Request,
    purpose: Literal["current_prebid", "historical_backtest"] = "current_prebid",
    submission_id: UUID | None = None,
    material_snapshot_id: UUID | None = None,
):
    auth(request)
    with connection() as c:
        value, tag = repo.context(
            c,
            bid_id,
            purpose=purpose,
            submission_id=submission_id,
            material_snapshot_id=material_snapshot_id,
        )
    return reply(value, tag)


def replay(c, cmd):
    value, tag = repo.view(c, cmd.replay["body"]["id"])
    if not value["is_current_head"] or (
        cmd.replay["body"]["current_applicability"] and not value["current_applicability"]
    ):
        raise DomainError(
            "TECHNICAL_SCALE_STALE", "旧满分用途头或原依据已变化，不能重放确认状态", 409
        )
    repo.match(tag, cmd.replay["headers"]["ETag"])
    return result(cmd.replay, True)


def save(request, body, bid, base=None):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    target = base or bid
    kind = "technical-scale-correction" if base else "technical-scale-register"
    with connection() as c:
        repo.lock(c, bid)
        existing = c.execute(
            "SELECT response FROM commands WHERE actor_id=%s AND command_kind=%s AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], kind, target, request.headers.get("idempotency-key")),
        ).fetchone()
        if existing and existing["response"]:
            cmd = command(c, request, actor, kind, target, body)
            return replay(c, cmd)
        if base:
            old, old_tag = repo.view(c, base)
            repo.match(request.headers.get("if-match"), old_tag)
            if not old["is_current_head"] or body.base_version_id != base:
                raise DomainError(
                    "TECHNICAL_SCALE_REVISION_CONFLICT", "请更正当前满分用途版本", 412
                )
            _, condition = repo.context(
                c,
                bid,
                purpose=value["context"]["purpose"],
                submission_id=body.context.submission_id,
                material_snapshot_id=body.context.material_snapshot_id,
            )
        else:
            condition = request.headers.get("if-match")
        prepared = repo.capture(c, bid, value, condition, base)
    repo.verify_bytes(prepared)  # no SQL connection, graph lock or receipt across storage HTTP
    with connection() as c:
        repo.lock(c, bid)
        if base:
            _, tag = repo.view(c, base)
            repo.match(request.headers.get("if-match"), tag)
        cmd = command(c, request, actor, kind, target, body)
        if cmd.replay:
            return replay(c, cmd)
        saved, tag = repo.publish(
            c,
            bid=bid,
            prepared=prepared,
            condition=condition,
            actor=actor["member_id"],
            command=cmd.id,
        )
        return result(cmd.finish(saved, 201, {"ETag": tag}))


@router.post("/bids/{bid_id}/technical-scales")
def register(bid_id: UUID, request: Request, body: TechnicalScaleRequest):
    return save(request, body, bid_id)


@router.get("/bids/{bid_id}/technical-scales")
def list_versions(bid_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        repo.lock(c, bid_id)
        rows = c.execute(
            "SELECT id FROM technical_scale.versions WHERE bid_id=%s ORDER BY created_at DESC,id DESC LIMIT 200",
            (bid_id,),
        ).fetchall()
        versions = [repo.view(c, row["id"])[0] for row in rows]
    return {"items": versions, "limit": 200, "formal_eligibility": "unavailable"}


@router.get("/technical-scales/{scale_id}")
def get(scale_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.view(c, scale_id)
    return reply(value, tag)


@router.post("/technical-scales/{scale_id}/corrections")
def correct(scale_id: UUID, request: Request, body: TechnicalScaleCorrection):
    auth(request, True)
    with connection() as c:
        old, _ = repo.view(c, scale_id)
    return save(request, body, UUID(old["bid_id"]), scale_id)


@router.post("/technical-scales/{scale_id}/withdrawals")
def withdraw(scale_id: UUID, request: Request, body: TechnicalScaleWithdrawal):
    actor = auth(request, True)
    with connection() as c:
        value, tag = repo.view(c, scale_id)
        cmd = command(c, request, actor, "technical-scale-withdraw", scale_id, body)
        if cmd.replay:
            return replay(c, cmd)
        repo.match(request.headers.get("if-match"), tag)
        if not value["is_current_head"]:
            raise DomainError("TECHNICAL_SCALE_REVISION_CONFLICT", "请撤回当前用途满分版本", 412)
        repo.rpc(
            c,
            "SELECT technical_scale.withdraw(%s,%s,%s,%s,%s)",
            (scale_id, value["head_revision"], body.reason, actor["member_id"], cmd.id),
        )
        saved, tag = repo.view(c, scale_id)
        return result(cmd.finish(saved, 200, {"ETag": tag}))
