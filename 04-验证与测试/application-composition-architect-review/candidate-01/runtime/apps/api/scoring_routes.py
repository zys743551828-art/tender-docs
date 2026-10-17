"""Dedicated complete-judgment inputs/jobs. No paid HTTP execution endpoint or consumer."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection
from packages.model_governance import historical_input
from packages.model_governance import scoring_repository as repo

router = APIRouter(prefix="/api/v1", tags=["complete scoring"])


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请填写具体原因")
        return value


class ScoreInputRequest(Closed):
    review_id: UUID | None = None
    historical_input_id: UUID | None = None

    @model_validator(mode="after")
    def one_actual_input(self):
        if (self.review_id is None) == (self.historical_input_id is None):
            raise ValueError("请选择当前评审或已冻结历史输入中的一种")
        return self


class ScoreJobRequest(Closed):
    budget: str = Field(strict=True, pattern=r"^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,12})?$")


class ScoreJobChange(Closed):
    action: Literal["pause", "resume", "cancel"]


def reply(value, tag):
    return JSONResponse(value, headers={"ETag": tag})


def result(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


def headers(request):
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 128:
        raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前完整评分上下文", 428)


def command(c, request, actor, kind, target, body):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body.model_dump(mode="json"),
        request.headers.get("if-match"),
    )


@router.get("/scoring-capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        ready = repo.configured(c)
    return {
        "schema_configured": ready,
        "complete_judgment_registration": ready,
        "score_dispatch_configured": False,
        "paid_execution_enabled": False,
        "formal_eligibility": "unavailable",
        "numeric_prediction_available": False,
    }


@router.get("/bids/{bid_id}/score-context")
def context(bid_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.context(c, bid_id)
    return reply(value, tag)


@router.post("/bids/{bid_id}/score-inputs")
def create_input(bid_id: UUID, request: Request, body: ScoreInputRequest):
    actor = auth(request, True)
    headers(request)
    with connection() as c:
        repo.lock(c, bid_id)
        old = c.execute(
            "SELECT response FROM commands WHERE actor_id=%s AND command_kind='score-input-freeze' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], bid_id, request.headers.get("idempotency-key")),
        ).fetchone()
        if old and old["response"]:
            current, tag = repo.input_view(c, old["response"]["body"]["id"])
            if not current["current"]:
                raise DomainError("SCORE_INPUT_STALE", "旧评分输入已失效，不能重放有效状态", 409)
            cmd = command(c, request, actor, "score-input-freeze", bid_id, body)
            repo.match(tag, cmd.replay["headers"]["ETag"])
            return result(cmd.replay, True)
    if body.review_id is not None:
        with connection() as c:
            metadata = repo.root_metadata(c, bid_id, body.review_id)
        root, root_content = repo.read_root(metadata)
        with connection() as c:
            prepared = repo.prepare_current(
                c,
                bid=bid_id,
                review=body.review_id,
                root=root,
                root_content=root_content,
                metadata=metadata,
                condition=request.headers.get("if-match"),
            )
    else:
        with connection() as c:
            snapshot = historical_input.metadata(c, body.historical_input_id)
        historical_input.verify_bytes(snapshot)
        with connection() as c:
            prepared = repo.prepare_historical(
                c,
                bid=bid_id,
                identity=body.historical_input_id,
                condition=request.headers.get("if-match"),
            )
    staged = repo.stage(prepared)
    with connection() as c:
        repo.lock(c, bid_id)
        cmd = command(c, request, actor, "score-input-freeze", bid_id, body)
        if cmd.replay:
            value, tag = repo.input_view(c, cmd.replay["body"]["id"])
            if not value["current"]:
                raise DomainError("SCORE_INPUT_STALE", "并发已登记输入已失效", 409)
            repo.match(tag, cmd.replay["headers"]["ETag"])
            return result(cmd.replay, True)
        value, tag = repo.publish(
            c,
            prepared=staged,
            actor=actor["member_id"],
            condition=request.headers.get("if-match"),
            reason=body.reason,
            command=cmd.id,
        )
        return result(cmd.finish(value, 201, {"ETag": tag}))


@router.get("/score-inputs/{input_id}")
def get_input(input_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.input_view(c, input_id)
    return reply(value, tag)


@router.post("/score-inputs/{input_id}/jobs")
def start_job(input_id: UUID, request: Request, body: ScoreJobRequest):
    actor = auth(request, True)
    headers(request)
    with connection() as c:
        repo.input_view(c, input_id)  # leading full scope before receipt lock
        cmd = command(c, request, actor, "score-job-start", input_id, body)
        if cmd.replay:
            current, tag = repo.job_view(c, cmd.replay["body"]["id"])
            if not current["source_current"]:
                raise DomainError("SCORE_INPUT_STALE", "原执行依赖已失效", 409)
            # Old job ACK remains its original immutable command receipt; current state
            # is read separately and never reset/requeued by replay.
            return result(cmd.replay, True)
        value, tag = repo.start(
            c,
            identity=input_id,
            actor=actor["member_id"],
            budget=body.budget,
            reason=body.reason,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return result(cmd.finish(value, 201, {"ETag": tag}))


@router.get("/score-jobs/{job_id}")
def get_job(job_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.job_view(c, job_id)
    return reply(value, tag)


@router.post("/score-jobs/{job_id}/state")
def change_job(job_id: UUID, request: Request, body: ScoreJobChange):
    actor = auth(request, True)
    headers(request)
    with connection() as c:
        current, tag = repo.job_view(c, job_id)
        cmd = command(c, request, actor, "score-job-state", job_id, body)
        if cmd.replay:
            return result(cmd.replay, True)
        repo.match(request.headers.get("if-match"), tag)
        if body.action == "resume":
            repo.assert_work_writable(c, current["bid_id"])
        repo.rpc(
            c,
            "SELECT score_execution.change_job(%s,%s,%s,%s,%s)",
            (job_id, current["generation"], body.action, actor["member_id"], body.reason),
        )
        value, tag = repo.job_view(c, job_id)
        return result(cmd.finish(value, 200, {"ETag": tag}))


class ShadowRequest(Closed):
    model_id: UUID


@router.get("/score-jobs/{job_id}/result")
def get_result(job_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.result_view(c, job_id)
    return reply(value, tag)


@router.get("/score-jobs/{job_id}/shadow-context")
def get_shadow_context(job_id: UUID, model_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.shadow_context(c, job_id, model_id)
    return reply(value, tag)


@router.post("/score-jobs/{job_id}/shadows")
def save_shadow(job_id: UUID, request: Request, body: ShadowRequest):
    from packages.model_governance import registry

    actor = auth(request, True)
    headers(request)
    with connection() as c:
        repo.shadow_context(c, job_id, body.model_id)
        old = c.execute(
            "SELECT response FROM commands WHERE actor_id=%s AND command_kind='score-shadow-save' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], job_id, request.headers.get("idempotency-key")),
        ).fetchone()
    if old and old["response"]:
        with connection() as c:
            current, tag = repo.shadow_context(c, job_id, body.model_id)
            if not all(
                current[key] for key in ("source_current", "model_current", "route_current")
            ):
                raise DomainError(
                    "SCORE_SHADOW_STALE", "旧影子记录的当前资格已变化，历史仍可读取", 409
                )
            repo.match(request.headers.get("if-match"), tag)
            cmd = command(c, request, actor, "score-shadow-save", job_id, body)
            return result(cmd.replay, True)
    with connection() as c:
        metadata = registry.blob(c, body.model_id)
    registry.read(metadata)  # actual inert model bytes, never under SQL/graph locks
    with connection() as c:
        current, current_tag = repo.shadow_context(
            c, job_id, body.model_id
        )  # full union scope before receipt lock
        cmd = command(c, request, actor, "score-shadow-save", job_id, body)
        if cmd.replay:
            if not all(
                current[key] for key in ("source_current", "model_current", "route_current")
            ):
                raise DomainError("SCORE_SHADOW_STALE", "并发保存的影子资格已变化", 409)
            repo.match(request.headers.get("if-match"), current_tag)
            return result(cmd.replay, True)
        value, tag = repo.persist_shadow(
            c,
            job_id=job_id,
            model_id=body.model_id,
            actor=actor["member_id"],
            condition=request.headers.get("if-match"),
            reason=body.reason,
            command=cmd.id,
            artifact_digest=metadata["digest"],
        )
        return result(cmd.finish(value, 201, {"ETag": tag}))


@router.get("/score-shadows/{shadow_id}")
def get_shadow(shadow_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.shadow_view(c, shadow_id)
    return reply(value, tag)
