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
    from packages.domain.work_access import begin

    if kind in ("decide_fact", "withdraw_fact"):
        bid = fetch(c, "SELECT bid_id FROM fact_items WHERE id=%s", (target,))["bid_id"]
    elif kind == "decide_rule":
        bid = fetch(c, "SELECT bid_id FROM rule_items WHERE id=%s", (target,))["bid_id"]
    elif kind == "decide_finding":
        bid = fetch(c, "SELECT bid_id FROM findings WHERE id=%s", (target,))["bid_id"]
    elif kind == "create_report":
        bid = fetch(c, "SELECT bid_id FROM review_runs WHERE id=%s", (target,))["bid_id"]
    elif kind in ("change_fact", "correct_rule_set", "confirm_selected_rules", "decide_source_effectivity", "compare_reviews"):
        bid = target
    else:
        raise DomainError("WORK_COMMAND_UNREGISTERED", "该写入入口尚未登记工作守卫", 503)
    return begin(
        c,
        actor["member_id"],
        kind,
        target,
        request.headers.get("idempotency-key"),
        body,
        request.headers.get("if-match"),
        bid=bid,
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


@router.post("/rules/{rule_id}/decisions")
def decide_rule(rule_id: UUID, r: Request, body: dict = Body(...)):
    from apps.api.review_views import rule_view
    from packages.domain.rule_corrections import reject_rule

    actor = auth(r, True)
    validate("DecisionRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "decide_rule", rule_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        row = fetch(c, "SELECT bid_id,rule_set_id FROM rule_items WHERE id=%s", (rule_id,))
        bid = row["bid_id"]
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        view = rule_view(c, rule_id)
        check_tag(r, etag("rule", rule_id, view["decision_revision"]))
        profile = fetch(c, "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid,))
        if profile["current_rule_set_id"] != row["rule_set_id"]:
            raise DomainError(
                "RULE_SET_REVISION_CONFLICT", "该条款已为历史版本，请核对当前清单", 409
            )
        if body["decision"] == "rejected":
            decision, _ = reject_rule(c, bid, actor["member_id"], rule_id, body, command_id=cmd.id)
        else:
            basis = None
            if body["decision"] == "confirmed":
                try:
                    basis = evidence_basis(c, bid, view["evidence_ids"])
                except ValueError as error:
                    raise DomainError(
                        "RULE_SOURCE_UNRESOLVED", "条款来源已撤回或身份变化，请核验生效关系", 409
                    ) from error
            decision = append_decision(
                c, bid, actor["member_id"], "rule", rule_id, body, command_id=cmd.id, basis=basis
            )
        return saved(cmd.finish(decision_view(decision)))


@router.post("/findings/{finding_id}/decisions")
def decide_finding(finding_id: UUID, r: Request, body: dict = Body(...)):
    from apps.api.review_views import finding_view

    actor = auth(r, True)
    validate("DecisionRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "decide_finding", finding_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        row = fetch(c, "SELECT bid_id FROM findings WHERE id=%s", (finding_id,))
        bid = row["bid_id"]
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        check_tag(r, tag("finding", finding_id, finding_view(c, finding_id)))
        decision = append_decision(
            c, bid, actor["member_id"], "finding", finding_id, body, command_id=cmd.id
        )
        from packages.metrics.snapshots import observe_review_risks

        observe_review_risks(c, bid=bid, actor=actor["member_id"], command=cmd.id)
        return saved(cmd.finish(decision_view(decision)))


@router.post("/bids/{bid_id}/rule-set-corrections")
def correct_rule_set(bid_id: UUID, r: Request, body: dict = Body(...)):
    from apps.api.review_views import rule_set_view
    from packages.domain.review_data import read_bundle, verify_bytes
    from packages.domain.rule_corrections import correct_rules

    actor = auth(r, True)
    validate("RuleSetCorrectionRequest", body)
    with connection() as c:
        cmd = command(c, r, actor, "correct_rule_set", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        # 原件字节在bid锁外核对；随后规则包和资料head CAS防止迟到覆盖。
        profile = fetch(
            c, "SELECT current_material_id FROM bid_profiles WHERE bid_id=%s", (bid_id,)
        )
        documents = read_bundle(c, bid_id, profile["current_material_id"])
        verify_bytes(documents)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        profile = fetch(
            c, "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid_id,)
        )
        if profile["current_rule_set_id"] is None:
            raise DomainError("RULE_SET_NOT_READY", "请先准备当前生效要求", 409)
        check_tag(
            r,
            tag(
                "rule-set",
                profile["current_rule_set_id"],
                rule_set_view(c, profile["current_rule_set_id"]),
            ),
        )
        identity = correct_rules(c, bid_id, actor["member_id"], cmd.id, body)
        value = rule_set_view(c, identity)
        return saved(
            cmd.finish(
                value,
                201,
                {
                    "ETag": tag("rule-set", identity, value),
                    "Location": f"/api/v1/bids/{bid_id}/rules",
                },
            )
        )


@router.post("/bids/{bid_id}/rules/decisions")
def confirm_selected_rules(bid_id: UUID, r: Request, body: dict = Body(...)):
    from apps.api.review_views import rule_set_view, rule_view

    actor = auth(r, True)
    if (
        set(body) != {"base_rule_set_id", "rule_ids", "reason"}
        or not isinstance(body["rule_ids"], list)
        or not 1 <= len(body["rule_ids"]) <= 500
        or not isinstance(body["reason"], str)
        or not 1 <= len(body["reason"].strip()) <= 2000
    ):
        raise DomainError("INVALID_REQUEST", "请选择待确认条款并填写核对理由", 422)
    try:
        ids = [UUID(value) for value in body["rule_ids"]]
        base = UUID(body["base_rule_set_id"])
    except (ValueError, TypeError, AttributeError):
        raise DomainError("INVALID_REQUEST", "条款标识无效，请刷新要求清单", 422) from None
    if len(set(ids)) != len(ids):
        raise DomainError("INVALID_REQUEST", "同一次确认不能重复选择条款", 422)
    with connection() as c:
        cmd = command(c, r, actor, "confirm_selected_rules", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        current = fetch(
            c, "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid_id,)
        )["current_rule_set_id"]
        if current != base:
            raise DomainError(
                "RULE_SET_REVISION_CONFLICT", "要求清单已变化，请重新选择待确认条款", 412
            )
        check_tag(r, tag("rule-set", current, rule_set_view(c, current)))
        for identity in ids:
            rule = fetch(
                c,
                "SELECT * FROM rule_items WHERE id=%s AND bid_id=%s AND rule_set_id=%s",
                (identity, bid_id, current),
            )
            view = rule_view(c, identity)
            if view["confirmation"] != "pending":
                raise DomainError(
                    "BATCH_RULE_NOT_PENDING", "所选条款的人工状态已变化，请重新核对选择", 409
                )
            try:
                basis = evidence_basis(c, bid_id, rule["evidence_ids"])
            except ValueError as error:
                raise DomainError(
                    "RULE_SOURCE_UNRESOLVED", "所选条款来源待核验，未部分确认任何条款", 409
                ) from error
            append_decision(
                c,
                bid_id,
                actor["member_id"],
                "rule",
                identity,
                {"decision": "confirmed", "reason": body["reason"], "evidence_ids": []},
                command_id=cmd.id,
                basis=basis,
            )
        value = rule_set_view(c, current)
        return saved(cmd.finish(value, headers={"ETag": tag("rule-set", current, value)}))
