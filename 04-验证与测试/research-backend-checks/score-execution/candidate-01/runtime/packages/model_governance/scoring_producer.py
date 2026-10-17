"""Dedicated score execution, independent of research and ordinary core call ledgers.

A runtime authority must derive owned input/chapters from a live fenced job. It exposes
only clean content and bounded accounting RPCs, never labels, proof files or bucket keys.
There is deliberately no default transport, credentials loader or general consumer here.
"""

import json
from decimal import Decimal

from packages.domain.runtime import DomainError, clean, sha
from packages.model_governance import features
from packages.research.core import assert_clean_structure, judge_three
from packages.research.model_port import usage_estimate
from packages.research.profile import JUDGMENT_SCHEMA, SYSTEM_PROMPT, descriptor, digest


def request_for(package):
    assert_clean_structure(package)
    if package.get("method_digest") != digest() or package.get("digest") != sha(
        {key: value for key, value in package.items() if key != "digest"}
    ):
        raise DomainError("SCORE_METHOD_MISMATCH", "评分包与冻结三判断方法身份不匹配", 409)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(package, ensure_ascii=False, sort_keys=True)},
    ]
    if sum(len(item["content"]) for item in messages) > 262144:
        raise DomainError(
            "SCORING_CONTEXT_TOO_LARGE", "完整评分范围超过当前端口上限，不能截断后继续", 413
        )
    profile = descriptor()
    return {
        "messages": messages,
        "json_schema": JUDGMENT_SCHEMA,
        "max_tokens": profile["max_output_tokens"],
        "temperature": profile["temperature"],
    }


def conservative_fee(prices):
    profile = descriptor()
    if (
        set(prices)
        != {
            "profile_digest",
            "currency",
            "context_tokens",
            "max_output_tokens",
            "input_per_million",
            "output_per_million",
        }
        or prices["profile_digest"] != digest()
        or prices["currency"] != "USD"
        or type(prices["context_tokens"]) is not int
        or type(prices["max_output_tokens"]) is not int
        or prices["context_tokens"] != profile["context_tokens"]
        or prices["max_output_tokens"] != profile["max_output_tokens"]
    ):
        raise DomainError(
            "SCORE_PRICE_PROFILE_MISMATCH", "预留与实际发送必须使用同一完整冻结模型参数", 409
        )
    rates = [Decimal(str(prices[key])) for key in ("input_per_million", "output_per_million")]
    if any(not value.is_finite() or value <= 0 for value in rates):
        raise DomainError("SCORE_PRICE_PROFILE_MISMATCH", "费用上界须为明确正数公开定价", 409)
    return (
        Decimal(profile["context_tokens"]) * rates[0]
        + Decimal(profile["max_output_tokens"]) * rates[1]
    ) / Decimal(1_000_000)


class ControlledScorePort:
    """One successful new reservation permits one send; unknown never gets a new nonce."""

    def __init__(self, *, authority, lease, snapshot, transport):
        self.authority, self.lease, self.snapshot, self.transport = (
            authority,
            lease,
            snapshot,
            transport,
        )
        self.reserve_fee = conservative_fee(snapshot["price_config"])
        self.allowed = {entry["package"]["digest"]: entry["id"] for entry in snapshot["chapters"]}
        if len(self.allowed) != len(snapshot["chapters"]):
            raise DomainError("SCORE_DUPLICATE_CHAPTER", "必要评分项不能重复冻结", 409)

    def judge(self, *, package, replicate, logical_key):
        if (
            package["digest"] not in self.allowed
            or type(replicate) is not int
            or replicate not in {1, 2, 3}
            or logical_key != f"{package['digest']}:replicate:{replicate}"
            or getattr(self.transport, "model", None) != descriptor()["model_alias"]
        ):
            raise DomainError(
                "SCORE_CALL_IDENTITY_INVALID", "评分执行身份、独立重复或模型不匹配", 409
            )
        request = request_for(package)
        reservation = self.authority.reserve(
            self.lease,
            chapter_id=self.allowed[package["digest"]],
            replicate=replicate,
            request_digest=sha(request),
            profile_digest=digest(),
            reserved_fee=str(self.reserve_fee),
        )
        if not reservation["new_reservation"]:
            # Only the bounded owned RPC can supply a previously recorded successful
            # response. An unresolved reserved/unknown call must fail here, never resend.
            recorded = self.authority.success(self.lease, reservation["call_id"])
            if (
                recorded["request_digest"] != sha(request)
                or recorded["profile_digest"] != digest()
                or recorded["execution_purpose"] != "score"
            ):
                raise DomainError(
                    "SCORE_RESULT_IDENTITY_INVALID", "既有响应不属于本次独立评分请求", 409
                )
            return recorded["data"], str(reservation["call_id"])
        try:
            generated = self.transport.generate(**request)
        except Exception as error:
            details = error.details if isinstance(error, DomainError) else {}
            state = "not_sent" if details.get("billing_state") == "not_sent" else "unknown"
            self.authority.settle(
                reservation["call_id"],
                self.lease,
                state=state,
                response=None,
                estimated_fee="0" if state == "not_sent" else None,
            )
            raise DomainError(
                "SCORE_CALL_UNRESOLVED", "评分请求未完成，未补发；未知计费保留预留并待对账", 409
            ) from None
        try:
            if generated.model != descriptor()["model_alias"]:
                raise DomainError("SCORE_PROFILE_CHANGED", "供应商返回模型与冻结方法不一致", 409)
            fee = usage_estimate(generated.usage, self.snapshot["price_config"])
            response = clean(
                {
                    "schema": "controlled-score-response/1",
                    "execution_purpose": "score",
                    "data": generated.data,
                    "content": generated.content,
                    "usage": generated.usage,
                    "model": generated.model,
                    "request_id": generated.request_id,
                    "response_id": generated.response_id,
                    "finish_reason": generated.finish_reason,
                    "request_digest": sha(request),
                    "profile_digest": digest(),
                    "formal_scoring_eligible": False,
                    "fee_basis": "usage_price_estimate",
                }
            )
            raw = json.dumps(
                response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            if len(raw.encode()) > 2097152:
                raise DomainError(
                    "SCORE_RESPONSE_TOO_LARGE", "响应超出受控持久化上限，保留费用待对账", 409
                )
        except Exception:
            self.authority.settle(
                reservation["call_id"],
                self.lease,
                state="unknown",
                response=None,
                estimated_fee=None,
            )
            raise DomainError(
                "SCORE_RESPONSE_UNVERIFIED", "响应或计费无法核实，未重发或记零", 409
            ) from None
        # If this database write fails, its original reserve stays unresolved. No catch
        # sends the provider again or invents a successful receipt.
        self.authority.settle(
            reservation["call_id"],
            self.lease,
            state="succeeded",
            response=raw,
            estimated_fee=str(fee),
        )
        return generated.data, str(reservation["call_id"])


def execute(authority, lease, *, transport):
    """Load owned clean scope, complete every rule, then requalify at atomic publication.

    Publication is authority-controlled. A source change during compute may still settle
    truthful usage but cannot publish old success. There is no object I/O in SQL scopes.
    """
    snapshot = authority.input(lease)
    if (
        snapshot["execution_purpose"] != "score"
        or snapshot["method_digest"] != digest()
        or not snapshot["chapters"]
    ):
        raise DomainError("SCORE_SCOPE_INVALID", "专用评分任务缺少完整必要范围或正确用途", 409)
    packages = [entry["package"] for entry in snapshot["chapters"]]
    rules = [package["rule"]["id"] for package in packages]
    if len(set(rules)) != len(rules) or sorted(rules) != sorted(snapshot["necessary_rule_ids"]):
        raise DomainError("SCORE_SCOPE_INCOMPLETE", "评分任务必须且仅覆盖全部必要评分项", 409)
    port = ControlledScorePort(
        authority=authority, lease=lease, snapshot=snapshot, transport=transport
    )
    judgments = [judge_three(package, port) for package in packages]
    vector = features.build(
        packages,
        judgments,
        technical_maximum=snapshot["technical_maximum"],
        maximum_evidence_verified=snapshot["maximum_verified"],
    )
    result = {
        "schema": "complete-score-judgments/1",
        "execution_purpose": "score",
        "input_digest": snapshot["input_digest"],
        "method_digest": digest(),
        "items": judgments,
        "features": vector,
        "prediction": {
            "mode": "unavailable",
            "value": None,
            "intervals": None,
            "reasons": ["independent_real_model_certification_unavailable"],
        },
        "formal_eligibility": "unavailable",
    }
    authority.publish(lease, result)
    return result
