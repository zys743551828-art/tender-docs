"""Explicit project lifecycle; valid submissions and immutable history are never fabricated."""

from packages.domain.runtime import DomainError, audit, clean, fetch

ACTIVE_STATES = ("queued", "running", "cancel_requested")
NEXT = {
    "preparation": {"start": "working", "terminate": "terminated"},
    "working": {"terminate": "terminated"},
    "submitted": {"end": "closed", "terminate": "terminated"},
    "closed": {"reopen": "working"},
    "terminated": {"reopen": "working"},
}


def lock(c, bid):
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))


def profile(c, bid):
    return fetch(
        c,
        "SELECT p.bid_id,p.phase,p.archived_at,b.revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
        (bid,),
    )


def active_operations(c, bid):
    return c.execute(
        "SELECT id,kind,state FROM operations WHERE bid_id=%s AND state=ANY(%s) ORDER BY id",
        (bid, list(ACTIVE_STATES)),
    ).fetchall()


def view(c, bid):
    row = profile(c, bid)
    active = active_operations(c, bid)
    allowed = list(NEXT[row["phase"]])
    archive_blockers = []
    if row["phase"] in ("closed", "terminated") and not row["archived_at"]:
        if active:
            archive_blockers.append("OPERATIONS_IN_FLIGHT")
        else:
            allowed.append("archive")
    if row["archived_at"]:
        allowed = ["reopen"]
    return clean(row) | {
        "allowed_actions": allowed,
        "archive_blockers": archive_blockers,
        "active_operations": clean(active),
    }


def assert_work_writable(c, bid):
    """Serialize ordinary API work mutations with archive; archival supplements use separate paths."""
    lock(c, bid)
    row = profile(c, bid)
    if row["archived_at"] or row["phase"] in ("closed", "terminated"):
        raise DomainError(
            "PROJECT_WORK_CLOSED",
            "项目已结束或归档；继续编制请明确重新打开，补录或更正请用专用入口",
            409,
            {"bid_id": str(bid), "phase": row["phase"], "archived": row["archived_at"] is not None},
        )


def change(c, bid, actor, action, reason, command_id):
    lock(c, bid)
    before = profile(c, bid)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise DomainError("LIFECYCLE_REASON_REQUIRED", "请记录本次项目状态变更的原因", 422)
    if action == "archive":
        if before["archived_at"]:
            return view(c, bid)
        if before["phase"] not in ("closed", "terminated"):
            raise DomainError("PROJECT_NOT_FINISHED", "请先明确结束或终止本轮工作", 409)
        active = active_operations(c, bid)
        if active:
            raise DomainError(
                "PROJECT_OPERATIONS_IN_FLIGHT",
                "仍有后台任务；可等待完成，或逐项请求取消并等终态后归档",
                409,
                {"active_operations": clean(active), "tasks_cancelled": False},
            )
        c.execute("UPDATE bid_profiles SET archived_at=clock_timestamp() WHERE bid_id=%s", (bid,))
    else:
        target = NEXT[before["phase"]].get(action)
        if target is None or before["archived_at"] and action != "reopen":
            raise DomainError("PROJECT_TRANSITION_INVALID", "当前业务阶段不支持这个动作", 409)
        c.execute(
            "UPDATE bid_profiles SET phase=%s,archived_at=NULL WHERE bid_id=%s", (target, bid)
        )
    changed = c.execute(
        "UPDATE bids SET revision=revision+1 WHERE id=%s RETURNING revision", (bid,)
    ).fetchone()
    observed = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    audit(
        c,
        actor,
        bid,
        "project.lifecycle_changed",
        {
            "action": action,
            "before": clean(before),
            "after": clean(profile(c, bid)),
            "reason": reason.strip(),
            "lifecycle_epoch": changed["revision"],
            "observed_at": observed,
        },
        command_id,
    )
    return view(c, bid)
