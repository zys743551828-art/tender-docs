"""Explicit in-memory authority + native MockTransport; software, never formal eligibility."""

import copy
import json
from uuid import uuid4

import httpx
import pytest

from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, sha
from packages.model_governance import scoring_producer as producer
from packages.model_governance.historical_input import build
from packages.research.profile import descriptor, digest
from tests.test_historical_input import captured


class MemoryAuthority:
    """Explicit synthetic stand-in. Production must use reviewed owned SQL RPCs."""

    def __init__(self, *, two_rules=False):
        value = captured()
        if two_rules:
            value["rules"].append(value["rules"][0] | {"id": uuid4()})
        chapters = build(value)["packages"]
        self.snapshot = {
            "execution_purpose": "score",
            "method_digest": digest(),
            "input_digest": sha("explicit-synthetic-input"),
            "chapters": [{"id": str(x["id"]), "package": x["package"]} for x in chapters],
            "necessary_rule_ids": [x["package"]["rule"]["id"] for x in chapters],
            "technical_maximum": None,
            "maximum_verified": False,
            "price_config": {
                "profile_digest": digest(),
                "currency": "USD",
                "context_tokens": 1000000,
                "max_output_tokens": 4096,
                "input_per_million": "0.30",
                "output_per_million": "1.20",
            },
        }
        self.calls, self.keys, self.publications = {}, {}, []
        self.current = True
        self.budget = 100

    def input(self, lease):
        return copy.deepcopy(self.snapshot)

    def reserve(self, lease, **body):
        if not self.current:
            raise DomainError("SOURCE_CHANGED", "synthetic source changed", 409)
        if (
            sum(float(c["reserved_fee"]) for c in self.calls.values()) + float(body["reserved_fee"])
            > self.budget
        ):
            raise DomainError("BUDGET_EXCEEDED", "synthetic budget exceeded", 409)
        key = body["chapter_id"], body["replicate"]
        if key in self.keys:
            identity = self.keys[key]
            if self.calls[identity]["state"] != "succeeded":
                raise DomainError("RECONCILIATION_REQUIRED", "synthetic unresolved call", 409)
            return {"new_reservation": False, "call_id": identity}
        identity = str(uuid4())
        self.keys[key] = identity
        self.calls[identity] = body | {"state": "reserved"}
        return {"new_reservation": True, "call_id": identity}

    def settle(self, identity, lease, **result):
        self.calls[identity].update(result)

    def success(self, lease, identity):
        return json.loads(self.calls[identity]["response"])

    def publish(self, lease, result):
        if not self.current:
            raise DomainError("SOURCE_CHANGED", "synthetic source changed", 409)
        self.publications.append(result)


def transport(*, on_call=None, fail=False, partial=False):
    requests = []

    def handler(request):
        requests.append(request)
        if on_call:
            on_call(len(requests))
        if fail:
            raise httpx.ReadTimeout("explicit synthetic timeout")
        supplied = json.loads(json.loads(request.content)["messages"][-1]["content"])
        block = supplied["sections"][0]["blocks"][0]
        output = {
            "band_id": supplied["rule"]["bands"][0]["id"],
            "reason": "明确合成判断",
            "status": "valid",
            "coverage": "partial" if partial else "complete",
            "evidence": [{"evidence_id": block["evidence_id"], "quote": block["text"]}],
        }
        return httpx.Response(
            200,
            json={
                "id": "explicit-synthetic-" + uuid4().hex,
                "model": descriptor()["model_alias"],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    return DeepSeekClient(
        api_key="explicit-synthetic", transport=httpx.MockTransport(handler)
    ), requests


def test_every_necessary_rule_gets_three_independent_clean_requests_and_preserved_usage():
    authority = MemoryAuthority(two_rules=True)
    model, requests = transport()
    result = producer.execute(authority, {}, transport=model)
    assert len(requests) == len(authority.calls) == 6 and len(authority.publications) == 1
    assert [x["valid_replicates"] for x in result["items"]] == [3, 3]
    for group in (requests[:3], requests[3:]):
        assert group[0].content == group[1].content == group[2].content
        body = json.loads(group[0].content)
        assert body["max_tokens"] == 4096
        assert [m["role"] for m in body["messages"]] == ["system", "system", "user"]
        assert "JSON Schema" in body["messages"][0]["content"]
    assert all(
        row["state"] == "succeeded" and json.loads(row["response"])["execution_purpose"] == "score"
        for row in authority.calls.values()
    )
    assert result["features"]["status"] == "unavailable" and result["features"]["values"] is None
    assert result["prediction"]["value"] is None and result["formal_eligibility"] == "unavailable"


def test_successful_receipts_restore_without_new_send():
    authority = MemoryAuthority()
    model, requests = transport()
    first = producer.execute(authority, {}, transport=model)
    second = producer.execute(authority, {}, transport=model)
    assert len(requests) == 3 and first == second


def test_unknown_billing_retains_reserve_and_never_resends_same_logical_call():
    authority = MemoryAuthority()
    model, requests = transport(fail=True)
    with pytest.raises(DomainError):
        producer.execute(authority, {}, transport=model)
    assert len(requests) == 1 and not authority.publications
    call = next(iter(authority.calls.values()))
    assert (
        call["state"] == "unknown"
        and call["estimated_fee"] is None
        and float(call["reserved_fee"]) > 0
    )
    with pytest.raises(DomainError):
        producer.execute(authority, {}, transport=model)
    assert len(requests) == 1


def test_source_change_during_http_settles_usage_but_blocks_later_call_or_success():
    authority = MemoryAuthority()

    def withdraw(number):
        authority.current = False

    model, requests = transport(on_call=withdraw)
    with pytest.raises(DomainError):
        producer.execute(authority, {}, transport=model)
    assert len(requests) == 1 and not authority.publications
    assert next(iter(authority.calls.values()))["state"] == "succeeded"


def test_partial_judgment_not_promoted_to_three_or_zero():
    authority = MemoryAuthority()
    model, requests = transport(partial=True)
    with pytest.raises(DomainError):
        producer.execute(authority, {}, transport=model)
    assert len(requests) == 1 and not authority.publications
    assert (
        next(iter(authority.calls.values()))["state"] == "succeeded"
    )  # billed execution != valid judgment


@pytest.mark.parametrize(
    "change", ["low_output", "low_context", "missing_rule", "duplicate_rule", "no_budget"]
)
def test_profile_scope_budget_fail_before_provider_send(change):
    authority = MemoryAuthority()
    if change == "low_output":
        authority.snapshot["price_config"]["max_output_tokens"] = 1
    elif change == "low_context":
        authority.snapshot["price_config"]["context_tokens"] = 2
    elif change == "missing_rule":
        authority.snapshot["necessary_rule_ids"].append(str(uuid4()))
    elif change == "duplicate_rule":
        authority.snapshot["chapters"].append(copy.deepcopy(authority.snapshot["chapters"][0]))
    else:
        authority.budget = 0
    model, requests = transport()
    with pytest.raises(DomainError):
        producer.execute(authority, {}, transport=model)
    assert len(requests) == 0 and not authority.publications
