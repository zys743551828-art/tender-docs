"""标准登记/独立事实补录/显式更正；原件网络I/O不持业务锁。"""

from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.human_routes import command, saved
from apps.api.views import bid_etag, match_bid
from packages.domain import storage
from packages.domain.runtime import (
    DomainError,
    clean,
    connection,
    decode_cursor,
    encode_cursor,
    etag,
    fetch,
    sha,
    validate,
)
from packages.domain.submission_gate import evaluate
from packages.domain.submissions import document, register, view

router = APIRouter(prefix="/api/v1", tags=["submission facts"])


def _verify(objects):
    for item in objects:
        try:
            stream = storage.client().get_object(Bucket=storage.bucket(), Key=item["object_key"])[
                "Body"
            ]
            try:
                raw = stream.read()
            finally:
                stream.close()
        except (BotoCoreError, ClientError) as error:
            raise DomainError(
                "SUBMISSION_ORIGINAL_UNAVAILABLE",
                "实际稿件或提交依据暂时无法读取，请保留输入重试",
                503,
                retryable=True,
            ) from error
        if len(raw) != item["byte_size"] or sha(raw) != item["digest"]:
            raise DomainError(
                "SUBMISSION_ORIGINAL_DIGEST_MISMATCH", "实际稿件或提交依据的真实字节校验失败", 409
            )


def _submit(
    r,
    actor,
    bid,
    body,
    *,
    kind,
    target,
    command_kind,
    whole_body=None,
    supersedes=None,
    reason=None,
):
    request = whole_body or body
    validate("SubmissionRequest" if kind == "standard" else "OfflineSubmissionRequest", body)
    with connection() as c:
        from packages.domain.work_access import begin

        cmd = begin(c, actor["member_id"], command_kind, target, r.headers.get("idempotency-key"), request, r.headers.get("if-match"), bid=bid, work=False)
        if cmd.replay:
            return saved(cmd.replay, True)
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请先读取当前项目并核对实际稿件后登记", 428)
        version = (
            body["body_version_id"] if kind == "standard" else body["actual_document_version_id"]
        )
        objects = [document(c, bid, version), document(c, bid, body["evidence_ref"])]
        c.rollback()
    _verify(objects)
    with connection() as c:
        cmd = begin(c, actor["member_id"], command_kind, target, r.headers.get("idempotency-key"), request, r.headers.get("if-match"), bid=bid, work=False)
        if cmd.replay:
            return saved(cmd.replay, True)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        match_bid(c, r.headers.get("if-match"), bid)
        identity = register(
            c,
            bid,
            actor["member_id"],
            cmd.id,
            body,
            kind=kind,
            supersedes=supersedes,
            correction_reason=reason,
        )
        value = view(c, identity)
        return saved(
            cmd.finish(
                value,
                201,
                {
                    "ETag": etag("submission", identity, sha(value)),
                    "X-Bid-ETag": bid_etag(c, bid),
                    "Location": f"/api/v1/submissions/{identity}",
                },
            )
        )


@router.get("/bids/{bid_id}/submission-gate")
def get_gate(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return JSONResponse(evaluate(c, bid_id), headers={"ETag": bid_etag(c, bid_id)})


@router.post("/bids/{bid_id}/submissions")
def register_standard(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    return _submit(
        r,
        actor,
        bid_id,
        body,
        kind="standard",
        target=bid_id,
        command_kind="register_standard_submission",
    )


@router.post("/bids/{bid_id}/submission-facts")
def register_fact(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    return _submit(
        r,
        actor,
        bid_id,
        body,
        kind="offline_fact",
        target=bid_id,
        command_kind="register_offline_submission",
    )


@router.post("/submissions/{submission_id}/corrections")
def correct_submission(submission_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    if (
        set(body) != {"kind", "submission", "reason"}
        or body["kind"] not in ("standard", "offline_fact")
        or not isinstance(body["submission"], dict)
        or not isinstance(body["reason"], str)
        or not 1 <= len(body["reason"].strip()) <= 2000
    ):
        raise DomainError("INVALID_REQUEST", "请明确原记录、真实稿件、登记类型及更正依据", 422)
    with connection() as c:
        old = fetch(c, "SELECT bid_id FROM submission_records WHERE id=%s", (submission_id,))
    return _submit(
        r,
        actor,
        old["bid_id"],
        body["submission"],
        kind=body["kind"],
        target=submission_id,
        command_kind="correct_submission",
        whole_body=body,
        supersedes=submission_id,
        reason=body["reason"],
    )


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = view(c, submission_id)
        return JSONResponse(value, headers={"ETag": etag("submission", submission_id, sha(value))})


@router.get("/bids/{bid_id}/submissions")
def list_submissions(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"submissions:{bid_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        if cursor:
            at, identity = decode_cursor(cursor, scope)
            rows = c.execute(
                "SELECT id,created_at FROM submission_records WHERE bid_id=%s AND(created_at,id)<(%s,%s) ORDER BY created_at DESC,id DESC LIMIT %s",
                (bid_id, at, identity, limit + 1),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id,created_at FROM submission_records WHERE bid_id=%s ORDER BY created_at DESC,id DESC LIMIT %s",
                (bid_id, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        return {
            "items": [view(c, row["id"]) for row in items],
            "has_more": more,
            "next_cursor": encode_cursor(items[-1], scope) if more else None,
        }


@router.get("/bids/{bid_id}/submission-documents")
def list_documents(
    bid_id: UUID, r: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
):
    auth(r)
    scope = f"submission-documents:{bid_id}:1"
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
        columns = "SELECT v.id AS document_version_id,v.original_name,v.artifact_kind,v.created_at,b.digest,b.byte_size,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s"
        if cursor:
            at, identity = decode_cursor(cursor, scope)
            rows = c.execute(
                columns
                + " AND(v.created_at,v.id)<(%s,%s) ORDER BY v.created_at DESC,v.id DESC LIMIT %s",
                (bid_id, at, identity, limit + 1),
            ).fetchall()
        else:
            rows = c.execute(
                columns + " ORDER BY v.created_at DESC,v.id DESC LIMIT %s", (bid_id, limit + 1)
            ).fetchall()
        more = len(rows) > limit
        items = rows[:limit]
        page = clean(
            [{key: value for key, value in row.items() if key != "created_at"} for row in items]
        )
        next_cursor = (
            encode_cursor(
                {"created_at": items[-1]["created_at"], "id": items[-1]["document_version_id"]},
                scope,
            )
            if more
            else None
        )
        return {"items": page, "has_more": more, "next_cursor": next_cursor}
