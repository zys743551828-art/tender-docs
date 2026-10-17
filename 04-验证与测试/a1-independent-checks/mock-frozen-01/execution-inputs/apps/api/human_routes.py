"""A1.2人工事实更正/确认；统一理由、强CAS、幂等，不自动调用模型。"""

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.review_views import decision_view
from packages.domain.fact_sets import (
    carryover,
    current_facts,
    decision_basis,
    evidence_basis,
    identity,
    replace_set,
)
from packages.domain.human_decisions import append_decision
from packages.domain.rule_sets import current_decision
from packages.domain.runtime import (
    Command,
    DomainError,
    clean,
    connection,
    etag,
    fetch,
    sha,
    validate,
)

router = APIRouter(prefix="/api/v1", tags=["human decisions"])


def fact_view(c, fact_id):
    row = fetch(c, "SELECT * FROM fact_items WHERE id=%s", (fact_id,))
    decision = current_decision(c, row["bid_id"], "fact", fact_id)
    head = c.execute(
        "SELECT revision FROM decision_heads WHERE bid_id=%s AND object_kind='fact' AND object_id=%s",
        (row["bid_id"], fact_id),
    ).fetchone()
    body = clean(
        {
            key: row[key]
            for key in ("id", "subject", "scope", "name", "value", "unit", "evidence_ids")
        }
        | {
            "confirmation": decision["decision"] if decision else "pending",
            "decision_revision": head["revision"] if head else 1,
        }
    )
    validate("FactView", body)
    issues = []
    try:
        current_basis = {
            "fact_identity": identity(row),
            "evidence": evidence_basis(c, row["bid_id"], row["evidence_ids"], publish=False),
        }
        if (
            decision
            and decision["decision"] == "confirmed"
            and decision_basis(c, decision["id"]) != current_basis
        ):
            issues = ["FACT_SOURCE_REVALIDATION_NEEDED"]
    except (DomainError, ValueError):
        issues = ["FACT_SOURCE_REVALIDATION_NEEDED"]
    if issues and body["confirmation"] == "confirmed":
        body["confirmation"] = "pending"
    return body | {
        "source_validity": "needs_revalidation" if issues else "effective",
        "source_issues": issues,
        "carryover": carryover(c, fact_id),
        "decision": decision_view(decision) if decision else None,
    }


def fact_set_view(c, bid_id):
    fetch(c, "SELECT id FROM bids WHERE id=%s", (bid_id,))
    head, rows = current_facts(c, bid_id)
    return clean(
        {
            "id": head["fact_set_id"],
            "bid_id": bid_id,
            "revision": head["revision"],
            "facts": [fact_view(c, row["id"]) for row in rows],
        }
    )


def tag(kind, identity, body):
    return etag(kind, identity, sha(body))


def check_tag(request, current):
    if not request.headers.get("if-match"):
        raise DomainError("PRECONDITION_REQUIRED", "请读取当前对象后提交", 428)
    if request.headers["if-match"] != current:
        raise DomainError("REVISION_CONFLICT", "对象或依据已变化，请核对后重试", 412)


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


def saved(value, replay=False):
    return JSONResponse(
        value["body"],
        status_code=value["status"],
        headers=value["headers"] | ({"Idempotent-Replayed": "true"} if replay else {}),
    )


@router.get("/bids/{bid_id}/facts")
def get_facts(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = fact_set_view(c, bid_id)
        return JSONResponse(body, headers={"ETag": tag("facts", bid_id, body)})


@router.get("/facts/{fact_id}")
def get_fact(fact_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        body = fact_view(c, fact_id)
        return JSONResponse(body, headers={"ETag": tag("fact", fact_id, body)})


@router.post("/bids/{bid_id}/facts")
def change_fact(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("FactChangeRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "change_fact", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        check_tag(r, tag("facts", bid_id, fact_set_view(c, bid_id)))
        replace_set(
            c,
            bid_id,
            actor["member_id"],
            body["fact"]["reason"],
            kind="replace" if body["replaces_fact_id"] else "add",
            removed_id=body["replaces_fact_id"],
            new_fact=body["fact"],
            command_id=cmd.id,
        )
        value = fact_set_view(c, bid_id)
        return saved(cmd.finish(value, headers={"ETag": tag("facts", bid_id, value)}))


@router.post("/facts/{fact_id}/decisions")
def decide_fact(fact_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("DecisionRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "decide_fact", fact_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        row = fetch(c, "SELECT * FROM fact_items WHERE id=%s", (fact_id,))
        bid = row["bid_id"]
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        check_tag(r, tag("fact", fact_id, fact_view(c, fact_id)))
        head, _ = current_facts(c, bid)
        if head["fact_set_id"] != row["fact_set_id"]:
            raise DomainError("FACT_HEAD_CHANGED", "该事实已为历史版本，请核对当前事实集", 409)
        try:
            basis = {
                "fact_identity": identity(row),
                "evidence": evidence_basis(c, bid, row["evidence_ids"]),
            }
        except ValueError as error:
            raise DomainError(
                "FACT_SOURCE_NOT_EFFECTIVE", "事实依据已撤回，请更正或撤回当前事实", 409
            ) from error
        decision = append_decision(
            c, bid, actor["member_id"], "fact", fact_id, body, command_id=cmd.id, basis=basis
        )
        replace_set(
            c,
            bid,
            actor["member_id"],
            body["reason"],
            kind="replace",
            removed_id=fact_id,
            command_id=cmd.id,
            confirmation_only=True,
        )
        return saved(cmd.finish(decision_view(decision)))


@router.post("/facts/{fact_id}/withdrawals")
def withdraw_fact(fact_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate("ReasonRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "withdraw_fact", fact_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        row = fetch(c, "SELECT bid_id FROM fact_items WHERE id=%s", (fact_id,))
        bid = row["bid_id"]
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        check_tag(r, tag("facts", bid, fact_set_view(c, bid)))
        replace_set(
            c,
            bid,
            actor["member_id"],
            body["reason"],
            kind="withdraw",
            removed_id=fact_id,
            command_id=cmd.id,
        )
        value = fact_set_view(c, bid)
        return saved(cmd.finish(value, headers={"ETag": tag("facts", bid, value)}))
