"""Actual historical results and controlled research APIs; include_router(router)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import auth
from packages.domain.runtime import Command, clean, connection, etag, fetch, validate
from packages.research import batches, chapters, importing, outcomes

router = APIRouter(prefix="/api/v1", tags=["research"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutcomeVerification(StrictBody):
    state: Literal["pending", "verified", "conflict", "rejected"]
    reason: str = Field(min_length=1, max_length=4000)
    identity: dict[str, str]
    aggregation: str = Field(max_length=200)
    published_at: datetime | None
    evidence_ids: list[UUID] = Field(max_length=100)
    material_snapshot_id: UUID | None
    effective_material_confirmed: bool


class DatasetRequest(StrictBody):
    sample_ids: list[UUID] = Field(min_length=1, max_length=1000)
    split: dict[str, Literal["train", "validation", "test", "excluded"]]
    cutoff: str


class GroupingRequest(StrictBody):
    template_key: str | None = Field(max_length=100)
    near_duplicate_key: str | None = Field(max_length=100)
    reviewed: bool
    reason: str = Field(min_length=1, max_length=4000)


class HistoricalImportRecord(StrictBody):
    external_record_id: str = Field(min_length=1, max_length=200)
    bid_id: UUID
    actual_document_version_id: UUID
    submitted_at: datetime
    evidence_ref: UUID
    explanation: str = Field(min_length=1, max_length=4000)
    score_kind: Literal["technical_total", "comprehensive_total", "unknown"]
    value: str | None = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    maximum: str | None = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    source_version_id: UUID | None
    known_at: datetime | None


class HistoricalImportRequest(StrictBody):
    records: list[HistoricalImportRecord] = Field(min_length=1, max_length=1000)


def response(result, replay=False):
    return JSONResponse(
        result["body"],
        status_code=result["status"],
        headers=result["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


def command(c, request, actor, kind, target, body, *, conditional=False):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match") if conditional else None,
    )


@router.get("/research/capabilities")
def capabilities(request: Request):
    auth(request)
    try:
        prices = batches.price_config()
        price = {"available": True, "digest": batches.sha(prices), "currency": prices["currency"]}
    except batches.DomainError:
        price = {"available": False, "digest": None, "currency": None}
    return {
        "historical_outcomes": True,
        "official_zero_is_valid": True,
        "unknown_is_not_zero": True,
        "batch_dry_run": True,
        "price": price,
        "formal_prediction": {
            "mode": "unavailable",
            "reasons": ["尚无正式成绩独立评测与业务发布证据"],
        },
        "model_training": "awaiting_verified_real_labels",
        "model_release": "unavailable",
        "requires_full_chapters": True,
        "judgments_per_item": 3,
    }


@router.get("/history")
def history(
    request: Request,
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    auth(request)
    with connection() as c:
        return outcomes.history(c, query=q, limit=limit, offset=offset)


@router.post("/research/import-previews")
def preview_import(request: Request, body: HistoricalImportRequest):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        cmd = command(
            c,
            request,
            actor,
            "preview-historical-import",
            UUID("59696bb3-834e-4768-80fb-5662402f6975"),
            value,
        )
        if cmd.replay:
            return response(cmd.replay, True)
        result = importing.preview(
            c, actor=actor["member_id"], records=value["records"], command=cmd.id
        )
        return response(
            cmd.finish(
                result, 201, {"ETag": etag("historical-import", result["id"], result["revision"])}
            )
        )


@router.get("/research/imports/{batch_id}")
def get_import(
    batch_id: UUID,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = importing.view(c, batch_id, limit=limit, offset=offset)
        return JSONResponse(
            value, headers={"ETag": etag("historical-import", batch_id, value["revision"])}
        )


@router.post("/research/imports/{batch_id}/confirm")
def confirm_import(batch_id: UUID, request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    if body != {}:
        raise batches.DomainError(
            "INVALID_REQUEST", "确认只使用本批预检查清单，修改资料须重新预检查", 422
        )
    with connection() as c:
        cmd = command(
            c, request, actor, "confirm-historical-import", batch_id, body, conditional=True
        )
        if cmd.replay:
            return response(cmd.replay, True)
        value = importing.confirm(
            c,
            actor=actor["member_id"],
            identity=batch_id,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(
            cmd.finish(
                value, headers={"ETag": etag("historical-import", batch_id, value["revision"])}
            )
        )


@router.get("/bids/{bid_id}/outcomes")
def list_outcomes(bid_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return outcomes.list_outcomes(c, bid_id)


@router.get("/bids/{bid_id}/outcome-context")
def outcome_context(bid_id: UUID, submission_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return outcomes.context(c, bid_id, submission_id)


@router.post("/bids/{bid_id}/outcomes")
def register_outcome(bid_id: UUID, request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("OutcomeRequest", body)
    with connection() as c:
        cmd = command(c, request, actor, "register-outcome", bid_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        view, tag = outcomes.register(
            c, actor=actor["member_id"], bid=bid_id, body=body, command=cmd.id
        )
        return response(
            cmd.finish(
                view,
                201,
                {"ETag": tag, "Verification-ETag": etag("outcome-verification", view["id"], 0)},
            )
        )


@router.get("/outcomes/{outcome_id}")
def get_outcome(outcome_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = fetch(c, "SELECT * FROM outcomes WHERE id=%s", (outcome_id,))
        view = outcomes.outcome_view(c, row)
        head = fetch(
            c, "SELECT * FROM outcome_heads WHERE submission_id=%s", (row["submission_id"],)
        )
        return JSONResponse(
            view | {"current": head["outcome_id"] == row["id"]},
            headers={
                "ETag": etag("outcome-verification", outcome_id, view["verification_revision"]),
                "Outcome-Head-ETag": etag("outcome-head", row["submission_id"], head["revision"]),
            },
        )


@router.post("/outcomes/{outcome_id}/corrections")
def correct_outcome(outcome_id: UUID, request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("OutcomeRequest", body)
    with connection() as c:
        cmd = command(c, request, actor, "correct-outcome", outcome_id, body, conditional=True)
        if cmd.replay:
            return response(cmd.replay, True)
        old = fetch(c, "SELECT * FROM outcomes WHERE id=%s", (outcome_id,))
        if str(old["submission_id"]) != body["submission_id"]:
            raise batches.DomainError(
                "OUTCOME_SUBMISSION_MISMATCH", "更正不能改变实际提交身份", 422
            )
        view, tag = outcomes.register(
            c,
            actor=actor["member_id"],
            bid=old["bid_id"],
            body=body,
            command=cmd.id,
            previous=outcome_id,
            condition=request.headers.get("if-match"),
        )
        return response(
            cmd.finish(
                view,
                201,
                {"ETag": tag, "Verification-ETag": etag("outcome-verification", view["id"], 0)},
            )
        )


@router.post("/outcomes/{outcome_id}/verification")
def verify_outcome(outcome_id: UUID, request: Request, body: OutcomeVerification):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        cmd = command(c, request, actor, "verify-outcome", outcome_id, value, conditional=True)
        if cmd.replay:
            return response(cmd.replay, True)
        view, tag = outcomes.verify(
            c,
            actor=actor["member_id"],
            outcome_id=outcome_id,
            body=value,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(cmd.finish(view, headers={"ETag": tag}))


@router.post("/bids/{bid_id}/submissions/{submission_id}/sample-admission")
def admit_sample(bid_id: UUID, submission_id: UUID, request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    if body != {}:
        raise batches.DomainError(
            "INVALID_REQUEST", "样本准入只核验既有正式提交与成绩，不接受外部标签", 422
        )
    with connection() as c:
        cmd = command(c, request, actor, "admit-sample", submission_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        return response(
            cmd.finish(
                outcomes.admit(
                    c,
                    bid=bid_id,
                    submission=submission_id,
                    actor=actor["member_id"],
                    command=cmd.id,
                )
            )
        )


@router.get("/research/samples")
def samples(
    request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)
):
    auth(request)
    with connection() as c:
        rows = c.execute(
            "SELECT s.*,h.outcome_id=s.outcome_id AS current FROM samples s JOIN outcome_heads h ON h.submission_id=s.submission_id ORDER BY s.id LIMIT %s OFFSET %s",
            (limit + 1, offset),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "next_offset": offset + limit if len(rows) > limit else None,
            "formal_model_eligible": False,
        }


@router.get("/research/samples/{sample_id}/grouping")
def get_grouping(sample_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = outcomes.grouping_view(c, sample_id)
        return JSONResponse(
            value, headers={"ETag": etag("sample-group", sample_id, value["revision"])}
        )


@router.post("/research/samples/{sample_id}/grouping")
def set_grouping(sample_id: UUID, request: Request, body: GroupingRequest):
    actor = auth(request, True)
    value = body.model_dump(mode="json")
    with connection() as c:
        cmd = command(
            c, request, actor, "record-sample-grouping", sample_id, value, conditional=True
        )
        if cmd.replay:
            return response(cmd.replay, True)
        result = outcomes.record_grouping(
            c,
            sample_id=sample_id,
            actor=actor["member_id"],
            body=value,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(
            cmd.finish(
                result, headers={"ETag": etag("sample-group", sample_id, result["revision"])}
            )
        )


@router.post("/research/datasets")
def freeze_dataset(request: Request, body: DatasetRequest):
    from datetime import datetime

    actor = auth(request, True)
    value = body.model_dump(mode="json")
    try:
        cutoff = datetime.fromisoformat(body.cutoff.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError
    except ValueError:
        raise batches.DomainError("INVALID_REQUEST", "请提供带时区的划分时间边界", 422) from None
    target = UUID("c18affc4-55c4-40d0-9a62-c9e990a967a1")
    with connection() as c:
        cmd = command(c, request, actor, "freeze-research-dataset", target, value)
        if cmd.replay:
            return response(cmd.replay, True)
        result = batches.freeze_dataset(
            c,
            actor=actor["member_id"],
            sample_ids=value["sample_ids"],
            split=body.split,
            cutoff=cutoff,
            command=cmd.id,
        )
        return response(cmd.finish(result, 201))


@router.post("/research/reviews/{review_id}/chapter-packages")
def prepare_chapters(review_id: UUID, request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    if body != {}:
        raise batches.DomainError(
            "INVALID_REQUEST", "章节仅从本轮冻结正文读取，不接受标签或聊天输入", 422
        )
    with connection() as c:
        cmd = command(c, request, actor, "prepare-research-chapters", review_id, body)
        if cmd.replay:
            return response(cmd.replay, True)
        value = chapters.prepare(c, actor=actor["member_id"], review_id=review_id, command=cmd.id)
        return response(cmd.finish(value, 201))


@router.post("/research/previews")
def preview(request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("ResearchPreviewRequest", body)
    with connection() as c:
        cmd = command(
            c, request, actor, "preview-research", UUID(body["dataset_manifest_blob_id"]), body
        )
        if cmd.replay:
            return response(cmd.replay, True)
        result = batches.preview(c, actor=actor["member_id"], request=body, command=cmd.id)
        validate("ResearchPreviewView", result)
        return response(cmd.finish(result, 201))


@router.post("/research/runs")
def start_run(request: Request, body: dict = Body(...)):
    actor = auth(request, True)
    validate("ResearchRunRequest", body)
    with connection() as c:
        cmd = command(c, request, actor, "start-research", UUID(body["preview_id"]), body)
        if cmd.replay:
            return response(cmd.replay, True)
        value = batches.start(c, actor=actor["member_id"], request=body, command=cmd.id)
        validate("ResearchRunView", value)
        return response(
            cmd.finish(value, 202, {"ETag": etag("research-run", value["id"], value["revision"])})
        )


@router.get("/research/runs/{run_id}")
def get_run(run_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = batches.run_view(c, run_id)
        validate("ResearchRunView", value)
        return JSONResponse(
            value, headers={"ETag": etag("research-run", run_id, value["revision"])}
        )


@router.get("/research/runs/{run_id}/items")
def run_items(
    run_id: UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
):
    auth(request)
    with connection() as c:
        fetch(c, "SELECT id FROM research_runs WHERE id=%s", (run_id,))
        rows = c.execute(
            "SELECT sample_id,repeat_index,bid_id,operation_id,state,error_code,result_blob_id AS result_artifact_id FROM research_run_items WHERE run_id=%s ORDER BY sample_id,repeat_index LIMIT %s OFFSET %s",
            (run_id, limit + 1, offset),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def mutate(run_id, request, body, kind):
    actor = auth(request, True)
    validate("ResumeResearchRequest" if kind == "resume" else "ReasonRequest", body)
    with connection() as c:
        cmd = command(c, request, actor, kind + "-research", run_id, body, conditional=True)
        if cmd.replay:
            return response(cmd.replay, True)
        value = batches.mutate_run(
            c,
            actor=actor["member_id"],
            identity=run_id,
            condition=request.headers.get("if-match"),
            kind=kind,
            body=body,
            command=cmd.id,
        )
        return response(
            cmd.finish(value, headers={"ETag": etag("research-run", run_id, value["revision"])})
        )


@router.post("/research/runs/{run_id}/resume")
def resume(run_id: UUID, request: Request, body: dict = Body(...)):
    return mutate(run_id, request, body, "resume")


@router.post("/research/runs/{run_id}/cancel")
def cancel(run_id: UUID, request: Request, body: dict = Body(...)):
    return mutate(run_id, request, body, "cancel")
