"""Actual immutable registry and scoped shadow-route control, no paid execution."""

from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, clean, connection
from packages.model_governance import registry
from packages.model_governance.repository import match

router = APIRouter(prefix="/api/v1/model-registry", tags=["model-registry"])
Lane = Literal["shadow", "business"]


class RegisterModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    training_job_id: UUID
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("请填写具体原因")
        return value


class RouteChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: UUID | None = None
    certification_id: UUID | None = None
    action: Literal["publish", "rollback", "suspend"]
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("请填写具体原因")
        return value


def _reply(value, tag):
    return JSONResponse(clean(value), headers={"ETag": tag})


def _result(result, replay=False):
    return JSONResponse(
        result["body"],
        status_code=result["status"],
        headers=result["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


def _headers(request):
    if not request.headers.get("idempotency-key"):
        raise DomainError("IDEMPOTENCY_REQUIRED", "请使用稳定幂等键提交命令", 428)
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前冻结资源", 428)


def _command(c, request, actor, kind, target, value):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        value,
        request.headers.get("if-match"),
    )


@router.get("/capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        ready = registry.configured(c)
    return {
        "schema_configured": ready,
        "immutable_registration": ready,
        "shadow_route_control": ready,
        "current_review_shadow_producer": False,
        "formal_model_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }


@router.get("/training-jobs/{training_job_id}/model-preview")
def model_preview(training_job_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = registry.preview(c, training_job_id)
    return _reply(value, tag)


@router.post("/models")
def register_model(request: Request, body: RegisterModelRequest):
    actor = auth(request, True)
    _headers(request)
    value = body.model_dump(mode="json")
    with connection() as c:
        preview, tag = registry.preview(c, body.training_job_id)
        old = c.execute(
            "SELECT response FROM commands WHERE actor_id=%s AND command_kind='register-score-model' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], body.training_job_id, request.headers.get("idempotency-key")),
        ).fetchone()
        if old and old["response"]:
            cmd = _command(c, request, actor, "register-score-model", body.training_job_id, value)
            # Rechecking preview above proves the same frozen sources still qualify for new use.
            return _result(cmd.replay, True)
        match(request.headers.get("if-match"), tag)
    staged = registry.stage(preview["document"])
    with connection() as c:
        # Before command locks, repeat full source scope and current generation validation.
        latest, latest_tag = registry.preview(c, body.training_job_id)
        cmd = _command(c, request, actor, "register-score-model", body.training_job_id, value)
        if cmd.replay:
            return _result(cmd.replay, True)
        result, result_tag = registry.publish(
            c,
            actor=actor["member_id"],
            training_job_id=body.training_job_id,
            document=latest["document"],
            staged=staged,
            condition=request.headers.get("if-match"),
            reason=body.reason,
            command=cmd.id,
        )
        # publish also compares the staged original document with the freshly derived document.
        match(latest_tag, tag)
        return _result(cmd.finish(result, 201, {"ETag": result_tag}))


@router.get("/models/{model_id}")
def get_model(model_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        stored = registry.blob(c, model_id)
    registry.read(stored)  # actual bytes checked outside every SQL transaction
    with connection() as c:
        value, tag = registry.view(c, model_id)
    return _reply(value, tag)


def _key(value):
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise DomainError("MODEL_ROUTE_KEY_INVALID", "请选择候选的服务器范围路由", 422)
    return value


@router.get("/routes/{lane}/{route_key}")
def get_route(lane: Lane, route_key: str, request: Request):
    auth(request)
    with connection() as c:
        value, tag = registry.route(c, _key(route_key), lane)
    return _reply(value, tag)


@router.post("/routes/{lane}/{route_key}/changes")
def change_route(lane: Lane, route_key: str, request: Request, body: RouteChange):
    actor = auth(request, True)
    _headers(request)
    key = _key(route_key)
    value = body.model_dump(mode="json")
    target = uuid5(UUID("7cd8e5d3-3ca3-4ccd-a647-d3878235d124"), f"{lane}:{key}")
    # Candidate bytes must be deployably readable before taking source/route locks.
    if body.model_id:
        with connection() as c:
            stored = registry.blob(c, body.model_id)
        registry.read(stored)
    with connection() as c:
        registry.require(c)
        c.execute("SELECT pg_advisory_xact_lock(91143,1)")
        old = c.execute(
            "SELECT model_id,rollback_model_id FROM model_release.routes WHERE route_key=%s AND lane=%s",
            (key, lane),
        ).fetchone()
        c.execute(
            "SELECT model_release.lock_scope(%s)",
            ([body.model_id, *(old.values() if old else [])],),
        )
        current, tag = registry.route(c, key, lane)
        cmd = _command(c, request, actor, "change-model-route", target, value)
        if cmd.replay:
            # An old command is historical once this route has changed again. Do not return
            # its former active projection as the current route.
            match(tag, cmd.replay["headers"]["ETag"])
            if body.model_id:
                candidate, _ = registry.view(c, body.model_id)
                if not candidate["current"]:
                    raise DomainError("MODEL_SOURCE_STALE", "模型依赖已失效，不能重放使用", 409)
            return _result(cmd.replay, True)
        result, result_tag = registry.change_route(
            c,
            key=key,
            lane=lane,
            model_id=body.model_id,
            certification_id=body.certification_id,
            action=body.action,
            actor=actor["member_id"],
            reason=body.reason,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return _result(cmd.finish(result, 200, {"ETag": result_tag}))
