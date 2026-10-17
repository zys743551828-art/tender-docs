"""Explicit synthetic engineering tests, no SQL or external provider execution."""

import copy
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from apps.api.historical_input_routes import router
from packages.domain.runtime import DomainError, sha
from packages.model_governance import historical_input as inputs
from packages.model_governance.historical_input_contracts import extend_openapi
from packages.model_governance.historical_requests import HistoricalInputRequest
from packages.research.profile import digest


def captured():
    body, parse, rule, evidence = [uuid4() for _ in range(4)]
    text = "合成2019年完整正文。第二页也必须完整提供。"
    return {
        "body_version_id": body,
        "body_parse_id": parse,
        "pages": [1, 2],
        "method_digest": digest(),
        "evidence": [
            {
                "id": evidence,
                "parse_id": parse,
                "physical_page": 1,
                "ordinal": 0,
                "text_content": text,
                "digest": sha(text.encode()),
            }
        ],
        "rules": [
            {
                "id": rule,
                "statement": "合成评分条款",
                "conditions": [],
                "exceptions": [],
                "max_points": Decimal("60"),
                "bands": [{"label": "完整", "description": "合成档位", "points": "60"}],
            }
        ],
    }


def test_build_includes_complete_pages_every_rule_without_labels_and_unchanged_identity():
    value = captured()
    value["rules"].append(value["rules"][0] | {"id": uuid4(), "statement": "另一必要项"})
    before = copy.deepcopy(value)
    result = inputs.build(value)
    assert len(result["packages"]) == 2 and value == before
    for item in result["packages"]:
        package = item["package"]
        assert package["sections"][0]["pages"] == [1, 2]
        assert package["sections"][0]["blocks"][0]["text"] == value["evidence"][0]["text_content"]
        assert package["method_digest"] == digest()
        assert set(package) == {
            "purpose",
            "rule",
            "body_version_id",
            "parse_id",
            "sections",
            "method_digest",
            "digest",
        }


def test_oversized_full_chapter_rejected_before_object_put(monkeypatch):
    value = captured()
    value["evidence"][0]["text_content"] = "字" * 250000
    value["evidence"][0]["digest"] = sha(value["evidence"][0]["text_content"].encode())
    writes = []
    monkeypatch.setattr(inputs, "stage_json", lambda *args: writes.append(args))
    with pytest.raises(DomainError, match="容量"):
        inputs.build(value)
    assert writes == []


def facts():
    return {
        "facts": [
            {"fact_kind": key, "claimed_at": "2019-01-01T00:00:00+00:00", "valid_until": None}
            for key in ["content_existed", "known_to_bidder", "effective"]
        ],
        "proof_refs": [
            {"fact_kind": key} for key in ["content_existed", "known_to_bidder", "effective"]
        ],
    }


@pytest.mark.parametrize(
    "kind", ["missing_known", "public_only", "future_known", "not_effective", "unproved_known"]
)
def test_public_or_future_or_unproved_fact_cannot_authorize_old_input(kind):
    value = facts()
    if kind in {"missing_known", "public_only"}:
        value["facts"][1]["claimed_at"] = None
        if kind == "public_only":
            value["facts"].append(
                {
                    "fact_kind": "published",
                    "claimed_at": "2018-01-01T00:00:00+00:00",
                    "valid_until": None,
                }
            )
    elif kind == "future_known":
        value["facts"][1]["claimed_at"] = "2026-01-01T00:00:00+00:00"
    elif kind == "not_effective":
        value["facts"][2]["valid_until"] = "2019-02-02T00:00:00+00:00"
    else:
        value["proof_refs"].pop(1)
    with pytest.raises(DomainError):
        inputs._facts(value, datetime(2019, 2, 2, tzinfo=timezone.utc), "tender")


def test_historical_fact_time_is_separate_from_current_observation():
    inputs._facts(facts(), datetime(2019, 2, 2, tzinfo=timezone.utc), "tender")
    # This does not modify/return any current source known/obtained/create time.


def test_actual_contract_only_four_input_paths_closed_request():
    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    assert (
        sum(method in {"get", "post"} for path in actual["paths"].values() for method in path) == 4
    )
    assert (
        actual["components"]["schemas"]["HistoricalInputRequest"]["additionalProperties"] is False
    )
    assert actual["components"]["schemas"]["HistoricalInputView"]["properties"][
        "formal_eligibility"
    ] == {"const": "unavailable"}
    for path in actual["paths"].values():
        for method, operation in path.items():
            if method in {"get", "post"}:
                assert all(
                    "$ref" in response["content"]["application/json"]["schema"]
                    for status, response in operation["responses"].items()
                    if status in {"200", "201"}
                )


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "real"),
        ("created_at", "2019-01-01"),
        ("review_id", str(uuid4())),
        ("formal_eligibility", "accepted"),
        ("historical_as_of", "2019-01-01"),
        ("official_score", 0),
    ],
)
def test_request_cannot_assert_qualification_old_review_or_backdated_freeze(field, value):
    body = {
        key: str(uuid4())
        for key in ["submission_id", "historical_subject_authority_id", "rule_set_id"]
    }
    body |= {
        "source_claim_ids": [str(uuid4())],
        "material_version_ids": [str(uuid4())],
        "reason": "合成回溯资料",
    }
    with pytest.raises(ValidationError):
        HistoricalInputRequest.model_validate(body | {field: value})


@pytest.mark.parametrize("pages", [[2, 3], [1, 3], [1], [1, 2, 3]])
def test_current_parse_requires_exact_physical_range_before_source_lookup(pages):
    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

        def fetchall(self):
            return self.value

    class Connection:
        def __init__(self):
            self.calls = 0

        def execute(self, query, values):
            self.calls += 1
            if self.calls == 1:
                return Result({"id": uuid4(), "coverage": "complete", "gaps": [], "page_count": 2})
            if self.calls == 2:
                return Result(
                    [{"physical_page": p, "quality": "usable", "gaps": []} for p in pages]
                )
            raise AssertionError("Invalid pages must fail before source lookup or PUT")

    with pytest.raises(DomainError, match="物理页"):
        inputs._parse(Connection(), uuid4(), uuid4())
