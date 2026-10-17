"""按需报价实际API：一次登记、独立试算、显式保存和不可变历史。"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection
from packages.pricing import repository as repo
from packages.pricing import sources
from packages.pricing.models import PricingRequest, RegisterFormula, SavePlan

router = APIRouter(prefix="/api/v1", tags=["pricing"])


def response(result, replay=False):
    return JSONResponse(
        result["body"],
        status_code=result["status"],
        headers={**result["headers"], **({"Idempotency-Replayed": "true"} if replay else {})},
    )


def write_headers(request):
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 128:
        raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请读取当前资源后再操作", 428)


def command(c, request, actor, kind, target, body):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match"),
    )


def load_formula(bid, identity):
    with connection() as c:
        blob = repo.formula_blob(c, bid, identity)
    return repo.read_formula(blob, bid=bid), blob["digest"]


@router.get("/bids/{bid_id}/pricing-context")
def context(bid_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = sources.context(c, bid_id)
    return JSONResponse(value, headers={"ETag": tag})


@router.get("/bids/{bid_id}/pricing-formula-sources")
def source_choices(
    bid_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100), cursor: UUID | None = None
):
    auth(request)
    with connection() as c:
        return sources.source_choices(c, bid_id, limit=limit, cursor=cursor)


@router.post("/bids/{bid_id}/pricing-formulas")
def register_formula(bid_id: UUID, request: Request, body: RegisterFormula):
    actor = auth(request, True)
    write_headers(request)
    value = body.model_dump(mode="json")
    # Existing committed replay avoids remote writes, but still rechecks switch+source.
    with connection() as c:
        sources.enabled_bid(c, bid_id)
        old = c.execute(
            "SELECT id,response FROM commands WHERE actor_id=%s AND command_kind='register-pricing-formula' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], bid_id, request.headers.get("idempotency-key")),
        ).fetchone()
    if old and old["response"]:
        manifest, digest = load_formula(bid_id, old["response"]["body"]["formula_version_id"])
        with connection() as c:
            repo.require_current(c, bid=bid_id, manifest=manifest, digest=digest)
            cmd = command(c, request, actor, "register-pricing-formula", bid_id, value)
            if cmd.replay:
                return response(cmd.replay, True)
    with connection() as c:
        manifest = repo.prepare_formula(
            c,
            bid=bid_id,
            actor=actor["member_id"],
            value=value,
            condition=request.headers.get("if-match"),
        )
    staged = repo.stage_formula(manifest)
    with connection() as c:
        sources.enabled_bid(c, bid_id, lock=True)
        cmd = command(c, request, actor, "register-pricing-formula", bid_id, value)
        if cmd.replay:
            # Race replay must qualify the committed version, not the fresh unused stage.
            existing = cmd.replay["body"]
            repo.require_current(
                c, bid=bid_id, manifest=existing["formula"], digest=existing["formula_digest"]
            )
            return response(cmd.replay, True)
        result = repo.publish_formula(
            c,
            bid=bid_id,
            actor=actor["member_id"],
            manifest=manifest,
            staged=staged,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(cmd.finish(result, 201, {"ETag": repo.formula_etag(result)}))


@router.get("/bids/{bid_id}/pricing-formulas")
def list_formulas(
    bid_id: UUID, request: Request, limit: int = Query(25, ge=1, le=50), cursor: UUID | None = None
):
    auth(request)
    with connection() as c:
        page = repo.list_formula_records(c, bid_id, limit=limit, cursor=cursor)
        blobs = [repo.formula_blob(c, bid_id, row["formula_version_id"]) for row in page["items"]]
    manifests = [repo.read_formula(blob, bid=bid_id) for blob in blobs]
    with connection() as c:
        sources.enabled_bid(c, bid_id)
        for summary, manifest, blob in zip(page["items"], manifests, blobs, strict=True):
            summary["current_applicability"] = repo.applicability(
                c, bid=bid_id, manifest=manifest, digest=blob["digest"]
            )
    return page


@router.get("/bids/{bid_id}/pricing-formulas/{formula_version_id}")
def get_formula(bid_id: UUID, formula_version_id: UUID, request: Request):
    auth(request)
    manifest, digest = load_formula(bid_id, formula_version_id)
    with connection() as c:
        value = repo.formula_view(c, bid=bid_id, manifest=manifest, digest=digest)
    return JSONResponse(value, headers={"ETag": repo.formula_etag(value)})


@router.post("/bids/{bid_id}/pricing-simulations")
def simulate(bid_id: UUID, request: Request, body: PricingRequest):
    actor = auth(request, True)
    write_headers(request)
    value = body.model_dump(mode="json")
    manifest, digest = load_formula(bid_id, body.formula_version_id)
    result = repo.simulate(manifest, value)
    with connection() as c:
        repo.require_current(c, bid=bid_id, manifest=manifest, digest=digest)
        cmd = command(c, request, actor, "simulate-pricing", bid_id, value)
        if cmd.replay:
            return response(cmd.replay, True)
        projection = repo.publish_simulation(
            c,
            bid=bid_id,
            actor=actor["member_id"],
            manifest=manifest,
            digest=digest,
            value=value,
            result=result,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(cmd.finish(projection, 201, {"ETag": repo.simulation_etag(projection)}))


@router.get("/bids/{bid_id}/pricing-simulations/{simulation_id}")
def get_simulation(bid_id: UUID, simulation_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value = repo.simulation_view(c, bid=bid_id, identity=simulation_id)
    return JSONResponse(value, headers={"ETag": repo.simulation_etag(value)})


@router.post("/bids/{bid_id}/pricing-plans")
def save_plan(bid_id: UUID, request: Request, body: SavePlan):
    actor = auth(request, True)
    write_headers(request)
    value = body.model_dump(mode="json")
    with connection() as c:
        sources.enabled_bid(c, bid_id, lock=True)
        # Revalidate current sources even when Command returns a previous saved response.
        simulation = repo.simulation_view(c, bid=bid_id, identity=body.simulation_id)
        repo.require_current(
            c,
            bid=bid_id,
            manifest=simulation["formula_snapshot"],
            digest=simulation["formula_digest"],
        )
        cmd = command(c, request, actor, "save-pricing-plan", bid_id, value)
        if cmd.replay:
            return response(cmd.replay, True)
        result = repo.save_plan(
            c,
            bid=bid_id,
            actor=actor["member_id"],
            value=value,
            condition=request.headers.get("if-match"),
            command=cmd.id,
        )
        return response(cmd.finish(result, 201))


@router.get("/bids/{bid_id}/pricing-plans")
def list_plans(
    bid_id: UUID, request: Request, limit: int = Query(25, ge=1, le=50), cursor: UUID | None = None
):
    auth(request)
    with connection() as c:
        return repo.list_plans(c, bid=bid_id, limit=limit, cursor=cursor)


@router.get("/bids/{bid_id}/pricing-plans/{plan_id}")
def get_plan(bid_id: UUID, plan_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        return repo.plan_view(c, bid=bid_id, identity=plan_id)
