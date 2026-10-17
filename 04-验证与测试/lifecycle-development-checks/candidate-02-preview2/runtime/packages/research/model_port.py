"""受控研究判断端口：注入已配置客户端，不读凭据，不自行建立HTTP客户端。"""

from __future__ import annotations

import json
import time
from decimal import Decimal

from packages.domain import storage
from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.research import ledger
from packages.research.artifacts import commit_stage, stage_json
from packages.research.batches import price_config
from packages.research.profile import JUDGMENT_SCHEMA, SYSTEM_PROMPT, descriptor, digest

RESPONSE_MEDIA = "application/vnd.tender.research-model-response+json"


def usage_estimate(usage, prices):
    if (
        not isinstance(usage, dict)
        or any(
            type(usage.get(k)) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        or usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]
    ):
        raise DomainError("RESEARCH_USAGE_UNKNOWN", "模型usage尚未核实，保留预留费用", 409)
    return (
        Decimal(usage["prompt_tokens"]) * Decimal(prices["input_per_million"])
        + Decimal(usage["completion_tokens"]) * Decimal(prices["output_per_million"])
    ) / Decimal(1_000_000)


class ControlledJudgmentPort:
    """客户端必须显式注入；只有reserve的新收据可以触发一次generate。"""

    def __init__(self, *, run_id, message, lease, transport):
        self.run_id, self.message, self.lease, self.transport = run_id, message, lease, transport

    def judge(self, *, package, replicate, logical_key):
        profile = descriptor()
        if (
            getattr(self.transport, "model", None) != profile["model_alias"]
            or package.get("method_digest") != digest()
        ):
            raise DomainError("RESEARCH_PROFILE_CHANGED", "研究模型别名或方法与冻结配置不一致", 409)
        if (
            replicate not in {1, 2, 3}
            or logical_key != f"{package['digest']}:replicate:{replicate}"
        ):
            raise DomainError("RESEARCH_CALL_IDENTITY_INVALID", "研究判断身份不合法", 422)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(package, ensure_ascii=False, sort_keys=True)},
        ]
        # 与已配置生成端口的输入保护一致：不截断，不把超限原件假装读完。
        if sum(len(item["content"]) for item in messages) > 262144:
            raise DomainError(
                "SCORING_CONTEXT_TOO_LARGE", "完整章节超出当前端口限制，须先实现可追溯子节处理", 413
            )
        prices = price_config()
        if prices["profile_digest"] != digest():
            raise DomainError(
                "RESEARCH_PRICE_PROFILE_MISMATCH", "价格快照与研究模型配置不一致", 409
            )
        request = {
            "messages": messages,
            "json_schema": JUDGMENT_SCHEMA,
            "max_tokens": profile["max_output_tokens"],
            "temperature": profile["temperature"],
        }
        reservation = (
            Decimal(prices["context_tokens"]) * Decimal(prices["input_per_million"])
            + Decimal(prices["max_output_tokens"]) * Decimal(prices["output_per_million"])
        ) / Decimal(1_000_000)
        deadline = time.monotonic() + 65
        while True:
            try:
                call = ledger.reserve(
                    run_id=self.run_id,
                    message=self.message,
                    lease=self.lease,
                    logical_key=logical_key,
                    request_digest=sha(request),
                    profile_digest=digest(),
                    reserved_fee=reservation,
                )
                break
            except DomainError as error:
                # 只等待尚未获预留、没有发出请求的本地调度限额；绝不重试供应商请求。
                if error.code != "RESEARCH_RATE_LIMITED" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        if not call["new_reservation"]:
            with ledger.research_connection() as c:
                row = fetch(c, "SELECT * FROM model_calls WHERE id=%s", (call["call_id"],))
                if row["state"] != "succeeded" or not row["response_ref"]:
                    raise DomainError(
                        "RESEARCH_CALL_RECONCILIATION_REQUIRED",
                        "已有调用无可复用结果，未自动重复计费",
                        409,
                    )
                blob = fetch(c, "SELECT * FROM blobs WHERE id=%s", (row["response_ref"],))
            raw = storage.get(blob["object_key"])
            if sha(raw) != blob["digest"] or len(raw) != blob["byte_size"]:
                raise DomainError("RESEARCH_RESULT_CORRUPTED", "已有模型响应实际字节核验失败", 409)
            return json.loads(raw)["data"], str(call["call_id"])
        try:
            generated = self.transport.generate(**request)
        except Exception as error:
            details = error.details if isinstance(error, DomainError) else {}
            if details.get("billing_state") == "not_sent":
                ledger.settle(
                    call_id=call["call_id"],
                    state="failed",
                    billed_fee="0",
                    fee_basis="confirmed_not_sent",
                    provider_request_id=details.get("provider_request_id"),
                )
            else:
                ledger.settle(
                    call_id=call["call_id"],
                    state="unknown",
                    provider_request_id=details.get("provider_request_id"),
                )
            raise DomainError(
                "RESEARCH_MODEL_CALL_FAILED",
                "研究模型请求未完成，未重试；请核查执行与计费记录",
                409,
            ) from None
        try:
            if generated.model != profile["model_alias"]:
                raise DomainError("RESEARCH_PROFILE_CHANGED", "供应商响应模型不属于冻结配置", 409)
            fee = usage_estimate(generated.usage, prices)
            response = {
                "data": generated.data,
                "content": generated.content,
                "model": generated.model,
                "response_id": generated.response_id,
                "request_id": generated.request_id,
                "usage": generated.usage,
                "finish_reason": generated.finish_reason,
                "profile_digest": digest(),
                "request_digest": sha(request),
                "fee_basis": "usage_price_estimate",
                "formal_scoring_eligible": False,
                "source_authority_digest": call["authority_digest"],
            }
            staged = stage_json(clean(response), RESPONSE_MEDIA)
            with ledger.research_connection() as c:
                blob = commit_stage(c, staged)
            ledger.settle(
                call_id=call["call_id"],
                state="succeeded",
                billed_fee=fee,
                fee_basis="usage_price_estimate",
                provider_request_id=generated.request_id,
                usage=generated.usage,
                response_ref=blob,
            )
            return generated.data, str(call["call_id"])
        except Exception:
            ledger.settle(
                call_id=call["call_id"],
                state="unknown",
                provider_request_id=getattr(generated, "request_id", None),
            )
            raise DomainError(
                "RESEARCH_RESULT_PERSIST_FAILED",
                "模型响应尚未可靠落库或核验，保留费用且未自动重发",
                409,
            ) from None
