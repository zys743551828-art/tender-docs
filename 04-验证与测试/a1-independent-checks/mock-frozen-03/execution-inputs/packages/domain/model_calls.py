"""普通业务模型单次调用账本；不借用研究run，不自动补发未知计费请求。"""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient, ProviderError
from packages.domain.runtime import DomainError, clean, connection, fetch, sha, uid

PIPELINE_CALL_LIMIT = 10
PIPELINE_OUTPUT_TOKEN_LIMIT = 32768


def lock_execution(c, message, lease):
    c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
    op = fetch(
        c,
        "SELECT * FROM operations WHERE id=%s AND bid_id=%s FOR UPDATE",
        (message["operation_id"], message["bid_id"]),
    )
    plan = fetch(
        c,
        "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
        (op["id"], message["step_key"], message["input_digest"]),
    )
    receipt = c.execute(
        "SELECT * FROM step_receipts WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND state='running' AND fence=%s AND owner_token=%s AND lease_until>clock_timestamp() FOR UPDATE",
        (op["id"], message["step_key"], message["input_digest"], lease["fence"], lease["owner"]),
    ).fetchone()
    if (
        op["state"] != "running"
        or op["cancel_requested_at"] is not None
        or plan["dispatch_generation"] != message["generation"]
        or receipt is None
    ):
        raise DomainError("MODEL_EXECUTION_STALE", "执行权已失效，未安排新的模型调用", 409)
    return op


def reserve(message, lease, *, logical_key, request_digest, profile_digest, max_tokens):
    if (
        not isinstance(logical_key, str)
        or not 1 <= len(logical_key) <= 160
        or type(max_tokens) is not int
        or not 1 <= max_tokens <= 8192
    ):
        raise DomainError("MODEL_CALL_INVALID", "模型调用身份或输出预算不合法", 422)
    with connection(True) as c:
        op = lock_execution(c, message, lease)
        old = c.execute(
            "SELECT * FROM model_calls WHERE operation_id=%s AND logical_call_key=%s AND attempt_no=1 FOR UPDATE",
            (op["id"], logical_key),
        ).fetchone()
        if old:
            if (
                old["request_digest"] != request_digest
                or old["profile_digest"] != profile_digest
                or old["research_run_id"] is not None
                or old["reserved_amount"] is not None
            ):
                raise DomainError("MODEL_CALL_IDENTITY_CONFLICT", "既有调用与本次输入不一致", 409)
            return old, False
        unresolved = c.execute(
            "SELECT mc.id FROM model_calls mc JOIN operations other ON other.id=mc.operation_id WHERE other.bid_id=%s AND mc.logical_call_key=%s AND mc.request_digest=%s AND mc.profile_digest=%s AND mc.state IN('reserved','unknown') ORDER BY mc.id LIMIT 1",
            (op["bid_id"], logical_key, request_digest, profile_digest),
        ).fetchone()
        if unresolved:
            raise DomainError(
                "MODEL_CALL_REQUIRES_RECONCILIATION",
                "同一模型请求此前是否完成或计费尚未核清，未再次发起请求",
                409,
                {"call_id": str(unresolved["id"]), "new_attempt_authorized": False},
            )
        # 同一用户命令派生的prepare和review共用预算；bid锁串行化预算与INSERT。
        budget = c.execute(
            "SELECT count(*) AS calls,coalesce(sum((mc.usage->'reservation'->>'max_tokens')::integer),0) AS tokens FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s AND o.command_id=%s",
            (op["bid_id"], op["command_id"]),
        ).fetchone()
        if (
            budget["calls"] >= PIPELINE_CALL_LIMIT
            or budget["tokens"] + max_tokens > PIPELINE_OUTPUT_TOKEN_LIMIT
        ):
            raise DomainError("MODEL_BUDGET_EXHAUSTED", "本次请求的模型次数或输出预算已用尽", 409)
        call_id = uid()
        reservation = {
            "max_tokens": max_tokens,
            "step_key": message["step_key"],
            "input_digest": message["input_digest"],
            "generation": message["generation"],
            "fence": lease["fence"],
            "owner": str(lease["owner"]),
        }
        row = c.execute(
            "INSERT INTO model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state,usage) VALUES(%s,%s,%s,1,%s,%s,'reserved',%s) RETURNING *",
            (
                call_id,
                op["id"],
                logical_key,
                request_digest,
                profile_digest,
                Jsonb({"reservation": reservation, "billing_state": "not_reported"}),
            ),
        ).fetchone()
        return row, True


def _record_failure(call_id, error, *, sent):
    details = error.details if isinstance(error, DomainError) else {}
    billing = details.get("billing_state", "unknown" if sent else "not_sent")
    state = "unknown" if billing == "unknown" else "failed"
    with connection(True) as c:
        c.execute(
            "UPDATE model_calls SET state=%s,provider_request_id=%s,usage=usage||%s WHERE id=%s AND state IN ('reserved','unknown')",
            (
                state,
                details.get("provider_request_id"),
                Jsonb(
                    {
                        "billing_state": billing,
                        "actual": details.get("usage"),
                        "failure_code": error.code
                        if isinstance(error, DomainError)
                        else "MODEL_RESULT_PERSIST_FAILED",
                    }
                ),
                call_id,
            ),
        )


def generate(message, lease, *, logical_key, messages, json_schema, max_tokens=2048):
    """只让新预留的调用发请求；重复reserved/unknown永不自动补发。"""
    client = DeepSeekClient.from_env()
    request = {
        "messages": messages,
        "json_schema": json_schema,
        "max_tokens": max_tokens,
        "temperature": 0,
        "thinking": "disabled",
    }
    profile = {
        "provider": "deepseek",
        "model": client.model,
        "port": "generation/1",
        "purpose": "core_review",
        "formal_scoring_eligible": False,
    }
    call, created = reserve(
        message,
        lease,
        logical_key=logical_key,
        request_digest=sha(request),
        profile_digest=sha(profile),
        max_tokens=max_tokens,
    )
    if not created:
        if call["state"] == "succeeded" and call["response_ref"]:
            with connection(True) as c:
                blob = fetch(c, "SELECT * FROM blobs WHERE id=%s", (call["response_ref"],))
            raw = storage.get(blob["object_key"])
            if sha(raw) != blob["digest"]:
                raise DomainError("MODEL_RESULT_CORRUPTED", "已记录模型结果的字节核验失败", 409)
            return json.loads(raw), call["id"]
        raise DomainError(
            "MODEL_CALL_REQUIRES_RECONCILIATION",
            "既有模型请求未取得可复用结果，未自动重新计费",
            409,
            {
                "call_id": str(call["id"]),
                "billing_state": (call["usage"] or {}).get("billing_state", "unknown"),
            },
        )
    sent, result = False, None
    recorded: DomainError | None = None
    try:
        # 新预留提交后再次核验取消和租约；数据库锁不持到远端响应。
        with connection(True) as c:
            lock_execution(c, message, lease)
        sent = True
        result = client.generate(
            messages=messages, max_tokens=max_tokens, json_schema=json_schema, temperature=0
        )
        value = {
            "content": result.content,
            "data": result.data,
            "usage": result.usage,
            "request_id": result.request_id,
            "response_id": result.response_id,
            "model": result.model,
        }
        raw = json.dumps(
            clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        digest, blob_id = sha(raw), uid()
        object_key = "model-results/" + str(call["id"]) + ".json"
        storage.put(object_key, raw, "application/json")
        with connection(True) as c:
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/json') ON CONFLICT(digest) DO NOTHING",
                (blob_id, digest, object_key, len(raw)),
            )
            existing = fetch(c, "SELECT id FROM blobs WHERE digest=%s", (digest,))
            c.execute(
                "UPDATE model_calls SET state='succeeded',provider_request_id=%s,response_ref=%s,usage=usage||%s WHERE id=%s AND state IN ('reserved','unknown')",
                (
                    result.request_id,
                    existing["id"],
                    Jsonb({"billing_state": "usage_reported", "actual": result.usage}),
                    call["id"],
                ),
            )
        return value, call["id"]
    except ProviderError as error:
        _record_failure(call["id"], error, sent=sent)
        raise
    except DomainError as error:
        recorded = (
            ProviderError(
                "MODEL_RESULT_PERSIST_FAILED",
                "模型结果未可靠保存",
                billing_state="usage_reported",
                request_id=result.request_id,
                usage=result.usage,
            )
            if result is not None
            else error
        )
        _record_failure(call["id"], recorded, sent=sent)
        # 本地错误不能借通用Worker重试自动重复付费。
        raise DomainError(
            error.code, error.message, error.status, error.details, retryable=False
        ) from None
    except Exception:
        recorded = (
            ProviderError(
                "MODEL_RESULT_PERSIST_FAILED",
                "模型结果未可靠保存",
                billing_state="usage_reported",
                request_id=result.request_id,
                usage=result.usage,
            )
            if result is not None
            else None
        )
        _record_failure(call["id"], recorded, sent=sent)
        raise DomainError(
            "MODEL_RESULT_PERSIST_FAILED",
            "模型调用结果未能可靠保存，请核对调用账本；未自动补发",
            503,
        ) from None
