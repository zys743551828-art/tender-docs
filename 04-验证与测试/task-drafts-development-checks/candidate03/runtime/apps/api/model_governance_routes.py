"""FR029 actual software APIs; no paid execution and no qualification flag accepted."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, clean, connection, etag, sha
from packages.model_governance import intake, repository
from packages.model_governance.common import GovernanceError, digest
from packages.model_governance.features import SCHEMA

router = APIRouter(prefix="/api/v1/model-governance", tags=["model-governance"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


Family = Literal["mean", "median", "method_shrinkage", "raw_band", "ridge", "isotonic"]


class TrainingProtocol(StrictBody):
    schema_name: Literal["score-training-protocol/1"] = Field(alias="schema")
    feature_schema: Literal["complete-three-band-features/1"]
    families: list[Family] = Field(min_length=1, max_length=6)
    ridge_alphas: list[float] = Field(min_length=1, max_length=30)
    shrinkage_values: list[float] = Field(min_length=1, max_length=30)
    fold_count: StrictInt = Field(ge=2, le=20)
    seed: StrictInt
    tie_order: list[Family] = Field(min_length=1, max_length=6)
    refit_with_development: bool
    interval_levels: list[float] = Field(min_length=2, max_length=2)
    bootstrap_iterations: StrictInt = Field(ge=100, le=10000)
    selection_metric: Literal["project_mae"]


class ProtocolRequest(StrictBody):
    document: TrainingProtocol
    reason: str = Field(min_length=1, max_length=4000)


class CohortRequest(StrictBody):
    research_run_id: UUID
    protocol_id: UUID
    repeat_index: StrictInt = Field(ge=1, le=10)
    calibration_group_ids: list[UUID] = Field(max_length=1000)
    reason: str = Field(min_length=1, max_length=4000)


class TrainingJobRequest(StrictBody):
    cohort_id: UUID
    reason: str = Field(min_length=1, max_length=4000)


def reply(value, tag=None, *, status=200):
    return JSONResponse(clean(value), status_code=status, headers={"ETag": tag} if tag else {})


def command(c, request, actor, kind, target, value, *, condition=False):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        value,
        request.headers.get("if-match") if condition else None,
    )


def command_reply(result, replay=False):
    return JSONResponse(
        result["body"],
        status_code=result["status"],
        headers=result["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


@router.get("/capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        configured = repository.configured(c)
    return {
        "schema_configured": configured,
        "feature_schema": SCHEMA,
        "software_training": configured,
        "formal_model_eligibility": "unavailable",
        "synthetic_permanent_research_only": True,
        "paid_execution_enabled": False,
        "scoring_worker_configured": False,
        "reasons": ["独立真实标签、业务门槛、观察与发布验收尚未完成"],
    }


@router.post("/training-protocols")
def create_protocol(request: Request, body: ProtocolRequest):
    actor = auth(request, True)
    value = body.model_dump(mode="json", by_alias=True)
    with connection() as c:
        repository.require(c)
        cmd = command(
            c,
            request,
            actor,
            "model-training-protocol",
            UUID("d94a3878-d452-410a-898e-85a77370059d"),
            value,
        )
        if cmd.replay:
            return command_reply(cmd.replay, True)
        result, tag = repository.register_protocol(
            c,
            actor=actor["member_id"],
            document=value["document"],
            reason=value["reason"],
            command=cmd.id,
        )
        return command_reply(cmd.finish(result, 201, {"ETag": tag}))


@router.get("/training-protocols/{protocol_id}")
def get_protocol(protocol_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repository.protocol_view(c, protocol_id)
    return reply(value, tag)


def prepare_cohort(body):
    with connection() as c:
        repository.require(c)
        protocol, _ = repository.protocol_view(c, body["protocol_id"])
        snapshot = intake.metadata(c, body["research_run_id"], body["repeat_index"])
    materials = intake.read_materials(snapshot)
    with connection() as c:
        metadata = intake.package_metadata(c, materials["dataset"])
    packages = intake.read_packages(metadata)
    with connection() as c:
        records = intake.capture(c, snapshot=snapshot, materials=materials, packages=packages)
    # Pure feature construction outside all database locks and transactions.
    try:
        exports = intake.construct_exports(
            origin=protocol["origin"],
            protocol=protocol["document"],
            materials=materials,
            packages=packages,
            calibration_group_ids=body["calibration_group_ids"],
        )
    except GovernanceError as error:
        raise DomainError(error.code, str(error), 422) from None
    preview = {
        "research_run_id": body["research_run_id"],
        "protocol_id": body["protocol_id"],
        "repeat_index": body["repeat_index"],
        "calibration_group_ids": body["calibration_group_ids"],
        "source_digest": digest(records),
        "export_digests": {key: digest(value) for key, value in exports.items()},
        "member_counts": {
            purpose: sum(row["purpose"] == purpose for row in exports["training"]["members"])
            for purpose in ("fit", "development", "calibration", "final", "excluded")
        },
        "research_only": True,
        "formal_eligibility": "unavailable",
        "external_model_calls": 0,
    }
    tag = etag("model-cohort-preview", body["research_run_id"], sha(preview))
    return preview, tag, snapshot, materials, packages, exports, records


@router.post("/cohort-previews")
def preview_cohort(request: Request, body: CohortRequest):
    auth(request, True)
    preview, tag, *_ = prepare_cohort(body.model_dump(mode="json"))
    return reply(preview, tag)


@router.post("/cohorts")
def create_cohort(request: Request, body: CohortRequest):
    actor = auth(request, True)
    if not request.headers.get("idempotency-key") or not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "冻结须有预览ETag和操作幂等键", 428)
    value = body.model_dump(mode="json")
    _, tag, snapshot, materials, packages, exports, records = prepare_cohort(value)
    with connection() as c:
        # Same authoritative server capture under fresh grouping/bid/graph locks before effect.
        current = intake.capture(c, snapshot=snapshot, materials=materials, packages=packages)
        if digest(current) != digest(records):
            raise DomainError("MODEL_COHORT_CHANGED", "冻结计算期间的来源或标签身份已变化", 409)
        repository.match(request.headers.get("if-match"), tag)
        cmd = command(
            c, request, actor, "model-cohort-freeze", body.research_run_id, value, condition=True
        )
        if cmd.replay:
            old, _ = repository.cohort_view(c, cmd.replay["body"]["id"])
            if not old["current"]:
                raise DomainError("MODEL_COHORT_STALE", "旧冻结来源已失效，不能写重放", 409)
            return command_reply(cmd.replay, True)
        result, current_tag = repository.publish_cohort(
            c,
            actor=actor["member_id"],
            run_id=body.research_run_id,
            protocol_id=body.protocol_id,
            exports=exports,
            records=current,
            condition=tag,
            preview_tag=tag,
            command=cmd.id,
        )
        return command_reply(cmd.finish(result, 201, {"ETag": current_tag}))


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repository.cohort_view(c, cohort_id)
    return reply(value, tag)


@router.post("/training-jobs")
def create_training_job(request: Request, body: TrainingJobRequest):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        repository.require(c)
        view, _ = repository.cohort_view(c, body.cohort_id)
        if not view["current"]:
            raise DomainError("MODEL_COHORT_STALE", "来源已失效，不能新建或重放训练命令", 409)
        cmd = command(
            c, request, actor, "model-training-job", body.cohort_id, value, condition=True
        )
        if cmd.replay:
            return command_reply(cmd.replay, True)
        result, tag = repository.create_training_job(
            c,
            actor=actor["member_id"],
            cohort_id=body.cohort_id,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return command_reply(cmd.finish(result, 201, {"ETag": tag}))


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repository.job_view(c, job_id)
    return reply(value, tag)
