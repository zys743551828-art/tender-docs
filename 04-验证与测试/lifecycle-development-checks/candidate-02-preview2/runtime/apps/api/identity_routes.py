"""Human identity selection and corrections, no automatic name matching."""

from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.views import bid_etag, match_bid
from packages.domain.runtime import Command, DomainError, clean, connection, etag, fetch, sha
from packages.identity import pagination
from packages.identity import repository as identities
from packages.identity.contracts import validate

router = APIRouter(prefix="/api/v1", tags=["identity"])


def default_tag(actor, value):
    return etag("default-bidder", actor, sha(value))


def command(c, r, actor, kind, target, body, conditional=False):
    return Command(
        c,
        actor["member_id"],
        kind,
        target,
        r.headers.get("idempotency-key"),
        body,
        r.headers.get("if-match") if conditional else None,
    )


def saved(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


@router.get("/identity/bidders")
def list_bidders(
    r: Request,
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
):
    auth(r)
    scope = sha({"directory": "bidders/1", "query": query})
    after = pagination.decode(cursor, scope)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = c.execute(
            "SELECT b.id,b.name FROM bidders b WHERE b.name ILIKE %s AND EXISTS(SELECT 1 FROM audit_entries a WHERE a.event_type=%s AND a.payload->>'bidder_id'=b.id::text) AND (%s::text IS NULL OR (b.name,b.id)>(%s,%s)) ORDER BY b.name,b.id LIMIT %s",
            (
                "%" + query + "%",
                identities.REGISTERED,
                after[0] if after else None,
                *(after or [None, None]),
                limit + 1,
            ),
        ).fetchall()
        return {
            "items": [identities.bidder(c, row["id"]) for row in rows[:limit]],
            "has_more": len(rows) > limit,
            "next_cursor": pagination.encode(
                scope, [rows[limit - 1]["name"], str(rows[limit - 1]["id"])]
            )
            if len(rows) > limit
            else None,
        }


@router.post("/identity/bidders")
def register_bidder(r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("RegisterBidder", body)
    with connection() as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("identity-default:" + str(actor["member_id"]),),
        )
        cmd = command(c, r, actor, "register_bidder", UUID(int=0), body)
        if cmd.replay:
            return saved(cmd.replay, True)
        company = identities.register_bidder(
            c, actor["member_id"], body["name"], body["reason"], cmd.id
        )
        if body.get("set_as_default", False):
            identities.set_default(c, actor["member_id"], company["id"], cmd.id)
        return saved(
            cmd.finish(
                {
                    "bidder": company,
                    "default": identities.default_bidder(c, actor["member_id"]),
                    "qualification_verified": False,
                },
                201,
            )
        )


@router.get("/identity/default-bidder")
def get_default(r: Request):
    actor = auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        value = identities.default_bidder(c, actor["member_id"])
        return JSONResponse(value, headers={"ETag": default_tag(actor["member_id"], value)})


@router.put("/identity/default-bidder")
def select_default(r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("SelectDefaultBidder", body)
    with connection() as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("identity-default:" + str(actor["member_id"]),),
        )
        cmd = command(c, r, actor, "select_default_bidder", actor["member_id"], body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请先读取当前默认主体", 428)
        if r.headers["if-match"] != default_tag(
            actor["member_id"], identities.default_bidder(c, actor["member_id"])
        ):
            raise DomainError("REVISION_CONFLICT", "默认主体已经变化，请保留选择并刷新", 412)
        value = identities.set_default(c, actor["member_id"], body["bidder_id"], cmd.id)
        return saved(cmd.finish(value, headers={"ETag": default_tag(actor["member_id"], value)}))


@router.get("/identity/engineering-projects")
def list_engineering(
    r: Request,
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
):
    auth(r)
    scope = sha({"directory": "engineering/1", "query": query})
    after = pagination.decode(cursor, scope)
    with connection() as c:
        rows = c.execute(
            "SELECT id,title,region,engineering_type FROM engineering_projects WHERE title ILIKE %s AND (%s::text IS NULL OR (title,id)>(%s,%s)) ORDER BY title,id LIMIT %s",
            ("%" + query + "%", after[0] if after else None, *(after or [None, None]), limit + 1),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "has_more": len(rows) > limit,
            "next_cursor": pagination.encode(
                scope, [rows[limit - 1]["title"], str(rows[limit - 1]["id"])]
            )
            if len(rows) > limit
            else None,
        }


@router.get("/identity/rounds")
def list_rounds(
    r: Request,
    engineering_project_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
):
    auth(r)
    scope = sha({"directory": "rounds/1", "engineering_project_id": str(engineering_project_id)})
    after = pagination.decode(cursor, scope, rounds=True)
    with connection() as c:
        fetch(c, "SELECT id FROM engineering_projects WHERE id=%s", (engineering_project_id,))
        rows = c.execute(
            "SELECT r.*,e.title AS engineering_title,e.region,e.engineering_type FROM bid_rounds r JOIN engineering_projects e ON e.id=r.engineering_project_id WHERE engineering_project_id=%s AND (%s::text IS NULL OR (coalesce(r.lot,''),-r.round_no,r.id)>(%s,%s,%s)) ORDER BY coalesce(r.lot,''),r.round_no DESC,r.id LIMIT %s",
            (
                engineering_project_id,
                after[0] if after else None,
                *(after or [None, None, None]),
                limit + 1,
            ),
        ).fetchall()
        return {
            "items": clean(rows[:limit]),
            "has_more": len(rows) > limit,
            "next_cursor": pagination.encode(
                scope,
                [
                    rows[limit - 1]["lot"] or "",
                    -rows[limit - 1]["round_no"],
                    str(rows[limit - 1]["id"]),
                ],
            )
            if len(rows) > limit
            else None,
        }


@router.get("/bids/{bid_id}/identity")
def get_identity(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return JSONResponse(
            {"identity": identities.snapshot(c, bid_id), "affected": identities.impact(c, bid_id)},
            headers={"ETag": bid_etag(c, bid_id)},
        )


@router.put("/bids/{bid_id}/identity")
def correct_identity(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("CorrectBidIdentity", body)
    with connection() as c:
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        cmd = command(c, r, actor, "correct_bid_identity", bid_id, body, True)
        if cmd.replay:
            return saved(cmd.replay, True)
        match_bid(c, r.headers.get("if-match"), bid_id)
        value = identities.correct(c, bid_id, actor["member_id"], cmd.id, body)
        return saved(cmd.finish(value, headers={"ETag": bid_etag(c, bid_id)}))
