"""A1.2人工决定基础事务；调用方必须先锁项目并核对实际对象ETag。"""

from psycopg import sql

from packages.domain.finding_lifecycle import origin
from packages.domain.runtime import DomainError, audit, fetch, uid

ALLOWED = {
    "rule": {"confirmed", "rejected", "deferred"},
    "fact": {"confirmed", "rejected"},
    "finding": {"confirm", "dismiss", "defer", "reopen"},
}


def append_decision(c, bid, actor, kind, object_id, body, *, command_id=None, basis=None):
    if kind not in ALLOWED or body["decision"] not in ALLOWED[kind]:
        raise DomainError("DECISION_NOT_ALLOWED", "该对象不支持此人工决定", 422)
    if not body["reason"].strip() or len(body["reason"]) > 2000:
        raise DomainError("DECISION_REASON_REQUIRED", "请填写不超过2000字的核对理由", 422)
    table = {"rule": "rule_items", "fact": "fact_items", "finding": "findings"}[kind]
    # table严格来自固定白名单，不接收用户SQL片段。
    row = fetch(
        c,
        sql.SQL("SELECT * FROM {} WHERE id=%s AND bid_id=%s").format(sql.Identifier(table)),
        (object_id, bid),
    )
    requested_id = row["id"]
    if kind == "finding":
        row = origin(c, row)
    ids = list(dict.fromkeys(body["evidence_ids"]))
    count = c.execute(
        "SELECT count(*) AS n FROM evidence_blocks WHERE bid_id=%s AND id=ANY(%s::uuid[])",
        (bid, ids),
    ).fetchone()["n"]
    if count != len(ids):
        raise DomainError("DECISION_EVIDENCE_INVALID", "决定依据不属于当前项目", 422)
    if (
        kind == "rule"
        and body["decision"] == "confirmed"
        and row["provenance_status"] != "effective"
    ):
        raise DomainError("RULE_SOURCE_UNRESOLVED", "条款来源或适用关系尚待核验，不能确认", 409)
    head = c.execute(
        "SELECT * FROM decision_heads WHERE bid_id=%s AND object_kind=%s AND object_id=%s FOR UPDATE",
        (bid, kind, row["id"]),
    ).fetchone()
    revision = head["revision"] if head else 1
    identity = uid()
    c.execute(
        "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s)",
        (identity, bid, kind, row["id"], revision, body["decision"], body["reason"], ids, actor),
    )
    c.execute(
        "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(bid_id,object_kind,object_id) DO UPDATE SET revision=excluded.revision,decision_id=excluded.decision_id",
        (bid, kind, row["id"], revision + 1, identity),
    )
    audit(
        c,
        actor,
        bid,
        "human.decision_recorded",
        {
            "decision_id": str(identity),
            "object_kind": kind,
            "object_id": str(row["id"]),
            "requested_object_id": str(requested_id),
            "decision": body["decision"],
            "basis": basis,
        },
        command_id,
    )
    return fetch(c, "SELECT * FROM decisions WHERE id=%s", (identity,))
