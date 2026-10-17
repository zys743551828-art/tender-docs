"""研究模型调用原子预算与保守对账；不读取任何供应商凭据。"""

from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, fetch, required, uid
from packages.research.core import amount


@contextmanager
def research_connection():
    with psycopg.connect(required("RESEARCH_WORKER_DATABASE_DSN"), row_factory=dict_row) as c:
        c.execute("SET LOCAL search_path=design,public")
        identity = c.execute(
            "SELECT r.rolsuper,pg_has_role(current_user,'tender_research_worker','MEMBER') AS capable,EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='design'::regnamespace AND relowner=r.oid) AS owns FROM pg_roles r WHERE rolname=current_user"
        ).fetchone()
        if identity is None or identity["rolsuper"] or identity["owns"] or not identity["capable"]:
            raise RuntimeError("研究执行必须使用独立非owner限定能力LOGIN")
        yield c


def reserve(*, run_id, message, lease, logical_key, request_digest, profile_digest, reserved_fee):
    fee = amount(reserved_fee)
    if not isinstance(logical_key, str) or not 1 <= len(logical_key) <= 160:
        raise DomainError("RESEARCH_CALL_INVALID", "研究调用身份不合法", 422)
    with research_connection() as c:
        frozen = fetch(
            c,
            "SELECT p.request FROM research_runs r JOIN experiment_previews p ON p.id=r.preview_id WHERE r.id=%s",
            (run_id,),
        )
        if frozen["request"]["profile_digest"] != profile_digest:
            raise DomainError("RESEARCH_PROFILE_CHANGED", "调用模型配置不属于本次冻结研究批次", 409)
        c.execute("SELECT pg_advisory_xact_lock(91144,1)")
        old = c.execute(
            "SELECT id FROM model_calls WHERE operation_id=%s AND logical_call_key=%s AND attempt_no=1",
            (message["operation_id"], logical_key),
        ).fetchone()
        tokens = 1_004_096
        if old is None:
            try:
                rpm, tpm = (
                    int(os.environ.get("RESEARCH_RPM", "10")),
                    int(os.environ.get("RESEARCH_TPM", "2008192")),
                )
                if not 1 <= rpm <= 1000 or not tokens <= tpm <= 100_000_000:
                    raise ValueError
            except ValueError:
                raise DomainError(
                    "RESEARCH_RATE_CONFIG_INVALID", "研究RPM/TPM配置不合法", 503
                ) from None
            used = c.execute(
                "SELECT count(*) AS calls,coalesce(sum(coalesce((usage->'reservation'->>'token_ceiling')::bigint,%s)),0) AS tokens FROM model_calls WHERE research_run_id IS NOT NULL AND created_at>=clock_timestamp()-interval '60 seconds'",
                (tokens,),
            ).fetchone()
            if used["calls"] >= rpm or used["tokens"] + tokens > tpm:
                raise DomainError(
                    "RESEARCH_RATE_LIMITED", "研究调用调度达到RPM/TPM上限，尚未发送请求", 429
                )
        try:
            value = c.execute(
                "SELECT design.reserve_research_call(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s) AS result",
                (
                    run_id,
                    message["operation_id"],
                    message["step_key"],
                    message["input_digest"],
                    message["generation"],
                    lease["fence"],
                    lease["owner"],
                    uid(),
                    logical_key,
                    request_digest,
                    profile_digest,
                    fee,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            if error.sqlstate in {"55000", "23514", "P0002"}:
                raise DomainError(
                    "RESEARCH_RESERVATION_BLOCKED",
                    "执行权、调用身份或批次预算已变化，未安排新请求",
                    409,
                ) from None
            raise
        eligible = c.execute(
            "SELECT s.admission FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id WHERE a.run_id=%s AND a.operation_id=%s",
            (run_id, message["operation_id"]),
        ).fetchone()
        if eligible is None or eligible["admission"] != "admitted":
            raise DomainError("SAMPLE_NOT_ADMITTED", "样本准入已变化，撤销未发送调用的预留", 409)
        if value["new_reservation"]:
            c.execute(
                "UPDATE model_calls SET usage=%s WHERE id=%s",
                (Jsonb({"reservation": {"token_ceiling": tokens}}), value["call_id"]),
            )
        return value


def settle(
    *,
    call_id,
    state,
    billed_fee=None,
    provider_request_id=None,
    usage=None,
    response_ref=None,
    fee_basis="provider_confirmed",
):
    """未知计费保留reserved；真实超额入spent并暂停；重复结算不重复扣费。"""
    if state not in {"succeeded", "failed", "unknown"}:
        raise DomainError("RESEARCH_SETTLEMENT_INVALID", "研究账单状态不合法", 422)
    fee = None if billed_fee is None else amount(billed_fee)
    if fee_basis not in {"provider_confirmed", "usage_price_estimate", "confirmed_not_sent"}:
        raise DomainError("RESEARCH_SETTLEMENT_INVALID", "费用依据不合法", 422)
    if state == "unknown" and fee is not None:
        raise DomainError("RESEARCH_SETTLEMENT_INVALID", "未知计费不得同时填写确认金额", 422)
    if state != "unknown" and fee is None:
        raise DomainError(
            "RESEARCH_SETTLEMENT_INVALID", "确认结算必须有真实金额，未知费用不能填零", 422
        )
    if state == "succeeded" and response_ref is None:
        raise DomainError("RESEARCH_RESULT_REQUIRED", "成功调用必须绑定摘要校验的实际产物", 422)
    with research_connection() as c:
        initial = fetch(
            c,
            "SELECT o.bid_id,o.id AS operation_id FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE m.id=%s AND m.research_run_id IS NOT NULL",
            (call_id,),
        )
        c.execute("SELECT design.lock_worker_bid(%s)", (initial["bid_id"],))
        fetch(c, "SELECT id FROM operations WHERE id=%s FOR UPDATE", (initial["operation_id"],))
        call = fetch(c, "SELECT * FROM model_calls WHERE id=%s FOR UPDATE", (call_id,))
        run = fetch(
            c, "SELECT * FROM research_runs WHERE id=%s FOR UPDATE", (call["research_run_id"],)
        )
        previous = call["usage"] or {}
        settlement = {
            "state": state,
            "billed_fee": None if fee is None else str(fee),
            "provider_request_id": provider_request_id,
            "usage": usage or {},
            "response_ref": str(response_ref) if response_ref else None,
            "fee_basis": fee_basis,
        }
        if previous.get("settlement"):
            if previous["settlement"] == settlement:
                return {"replayed": True, "state": state}
            if call["state"] != "unknown":
                raise DomainError("RESEARCH_SETTLEMENT_CONFLICT", "同一调用已有不同的确认账单", 409)
        if state == "unknown":
            c.execute(
                "UPDATE model_calls SET state='unknown',provider_request_id=%s,usage=%s WHERE id=%s",
                (provider_request_id, Jsonb(previous | {"settlement": settlement}), call_id),
            )
            c.execute(
                "UPDATE research_runs SET state=CASE WHEN state='cancelled' THEN state ELSE 'paused' END,revision=revision+1 WHERE id=%s",
                (run["id"],),
            )
        else:
            if response_ref is not None:
                fetch(c, "SELECT id FROM blobs WHERE id=%s", (response_ref,))
            c.execute(
                "UPDATE model_calls SET state=%s,provider_request_id=%s,usage=%s,response_ref=%s WHERE id=%s",
                (
                    state,
                    provider_request_id,
                    Jsonb(previous | {"settlement": settlement}),
                    response_ref,
                    call_id,
                ),
            )
            c.execute(
                "UPDATE research_runs SET spent_fee=spent_fee+%s,reserved_fee=reserved_fee-%s,revision=revision+1,state=CASE WHEN spent_fee+%s+reserved_fee-%s>fee_limit AND state<>'cancelled' THEN 'paused' ELSE state END WHERE id=%s",
                (fee, call["reserved_amount"], fee, call["reserved_amount"], run["id"]),
            )
        return {
            "replayed": False,
            "state": state,
            "billing_state": "unknown" if fee is None else fee_basis,
        }
