"""Explicit project lifecycle; valid submissions and immutable history are never fabricated."""

from psycopg import sql

from packages.domain.runtime import DomainError, audit, clean, fetch

ACTIVE_STATES = ("queued", "running", "cancel_requested")
NEXT = {
    "preparation": {"start": "working", "terminate": "terminated"},
    "working": {"terminate": "terminated"},
    "submitted": {"end": "closed", "terminate": "terminated"},
    "closed": {"reopen": "working"},
    "terminated": {"reopen": "working"},
}


def lock(c, bid, *, worker=False):
    if worker:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
        fetch(c, "SELECT id FROM bids WHERE id=%s", (bid,))
    else:
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


def closure_schema_available(c):
    return c.execute(
        "SELECT to_regclass('design.upload_batch_closures') IS NOT NULL AS ready"
    ).fetchone()["ready"]


def unfinished_intents(c, bid):
    # The optional extension is initialized only in a fresh database. Until then every
    # unfinished base intent remains a blocker; absence cannot silently release archive.
    exclusions = (
        sql.SQL(
            "AND NOT EXISTS(SELECT FROM upload_batch_closures closed WHERE closed.batch_id=b.id)"
        )
        if closure_schema_available(c)
        else sql.SQL("")
    )
    uploads = c.execute(
        sql.SQL("""SELECT u.id,u.batch_id,u.state,u.verified_version_id,t.aborted_at
        FROM uploads u JOIN upload_batches b ON b.id=u.batch_id
        LEFT JOIN upload_transfers t ON t.upload_id=u.id
        WHERE u.bid_id=%s AND t.committed_binding_id IS NULL AND t.aborted_at IS NULL
         AND u.state IN ('authorized','uploaded','verifying','verified') {} ORDER BY u.batch_id,u.id""").format(
            exclusions
        ),
        (bid,),
    ).fetchall()
    batches = c.execute(
        sql.SQL(
            "SELECT b.id,b.state,b.revision FROM upload_batches b WHERE b.bid_id=%s "
            "AND b.state IN ('open','verifying') {} ORDER BY b.id"
        ).format(exclusions),
        (bid,),
    ).fetchall()
    return clean(uploads), clean(batches)


def assert_batch_open(c, bid, batch):
    """Caller must hold bid lock before any work-creating intent or delayed publication."""
    row = fetch(c, "SELECT * FROM upload_batches WHERE bid_id=%s AND id=%s", (bid, batch))
    if (
        closure_schema_available(c)
        and c.execute("SELECT id FROM upload_batch_closures WHERE batch_id=%s", (batch,)).fetchone()
    ):
        raise DomainError(
            "BATCH_EXPLICITLY_CLOSED", "批次已结束，暂存资料保留；请明确选择新的补录或工作批次", 409
        )
    return row


def batch_closure_view(c, bid, batch):
    row = fetch(
        c,
        "SELECT id,bid_id,state,revision FROM upload_batches WHERE bid_id=%s AND id=%s",
        (bid, batch),
    )
    uploads = c.execute(
        """SELECT u.id,u.state,u.verified_version_id,t.revision,t.aborted_at,t.committed_binding_id
        FROM uploads u LEFT JOIN upload_transfers t ON t.upload_id=u.id
        WHERE u.bid_id=%s AND u.batch_id=%s ORDER BY u.id""",
        (bid, batch),
    ).fetchall()
    prior = (
        c.execute("SELECT * FROM upload_batch_closures WHERE batch_id=%s", (batch,)).fetchone()
        if closure_schema_available(c)
        else None
    )
    return clean(
        {
            "batch": row,
            "uploads": uploads,
            "closure": prior,
            "available": closure_schema_available(c),
        }
    )


def close_batch(c, bid, batch, actor, reason, command_id):
    """End an intent explicitly, without changing bytes, document roles or current material."""
    lock(c, bid)
    if not closure_schema_available(c):
        raise DomainError(
            "BATCH_CLOSURE_NOT_CONFIGURED", "当前环境尚未开放保留暂存资料结束批次", 503
        )
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise DomainError("LIFECYCLE_REASON_REQUIRED", "请记录本次结束批次的原因", 422)
    batch_row = fetch(
        c, "SELECT * FROM upload_batches WHERE bid_id=%s AND id=%s FOR UPDATE", (bid, batch)
    )
    prior = c.execute("SELECT * FROM upload_batch_closures WHERE batch_id=%s", (batch,)).fetchone()
    if prior:
        return clean(prior)
    if (
        batch_row["state"] == "committed"
        and not c.execute(
            """SELECT u.id FROM uploads u LEFT JOIN upload_transfers t ON t.upload_id=u.id
           WHERE u.batch_id=%s AND t.committed_binding_id IS NULL AND t.aborted_at IS NULL
           AND u.state IN ('authorized','uploaded','verifying','verified') LIMIT 1""",
            (batch,),
        ).fetchone()
    ):
        raise DomainError("BATCH_ALREADY_COMMITTED", "批次已全部入库，无需结束暂存意图", 409)
    verifying = c.execute(
        "SELECT id FROM uploads WHERE batch_id=%s AND state='verifying' ORDER BY id", (batch,)
    ).fetchall()
    if batch_row["state"] == "verifying" or verifying:
        raise DomainError(
            "BATCH_VERIFICATION_IN_FLIGHT",
            "字节核验仍在进行，请等待完成或明确取消并等终态",
            409,
            {"uploads": clean(verifying), "tasks_cancelled": False},
        )
    receipt = c.execute(
        "INSERT INTO upload_batch_closures(id,bid_id,batch_id,actor_id,command_id,reason) VALUES(gen_random_uuid(),%s,%s,%s,%s,%s) RETURNING *",
        (bid, batch, actor, command_id, reason.strip()),
    ).fetchone()
    c.execute("UPDATE bids SET revision=revision+1 WHERE id=%s", (bid,))
    audit(
        c,
        actor,
        bid,
        "upload.batch_closed_staging_retained",
        {
            "batch_id": batch,
            "closure_id": receipt["id"],
            "bytes_deleted": False,
            "material_committed": False,
        },
        command_id,
    )
    return clean(receipt)


def view(c, bid):
    row = profile(c, bid)
    active = active_operations(c, bid)
    unfinished_uploads, unfinished_batches = unfinished_intents(c, bid)
    allowed = list(NEXT[row["phase"]])
    archive_blockers = []
    if row["phase"] in ("closed", "terminated") and not row["archived_at"]:
        if active:
            archive_blockers.append("OPERATIONS_IN_FLIGHT")
        if unfinished_uploads:
            archive_blockers.append("UPLOADS_UNFINISHED")
        if unfinished_batches:
            archive_blockers.append("BATCHES_UNFINISHED")
        if not archive_blockers:
            allowed.append("archive")
    if row["archived_at"]:
        allowed = ["reopen"]
    return clean(row) | {
        "allowed_actions": allowed,
        "archive_blockers": archive_blockers,
        "active_operations": clean(active),
        "unfinished_uploads": unfinished_uploads,
        "unfinished_batches": unfinished_batches,
        "batch_closure_available": closure_schema_available(c),
    }


def assert_work_writable(c, bid, *, worker=False):
    """Serialize ordinary API work mutations with archive; archival supplements use separate paths."""
    lock(c, bid, worker=worker)
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
        pending_uploads, pending_batches = unfinished_intents(c, bid)
        if pending_uploads or pending_batches:
            raise DomainError(
                "PROJECT_UPLOAD_INTENTS_UNFINISHED",
                "仍有未结束上传或批次；请等待、明确取消上传或结束批次并保留暂存资料",
                409,
                {
                    "unfinished_uploads": pending_uploads,
                    "unfinished_batches": pending_batches,
                    "bytes_deleted": False,
                    "material_committed": False,
                },
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
