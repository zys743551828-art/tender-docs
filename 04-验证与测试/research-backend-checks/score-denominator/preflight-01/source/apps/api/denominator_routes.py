"""Explicit sourced-denominator/derived coverage API; never calls a provider."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection
from packages.model_governance import denominator_repository as repo
from packages.model_governance.repository import match

router = APIRouter(prefix="/api/v1", tags=["sourced score denominator"])


class DenominatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请说明本次引用或派生用途")
        return value


class CoverageRequest(DenominatorRequest):
    job_id: UUID


def response(value, tag, status=200):
    return JSONResponse(value, status_code=status, headers={"ETag": tag})


def command(c, request, actor, kind, target, body, sample=None):
    if not request.headers.get("if-match") or not request.headers.get("idempotency-key"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前完整对象与分母上下文", 428)
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body.model_dump(mode="json") | {"sample_id": str(sample) if sample else None},
        request.headers.get("if-match"),
    )


def replay(c, cmd, *, derived=False):
    saved = cmd.replay
    value, tag = (repo.result_view if derived else repo.view)(c, saved["body"]["id"])
    if not value["current"]:
        raise DomainError(
            "SCORE_DENOMINATOR_STALE", "旧引用或派生快照已失去当前资格，仍保留只读历史", 409
        )
    match(saved["headers"]["ETag"], tag)
    return JSONResponse(
        saved["body"],
        status_code=saved["status"],
        headers=saved["headers"] | {"Idempotency-Replayed": "true"},
    )


def read_context(kind, target, request, sample=None):
    auth(request)
    with connection() as c:
        value, tag = repo.context(c, kind, target, sample)
    return response(value, tag)


def save(kind, target, request, body, sample=None):
    actor = auth(request, True)
    with connection() as c:
        repo.require(c)
        cmd = command(c, request, actor, "score-denominator-" + kind, target, body, sample)
        if cmd.replay:
            return replay(c, cmd)
        value, tag = repo.freeze(
            c,
            kind=kind,
            target=target,
            sample=sample,
            actor=actor["member_id"],
            condition=request.headers.get("if-match"),
        )
        saved = cmd.finish(value, 201, {"ETag": tag})
        return JSONResponse(saved["body"], status_code=saved["status"], headers=saved["headers"])


@router.get("/score-inputs/{input_id}/denominator-context")
def score_context(input_id: UUID, request: Request):
    return read_context("score_input", input_id, request)


@router.post("/score-inputs/{input_id}/denominators")
def score_save(input_id: UUID, request: Request, body: DenominatorRequest):
    return save("score_input", input_id, request, body)


@router.get("/historical-inputs/{input_id}/denominator-context")
def history_context(input_id: UUID, request: Request):
    return read_context("historical_input", input_id, request)


@router.post("/historical-inputs/{input_id}/denominators")
def history_save(input_id: UUID, request: Request, body: DenominatorRequest):
    return save("historical_input", input_id, request, body)


@router.get("/model-governance/cohorts/{cohort_id}/members/{sample_id}/denominator-context")
def stagea_context(cohort_id: UUID, sample_id: UUID, request: Request):
    return read_context("stagea_member", cohort_id, request, sample_id)


@router.post("/model-governance/cohorts/{cohort_id}/members/{sample_id}/denominators")
def stagea_save(cohort_id: UUID, sample_id: UUID, request: Request, body: DenominatorRequest):
    return save("stagea_member", cohort_id, request, body, sample_id)


@router.get("/score-denominators/{binding_id}")
def get_binding(binding_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.view(c, binding_id)
    return response(value, tag)


@router.post("/score-denominators/{binding_id}/coverages")
def derive(binding_id: UUID, request: Request, body: CoverageRequest):
    actor = auth(request, True)
    with connection() as c:
        _, tag = repo.view(c, binding_id)
        match(request.headers.get("if-match"), tag)
        frozen = repo.snapshot(c, binding_id, body.job_id)
    payload = repo.compute(frozen)  # no DB connection/lock across feature arithmetic
    with connection() as c:
        cmd = command(c, request, actor, "sourced-score-coverage", binding_id, body)
        if cmd.replay:
            return replay(c, cmd, derived=True)
        _, tag = repo.view(c, binding_id)
        match(request.headers.get("if-match"), tag)
        value, tag = repo.publish(
            c, binding_id=binding_id, job_id=body.job_id, payload=payload, actor=actor["member_id"]
        )
        saved = cmd.finish(value, 201, {"ETag": tag})
        return JSONResponse(saved["body"], status_code=saved["status"], headers=saved["headers"])


@router.get("/score-coverages/{coverage_id}")
def get_coverage(coverage_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.result_view(c, coverage_id)
    return response(value, tag)
