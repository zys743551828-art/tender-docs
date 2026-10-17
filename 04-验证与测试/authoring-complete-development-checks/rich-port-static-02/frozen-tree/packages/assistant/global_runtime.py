"""Single-call global execution and budget ledger; never creates a surrogate bid."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.assistant.authority import assert_source_guards
from packages.assistant.generation import request_for, validate_answer
from packages.assistant.jobs import job, require_schema
from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, clean, connection, fetch, sha, uid


def provider():
    return DeepSeekClient.from_env()


def checked_message(c, message):
    if (
        set(message) != {"schema_version", "type", "event_id", "run_id", "input_digest"}
        or message["schema_version"] != 1
        or message["type"] != "assistant-global/1"
    ):
        raise DomainError("ASSISTANT_TASK_INVALID", "全局助手消息协议不符", 409)
    row = job(c, UUID(message["run_id"]))
    stored = fetch(
        c,
        "SELECT * FROM assistant_global_outbox WHERE id=%s AND run_id=%s",
        (UUID(message["event_id"]), row["id"]),
    )
    if (
        row["scope"] != "global"
        or row["kind"] != "answer"
        or row["bid_id"] is not None
        or row["input_digest"] != message["input_digest"]
        or stored["payload"] != message
        or stored["payload_digest"] != sha(message)
    ):
        raise DomainError("ASSISTANT_TASK_INVALID", "全局运行与冻结消息身份不符", 409)
    return row


def acquire(message):
    with connection(True) as c:
        require_schema(c, worker=True)
        row = checked_message(c, message)
        run = fetch(
            c,
            "SELECT *,clock_timestamp() AS checked_at FROM assistant_global_runs WHERE id=%s FOR UPDATE",
            (row["id"],),
        )
        if run["state"] in ("succeeded", "failed", "cancelled"):
            return None
        if run["cancel_requested_at"] is not None:
            c.execute(
                "UPDATE assistant_global_runs SET state='cancelled',finished_at=clock_timestamp(),lease_until=NULL,error_code='CANCELLED' WHERE id=%s",
                (row["id"],),
            )
            return None
        if (
            run["state"] == "running"
            and run["lease_until"]
            and run["lease_until"] > run["checked_at"]
        ):
            return None
        owner = uid()
        fence = run["fence"] + 1
        c.execute(
            "UPDATE assistant_global_runs SET state='running',fence=%s,owner_token=%s,lease_until=clock_timestamp()+interval '120 seconds' WHERE id=%s",
            (fence, owner, row["id"]),
        )
        return {"owner": owner, "fence": fence}


def locked_execution(c, run_id, lease):
    run = fetch(
        c,
        "SELECT *,clock_timestamp() AS checked_at FROM assistant_global_runs WHERE id=%s FOR UPDATE",
        (run_id,),
    )
    if (
        run["state"] != "running"
        or run["fence"] != lease["fence"]
        or run["owner_token"] != lease["owner"]
        or run["lease_until"] is None
        or run["lease_until"] <= run["checked_at"]
        or run["cancel_requested_at"] is not None
    ):
        raise DomainError("MODEL_EXECUTION_STALE", "全局执行权失效或已请求取消，未安排新调用", 409)
    return run


def renew(message, lease):
    with connection(True) as c:
        return (
            c.execute(
                """UPDATE assistant_global_runs SET lease_until=clock_timestamp()+interval '120 seconds'
            WHERE id=%s AND state='running' AND fence=%s AND owner_token=%s
            AND lease_until>clock_timestamp() AND cancel_requested_at IS NULL RETURNING id""",
                (UUID(message["run_id"]), lease["fence"], lease["owner"]),
            ).fetchone()
            is not None
        )


@contextmanager
def heartbeat(message, lease):
    stopped = threading.Event()

    def beat():
        while not stopped.wait(10):
            try:
                if not renew(message, lease):
                    return
            except Exception:
                return  # A lost heartbeat never grants execution; finish rechecks DB fence.

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def reserve(row, lease, request, profile):
    with connection(True) as c:
        # All same logical calls, even in distinct runs, serialize before querying the ledger.
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("assistant-global-call:" + row["intent_digest"] + ":" + sha(profile),),
        )
        run = locked_execution(c, row["id"], lease)
        old = c.execute(
            "SELECT * FROM assistant_global_calls WHERE run_id=%s FOR UPDATE", (row["id"],)
        ).fetchone()
        if old:
            if (
                old["request_digest"] != sha(request)
                or old["profile_digest"] != sha(profile)
                or old["intent_digest"] != row["intent_digest"]
                or old["max_tokens"] != request["max_tokens"]
            ):
                raise DomainError("MODEL_CALL_IDENTITY_CONFLICT", "已有调用与本次输入不一致", 409)
            return old, False
        unresolved = c.execute(
            "SELECT id FROM assistant_global_calls WHERE intent_digest=%s AND profile_digest=%s AND state IN ('reserved','unknown') ORDER BY id LIMIT 1",
            (row["intent_digest"], sha(profile)),
        ).fetchone()
        if unresolved:
            raise DomainError(
                "MODEL_CALL_REQUIRES_RECONCILIATION",
                "同一问题的费用或结果尚未核清，未再次调用",
                409,
                {"call_id": str(unresolved["id"]), "new_attempt_authorized": False},
            )
        if request["max_tokens"] != run["max_tokens"] or not 1 <= request["max_tokens"] <= 8192:
            raise DomainError("MODEL_BUDGET_EXHAUSTED", "请求超出冻结的单次输出预算", 409)
        call = fetch(
            c,
            """INSERT INTO assistant_global_calls(id,run_id,intent_digest,request_digest,profile_digest,max_tokens)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                uid(),
                row["id"],
                row["intent_digest"],
                sha(request),
                sha(profile),
                request["max_tokens"],
            ),
        )
        return call, True


def failure(call_id, error, *, sent, result=None):
    details = error.details if isinstance(error, DomainError) else {}
    billing = (
        "usage_reported"
        if result is not None
        else details.get("billing_state", "unknown" if sent else "not_sent")
    )
    with connection(True) as c:
        c.execute(
            "UPDATE assistant_global_calls SET state=%s,provider_request_id=%s,usage=%s WHERE id=%s AND state IN ('reserved','unknown')",
            (
                "unknown" if billing == "unknown" else "failed",
                result.request_id if result else details.get("provider_request_id"),
                Jsonb(
                    {
                        "billing_state": billing,
                        "actual": result.usage if result else details.get("usage"),
                        "failure_code": error.code
                        if isinstance(error, DomainError)
                        else "ASSISTANT_RESULT_PERSIST_FAILED",
                    }
                ),
                call_id,
            ),
        )


def compute(message, lease):
    client = provider()  # Constructor/config checks outside any DB connection.
    with connection(True) as c:
        row = checked_message(c, message)
        assert_source_guards(c, row["input_manifest"])
    manifest = row["input_manifest"]
    messages, schema = request_for(manifest)
    request = {
        "messages": messages,
        "json_schema": schema,
        "max_tokens": manifest["request"]["max_tokens"],
        "temperature": 0,
        "thinking": "disabled",
    }
    profile = {
        "provider": "deepseek",
        "model": client.model,
        "port": "generation/1",
        "purpose": "assistant",
        "formal_scoring_eligible": False,
    }
    call, created = reserve(row, lease, request, profile)
    if not created:
        if call["state"] != "succeeded" or call["response_ref"] is None:
            raise DomainError(
                "MODEL_CALL_REQUIRES_RECONCILIATION",
                "已有调用未取得可靠结果，未自动再次调用",
                409,
                {"call_id": str(call["id"]), "new_attempt_authorized": False},
            )
        with connection(True) as c:
            blob = fetch(c, "SELECT * FROM blobs WHERE id=%s", (call["response_ref"],))
        raw = storage.get(blob["object_key"])
        if sha(raw) != blob["digest"]:
            raise DomainError("MODEL_RESULT_CORRUPTED", "全局模型结果字节校验失败", 409)
        return {
            "job_id": row["id"],
            "call_id": call["id"],
            "input_digest": row["input_digest"],
            "payload": validate_answer(manifest, json.loads(raw)["data"]),
            "provider_result": json.loads(raw),
        }
    sent = False
    result = None
    try:
        with connection(True) as c:
            locked_execution(c, row["id"], lease)
            assert_source_guards(c, manifest)
        sent = True
        result = client.generate(
            messages=messages, json_schema=schema, max_tokens=request["max_tokens"], temperature=0
        )
        value = {
            "data": result.data,
            "content": result.content,
            "usage": result.usage,
            "request_id": result.request_id,
            "response_id": result.response_id,
            "model": result.model,
        }
        raw = json.dumps(
            clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        key = "global-model-results/" + str(call["id"]) + ".json"
        storage.put(key, raw, "application/json")
        with connection(True) as c:
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/json') ON CONFLICT(digest) DO NOTHING",
                (uid(), sha(raw), key, len(raw)),
            )
            blob = fetch(c, "SELECT id FROM blobs WHERE digest=%s", (sha(raw),))
            c.execute(
                "UPDATE assistant_global_calls SET state='succeeded',provider_request_id=%s,response_ref=%s,usage=%s WHERE id=%s AND state IN ('reserved','unknown')",
                (
                    result.request_id,
                    blob["id"],
                    Jsonb({"billing_state": "usage_reported", "actual": result.usage}),
                    call["id"],
                ),
            )
        return {
            "job_id": row["id"],
            "call_id": call["id"],
            "input_digest": row["input_digest"],
            "payload": validate_answer(manifest, result.data),
            "provider_result": value,
        }
    except Exception as error:
        # Preserve reported billing after a response; never turn parser/persist errors into auto-pay retries.
        if (
            result is None
            or not isinstance(error, DomainError)
            or error.code != "ASSISTANT_CITATION_INVALID"
        ):
            failure(call["id"], error, sent=sent, result=result)
        if isinstance(error, DomainError):
            raise DomainError(
                error.code, error.message, error.status, error.details, retryable=False
            ) from None
        raise DomainError(
            "ASSISTANT_RESULT_UNKNOWN", "助手结果未可靠完成；未自动再次调用", 503, retryable=False
        ) from None


def finish(message, lease, outcome=None, error=None):
    with connection(True) as c:
        row = checked_message(c, message)
        run = fetch(
            c,
            "SELECT *,clock_timestamp() AS checked_at FROM assistant_global_runs WHERE id=%s FOR UPDATE",
            (row["id"],),
        )
        if (
            run["state"] != "running"
            or run["fence"] != lease["fence"]
            or run["owner_token"] != lease["owner"]
            or run["lease_until"] is None
            or run["lease_until"] <= run["checked_at"]
        ):
            return False
        if run["cancel_requested_at"] is not None:
            state, code = "cancelled", "CANCELLED"
        elif error:
            state, code = (
                "failed",
                error.code if isinstance(error, DomainError) else "ASSISTANT_FAILURE",
            )
        else:
            if outcome["job_id"] != row["id"] or outcome["input_digest"] != row["input_digest"]:
                raise DomainError("ASSISTANT_TASK_INVALID", "全局结果与冻结运行不符", 409)
            call = fetch(
                c,
                "SELECT * FROM assistant_global_calls WHERE id=%s AND run_id=%s",
                (outcome["call_id"], row["id"]),
            )
            if call["state"] != "succeeded" or not call["response_ref"]:
                raise DomainError("ASSISTANT_CALL_NOT_SUCCEEDED", "全局调用未可靠入账", 409)
            blob = fetch(c, "SELECT digest FROM blobs WHERE id=%s", (call["response_ref"],))
            if sha(outcome["provider_result"]) != blob["digest"]:
                raise DomainError("ASSISTANT_OUTPUT_CHANGED", "全局候选不对应已入账字节", 409)
            payload = validate_answer(row["input_manifest"], outcome["provider_result"]["data"])
            if payload != outcome["payload"]:
                raise DomainError("ASSISTANT_OUTPUT_CHANGED", "全局候选与模型结果不一致", 409)
            c.execute(
                "INSERT INTO assistant_outputs(job_id,global_call_id,payload,digest) VALUES(%s,%s,%s,%s)",
                (row["id"], call["id"], Jsonb(payload), sha(payload)),
            )
            state, code = "succeeded", None
        c.execute(
            "UPDATE assistant_global_runs SET state=%s,error_code=%s,finished_at=clock_timestamp(),lease_until=NULL WHERE id=%s",
            (state, code, row["id"]),
        )
        return True


def handle(message):
    lease = acquire(message)
    if lease is None:
        return False
    try:
        with heartbeat(message, lease):
            outcome = compute(message, lease)
        return finish(message, lease, outcome)
    except DomainError as error:
        finish(message, lease, error=error)
        return True
