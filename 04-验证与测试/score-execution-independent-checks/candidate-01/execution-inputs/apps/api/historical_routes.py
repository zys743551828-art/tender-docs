"""Historical proof/subject commands only; retrospective input execution remains unavailable."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, clean, connection
from packages.model_governance import historical_repository as repo
from packages.model_governance.historical_requests import (
    HistoricalConfirmationRequest,
    HistoricalSubjectRequest,
    HistorySourceClaimRequest,
)

router = APIRouter(prefix="/api/v1/historical-proof", tags=["historical-proof"])


def reply(value, tag):
    return JSONResponse(clean(value), headers={"ETag": tag})


def result(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


def command(c, actor, request, kind, target, value):
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前历史依据上下文", 428)
    return Command(
        c,
        actor["member_id"],
        "historical-" + kind,
        target,
        request.headers.get("idempotency-key"),
        value,
        request.headers.get("if-match"),
    )


@router.get("/capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        ready = repo.configured(c)
    return {
        "schema_configured": ready,
        "proof_and_subject_commands": ready,
        "historical_input_execution": False,
        "formal_model_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }


@router.get("/submissions/{submission_id}/context")
def get_context(submission_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.context(c, submission_id)
    return reply(value, tag)


def save(request, body, kind):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        repo.context(c, body.submission_id)  # leading full scope before command row locks
        cmd = command(c, actor, request, "save-" + kind, body.submission_id, value)
        if cmd.replay:
            existing, tag = repo.view(c, kind, cmd.replay["body"]["id"])
            if not existing["is_current_head"]:
                raise DomainError(
                    "HISTORICAL_AUTHORITY_STALE", "已保存证明被新版本替代，请读取当前版本", 409
                )
            if existing["proof_refs"] and not existing["proofs_current"]:
                raise DomainError(
                    "HISTORICAL_AUTHORITY_STALE", "历史依据已变化，不能重放保存状态", 409
                )
            repo.match(tag, cmd.replay["headers"]["ETag"])
            return result(cmd.replay, True)
        function = repo.save_subject if kind == "subject" else repo.save_claim
        saved, tag = function(
            c,
            actor=actor["member_id"],
            body=value,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return result(cmd.finish(saved, 201, {"ETag": tag}))


@router.post("/subjects")
def save_subject(request: Request, body: HistoricalSubjectRequest):
    return save(request, body, "subject")


@router.post("/source-claims")
def save_claim(request: Request, body: HistorySourceClaimRequest):
    return save(request, body, "claim")


@router.get("/subjects/{subject_id}")
def get_subject(subject_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.view(c, "subject", subject_id)
    return reply(value, tag)


@router.get("/source-claims/{claim_id}")
def get_claim(claim_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.view(c, "claim", claim_id)
    return reply(value, tag)


def confirm(request, body, kind, identity):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        current, tag = repo.view(c, kind, identity)
        cmd = command(c, actor, request, "confirm-" + kind, identity, value)
        if cmd.replay:
            if (
                not current["is_current_head"]
                or not current["proofs_current"]
                or current["manual_decision"] != "confirmed"
            ):
                raise DomainError(
                    "HISTORICAL_AUTHORITY_STALE", "确认依据或有效头已变化，不能重放旧确认", 409
                )
            if kind == "subject" and not current["subject_relationship_current"]:
                raise DomainError("HISTORICAL_AUTHORITY_STALE", "历史主体关系已不成立", 409)
            if (
                kind == "claim"
                and any(
                    fact["fact_kind"] in {"known_to_bidder", "received_by_bidder"}
                    and fact["claimed_at"] is not None
                    for fact in current["facts"]
                )
                and not current["related_subject_current"]
            ):
                raise DomainError(
                    "HISTORICAL_AUTHORITY_STALE", "本方历史可见事实的主体证明已变化", 409
                )
            repo.match(tag, cmd.replay["headers"]["ETag"])
            return result(cmd.replay, True)
        confirmed, tag = repo.confirm(
            c,
            kind=kind,
            identity=identity,
            actor=actor["member_id"],
            reason=body.reason,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return result(cmd.finish(confirmed, 200, {"ETag": tag}))


@router.post("/subjects/{subject_id}/confirm")
def confirm_subject(subject_id: UUID, request: Request, body: HistoricalConfirmationRequest):
    return confirm(request, body, "subject", subject_id)


@router.post("/source-claims/{claim_id}/confirm")
def confirm_claim(claim_id: UUID, request: Request, body: HistoricalConfirmationRequest):
    return confirm(request, body, "claim", claim_id)
