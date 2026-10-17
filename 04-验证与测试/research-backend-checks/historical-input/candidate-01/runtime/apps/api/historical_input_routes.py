"""Historical reconstruction input API. Explicit retrospective/research-only authority."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import Command, DomainError, connection
from packages.model_governance import historical_input as repo
from packages.model_governance.historical_requests import HistoricalInputRequest

router = APIRouter(prefix="/api/v1/historical-inputs", tags=["historical-inputs"])


def reply(value, tag):
    return JSONResponse(value, headers={"ETag": tag})


def result(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotency-Replayed": "true"} if replay else {}),
    )


def _command(c, request, actor, body):
    return Command(
        c,
        actor["member_id"],
        "historical-input-freeze",
        body.submission_id,
        request.headers.get("idempotency-key"),
        body.model_dump(mode="json"),
        request.headers.get("if-match"),
    )


def _read(identity):
    with connection() as c:
        snapshot = repo.metadata(c, identity)
    repo.verify_bytes(snapshot)
    with connection() as c:
        value, tag = repo.view(c, identity)
    return value, tag


def _replay(request, actor, body, identity):
    # Verify actual object bytes without graph/SQL locks, then fresh current guard plus
    # receipt identity. An old confirmed replay may never bypass withdrawal/latest parse.
    with connection() as c:
        snapshot = repo.metadata(c, identity)
    repo.verify_bytes(snapshot)
    with connection() as c:
        current, tag = repo.view(c, identity)
        if not current["current"]:
            repo.unavailable("冻结历史输入已失效，不能重放旧有效状态")
        cmd = _command(c, request, actor, body)
        if cmd.replay is None:
            raise DomainError(
                "HISTORICAL_INPUT_REPLAY_MISSING", "已提交回执不存在，请重新核对", 409
            )
        repo.match(tag, cmd.replay["headers"]["ETag"])
        return result(cmd.replay, True)


@router.get("/capabilities")
def capabilities(request: Request):
    auth(request)
    with connection() as c:
        ready = repo.configured(c)
    return {
        "schema_configured": ready,
        "historical_input_registration": ready,
        "execution_available": False,
        "formal_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }


@router.get("/submissions/{submission_id}/context")
def context(submission_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        value, tag = repo.context(c, submission_id)
    return reply(value, tag)


@router.get("/{input_id}")
def get_input(input_id: UUID, request: Request):
    auth(request)
    return reply(*_read(input_id))


@router.post("")
def create_input(request: Request, body: HistoricalInputRequest):
    actor = auth(request, True)
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请先读取当前历史回测输入上下文", 428)
    key = request.headers.get("idempotency-key")
    if not key or len(key) > 128:
        raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)
    value = body.model_dump(mode="json")
    with connection() as c:
        repo.require(c)
        old = c.execute(
            "SELECT response FROM commands WHERE actor_id=%s AND command_kind='historical-input-freeze' AND target_id=%s AND idempotency_key=%s",
            (actor["member_id"], body.submission_id, key),
        ).fetchone()
    if old and old["response"]:
        return _replay(request, actor, body, old["response"]["body"]["id"])
    with connection() as c:
        captured = repo.capture(c, value, request.headers.get("if-match"))
    prepared = repo.stage(repo.build(captured))
    replay = None
    with connection() as c:
        # Leading full scope precedes command receipt locks. Actual source CAS is repeated
        # by publish. Neither this nor seal performs remote I/O.
        repo.context(c, body.submission_id)
        cmd = _command(c, request, actor, body)
        if cmd.replay:
            replay = cmd.replay["body"]["id"]
        else:
            saved, tag = repo.publish(
                c,
                prepared=prepared,
                value=value,
                condition=request.headers.get("if-match"),
                actor=actor["member_id"],
                command=cmd.id,
            )
            output = cmd.finish(saved, 201, {"ETag": tag})
    if replay is not None:
        return _replay(request, actor, body, replay)
    return result(output)
