"""独立研究topic调度、执行权及结果提交；调用端口由受控模型服务提供。"""

from __future__ import annotations

from datetime import timedelta

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, clean, fetch, now, sha, uid
from packages.research.artifacts import commit_stage, stage_json
from packages.research.authority import qualify
from packages.research.batches import TOPIC
from packages.research.ledger import research_connection


def schedule_one(run_id):
    """bid→operation→run锁序；从不持run锁再取bid锁。"""
    with research_connection() as c:
        candidate = c.execute(
            "SELECT sample_id,bid_id,repeat_index FROM research_run_items WHERE run_id=%s AND state='pending' ORDER BY sample_id,repeat_index LIMIT 1",
            (run_id,),
        ).fetchone()
        if not candidate:
            return None
        c.execute("SELECT design.lock_worker_bid(%s)", (candidate["bid_id"],))
        run = fetch(
            c,
            "SELECT r.*,p.request,p.created_by FROM research_runs r JOIN experiment_previews p ON p.id=r.preview_id WHERE r.id=%s FOR UPDATE OF r",
            (run_id,),
        )
        if run["state"] not in {"queued", "running"} or run["cancel_requested_at"] is not None:
            return None
        active = c.execute(
            "SELECT count(*) AS n FROM research_run_items WHERE run_id=%s AND state='running'",
            (run_id,),
        ).fetchone()["n"]
        if active >= run["request"]["concurrency"]:
            return None
        if run["spent_fee"] + run["reserved_fee"] >= run["fee_limit"]:
            c.execute(
                "UPDATE research_runs SET state='paused',revision=revision+1 WHERE id=%s", (run_id,)
            )
            return None
        item = c.execute(
            "SELECT * FROM research_run_items WHERE run_id=%s AND sample_id=%s AND repeat_index=%s AND state='pending' FOR UPDATE",
            (run_id, candidate["sample_id"], candidate["repeat_index"]),
        ).fetchone()
        if item is None:
            return None
        attempt = c.execute(
            "SELECT coalesce(max(attempt_no),0)+1 AS n FROM research_run_attempts WHERE run_id=%s AND sample_id=%s AND repeat_index=%s",
            (run_id, item["sample_id"], item["repeat_index"]),
        ).fetchone()["n"]
        if attempt > 3:
            c.execute(
                "UPDATE research_run_items SET state='failed',error_code='ATTEMPT_LIMIT' WHERE run_id=%s AND sample_id=%s AND repeat_index=%s",
                (run_id, item["sample_id"], item["repeat_index"]),
            )
            return None
        command, operation = uid(), uid()
        payload = {
            "run_id": str(run_id),
            "sample_id": str(item["sample_id"]),
            "repeat_index": item["repeat_index"],
            "attempt": attempt,
            "manifest_digest": run["manifest_digest"],
        }
        digest = sha(payload)
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'research-sample-attempt',%s,%s,%s)",
            (
                command,
                run["created_by"],
                item["sample_id"],
                f"{run_id}:{item['repeat_index']}:{attempt}",
                digest,
            ),
        )
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'research-judgment',%s,'queued','research/1')",
            (operation, item["bid_id"], command, digest),
        )
        c.execute(
            "INSERT INTO operation_steps(operation_id,step_key,input_digest,handler_version,required,depends_on,dispatch_generation) VALUES(%s,'research-judgment',%s,'research/1',true,'{}',1)",
            (operation, digest),
        )
        c.execute(
            "INSERT INTO research_run_attempts(run_id,sample_id,repeat_index,bid_id,attempt_no,operation_id) VALUES(%s,%s,%s,%s,%s,%s)",
            (run_id, item["sample_id"], item["repeat_index"], item["bid_id"], attempt, operation),
        )
        c.execute(
            "UPDATE research_run_items SET state='running',operation_id=%s WHERE run_id=%s AND sample_id=%s AND repeat_index=%s",
            (operation, run_id, item["sample_id"], item["repeat_index"]),
        )
        c.execute(
            "UPDATE research_runs SET state='running',revision=revision+1 WHERE id=%s", (run_id,)
        )
        message = {
            "schema_version": 1,
            "event_type": "step.requested",
            "event_id": str(uid()),
            "operation_id": str(operation),
            "bid_id": str(item["bid_id"]),
            "step_key": "research-judgment",
            "input_digest": digest,
            "generation": 1,
            "handler_version": "research/1",
            "payload_ref": str(item["sample_id"]),
            "created_at": now().isoformat(),
        }
        c.execute(
            "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload) VALUES(%s,%s,%s,%s,%s)",
            (
                message["event_id"],
                operation,
                TOPIC,
                str(operation) + ":research-judgment:1",
                Jsonb(message),
            ),
        )
        return message


def acquire(message):
    if (
        message.get("step_key") != "research-judgment"
        or message.get("handler_version") != "research/1"
    ):
        raise DomainError("RESEARCH_MESSAGE_INVALID", "消息不是本研究处理器的冻结计划", 422)
    with research_connection() as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
        operation = fetch(
            c,
            "SELECT * FROM operations WHERE bid_id=%s AND id=%s FOR UPDATE",
            (message["bid_id"], message["operation_id"]),
        )
        step = fetch(
            c,
            "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (operation["id"], message["step_key"], message["input_digest"]),
        )
        if step["dispatch_generation"] != message["generation"] or operation["state"] not in {
            "queued",
            "running",
        }:
            return None
        previous = c.execute(
            "SELECT * FROM step_receipts WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (operation["id"], message["step_key"], message["input_digest"]),
        ).fetchone()
        if previous:
            # 研究模型请求的过期租约不得自动重发；人工恢复追加operation并保留预算。
            return None
        attempt = fetch(
            c, "SELECT * FROM research_run_attempts WHERE operation_id=%s", (operation["id"],)
        )
        run = fetch(c, "SELECT * FROM research_runs WHERE id=%s FOR UPDATE", (attempt["run_id"],))
        if run["state"] != "running" or run["cancel_requested_at"] is not None:
            return None
        owner = uid()
        c.execute(
            "INSERT INTO step_receipts(operation_id,step_key,input_digest,fence,attempt_count,state,heartbeat_at,owner_token,lease_until) VALUES(%s,%s,%s,1,1,'running',now(),%s,%s)",
            (
                operation["id"],
                message["step_key"],
                message["input_digest"],
                owner,
                now() + timedelta(seconds=120),
            ),
        )
        c.execute("UPDATE operations SET state='running' WHERE id=%s", (operation["id"],))
        return {
            "owner": owner,
            "fence": 1,
            "run_id": attempt["run_id"],
            "sample_id": attempt["sample_id"],
            "repeat_index": attempt["repeat_index"],
        }


def finish(message, lease, *, result=None, error_code=None):
    if (result is None) == (error_code is None):
        raise ValueError("Exactly one of result and error_code is required")
    staged = (
        stage_json(clean(result), "application/vnd.tender.research-result+json")
        if result is not None
        else None
    )
    with research_connection() as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
        operation = fetch(
            c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (message["operation_id"],)
        )
        step = fetch(
            c,
            "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (operation["id"], message["step_key"], message["input_digest"]),
        )
        if step["dispatch_generation"] != message["generation"]:
            raise DomainError("RESEARCH_EXECUTION_STALE", "研究派发代次已变化，不接受迟到结果", 409)
        receipt = c.execute(
            "SELECT * FROM step_receipts WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND state='running' AND fence=%s AND owner_token=%s AND lease_until>clock_timestamp() FOR UPDATE",
            (
                operation["id"],
                message["step_key"],
                message["input_digest"],
                lease["fence"],
                lease["owner"],
            ),
        ).fetchone()
        if receipt is None or operation["state"] not in {"running", "cancel_requested"}:
            raise DomainError("RESEARCH_EXECUTION_STALE", "研究执行权已失效，不接受迟到结果", 409)
        run = fetch(c, "SELECT * FROM research_runs WHERE id=%s FOR UPDATE", (lease["run_id"],))
        cancelled = (
            run["cancel_requested_at"] is not None or operation["cancel_requested_at"] is not None
        )
        state = "cancelled" if cancelled else "failed" if error_code else "succeeded"
        blob_id = None
        if result is not None and not cancelled:
            qualify(c, message, lease)
            blob_id = commit_stage(c, staged)
        c.execute(
            "UPDATE step_receipts SET state=%s,output=%s,failure_code=%s,owner_token=NULL,lease_until=NULL WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
            (
                state,
                Jsonb({"result_blob_id": str(blob_id) if blob_id else None}),
                error_code,
                operation["id"],
                message["step_key"],
                message["input_digest"],
            ),
        )
        c.execute(
            "UPDATE operations SET state=%s,finished_at=now(),cancel_requested_at=CASE WHEN %s THEN coalesce(cancel_requested_at,now()) ELSE cancel_requested_at END WHERE id=%s",
            (state, cancelled, operation["id"]),
        )
        c.execute(
            "UPDATE research_run_items SET state=%s,error_code=%s,result_blob_id=%s WHERE run_id=%s AND sample_id=%s AND repeat_index=%s AND operation_id=%s",
            (
                state,
                error_code,
                blob_id,
                lease["run_id"],
                lease["sample_id"],
                lease["repeat_index"],
                operation["id"],
            ),
        )
        unfinished = c.execute(
            "SELECT count(*) AS n,count(*) FILTER(WHERE state='failed') AS failed FROM research_run_items WHERE run_id=%s AND state<>'succeeded'",
            (run["id"],),
        ).fetchone()
        active = c.execute(
            "SELECT count(*) AS n FROM research_run_items WHERE run_id=%s AND state IN ('pending','running')",
            (run["id"],),
        ).fetchone()["n"]
        if run["state"] not in {"cancelled", "paused"} and active == 0:
            c.execute(
                "UPDATE research_runs SET state=%s,revision=revision+1 WHERE id=%s",
                (
                    "cancelled"
                    if run["cancel_requested_at"] is not None
                    else "succeeded"
                    if unfinished["n"] == 0
                    else "failed",
                    run["id"],
                ),
            )
        elif run["cancel_requested_at"] is not None and active == 0:
            c.execute(
                "UPDATE research_runs SET state='cancelled',revision=revision+1 WHERE id=%s",
                (run["id"],),
            )
        else:
            c.execute("UPDATE research_runs SET revision=revision+1 WHERE id=%s", (run["id"],))
        return {"state": state, "result_blob_id": str(blob_id) if blob_id else None}
