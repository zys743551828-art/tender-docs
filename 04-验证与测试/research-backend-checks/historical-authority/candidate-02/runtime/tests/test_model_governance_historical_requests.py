"""Draft protocol syntax only. Does not prove historical facts or application reachability."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.model_governance.historical_requests import (
    HistoricalFact,
    HistoricalInputRequest,
    HistoricalSubjectRequest,
    HistorySourceClaimRequest,
)


def claim():
    return {
        "submission_id": str(uuid4()),
        "document_version_id": str(uuid4()),
        "facts": [
            {
                "fact_kind": "published",
                "claimed_at": None,
                "proof_refs": [],
                "explanation": "公开日期尚待核实",
            }
        ],
        "declared_data_origin": "uncertain",
        "reason": "保存未知历史事实，不确认知晓",
    }


def test_unknown_keeps_null_and_publication_does_not_create_company_known():
    value = HistorySourceClaimRequest.model_validate(claim())
    assert value.facts[0].claimed_at is None
    assert [fact.fact_kind for fact in value.facts] == ["published"]
    assert "confirmed" not in value.model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"claimed_at": "2019-01-01T00:00:00"},
        {"valid_until": "2019-01-01T00:00:00Z"},
        {"claimed_at": "2019-01-01T00:00:00Z", "valid_until": "2020-01-01T00:00:00Z"},
    ],
)
def test_unknown_naive_or_wrong_kind_effective_interval_rejected(changes):
    value = claim()["facts"][0] | changes
    with pytest.raises(ValidationError):
        HistoricalFact.model_validate(value)


def test_effective_typed_interval_and_duplicate_facts_explicitly_rejected():
    value = {
        "fact_kind": "effective",
        "claimed_at": "2019-01-01T00:00:00Z",
        "valid_until": "2020-01-01T00:00:00Z",
        "proof_refs": [],
        "explanation": "仅草稿，未有证据不能确认",
    }
    assert HistoricalFact.model_validate(value).claimed_at.year == 2019
    with pytest.raises(ValidationError):
        HistoricalFact.model_validate(value | {"valid_until": "2018-01-01T00:00:00Z"})
    repeated = claim()
    repeated["facts"] *= 2
    with pytest.raises(ValidationError):
        HistorySourceClaimRequest.model_validate(repeated)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_known_at", "2019-01-01T00:00:00Z"),
        ("created_at", "2019-01-01T00:00:00Z"),
        ("confirmed", True),
        ("data_origin", "real"),
        ("review_id", str(uuid4())),
        ("model_eligibility", "accepted"),
    ],
)
def test_claim_draft_cannot_backdate_system_or_self_grant_authority(field, value):
    with pytest.raises(ValidationError):
        HistorySourceClaimRequest.model_validate(claim() | {field: value})


def test_input_draft_does_not_accept_fake_old_review_or_model_content():
    value = {
        "submission_id": str(uuid4()),
        "historical_subject_authority_id": str(uuid4()),
        "source_claim_ids": [str(uuid4())],
        "material_version_ids": [str(uuid4())],
        "rule_set_id": str(uuid4()),
        "reason": "冻结现在创建的历史回测",
    }
    assert HistoricalInputRequest.model_validate(value).submission_id
    for extra in (
        {"review_id": str(uuid4())},
        {"historical_as_of": "2019-01-01T00:00:00Z"},
        {"body_version_id": str(uuid4())},
        {"labels": [99]},
        {"content": "user arbitrary model input"},
    ):
        with pytest.raises(ValidationError):
            HistoricalInputRequest.model_validate(value | extra)


def test_pending_subject_can_be_saved_without_invented_identity():
    value = HistoricalSubjectRequest.model_validate(
        {
            "submission_id": str(uuid4()),
            "proof_refs": [],
            "declared_data_origin": "uncertain",
            "reason": "历史主体尚不明确，保存待核验",
        }
    )
    assert value.proposed_identity is None


@pytest.mark.parametrize("time", [0, False, "0", "2019", 1546300800])
def test_unknown_time_is_not_silently_coerced_from_zero_or_unix_epoch(time):
    value = claim()["facts"][0] | {"claimed_at": time}
    with pytest.raises(ValidationError):
        HistoricalFact.model_validate(value)


def test_pending_attachment_does_not_invent_unknown_historical_time():
    value = claim()
    value["facts"][0]["proof_refs"] = [
        {
            "document_version_id": str(uuid4()),
            "blob_digest": "a" * 64,
            "parse_id": str(uuid4()),
            "evidence_id": str(uuid4()),
            "physical_page": 1,
            "evidence_digest": "b" * 64,
            "quote": "仅已找到附件，具体时间未知",
            "explanation": "可保存证据引用，未知日期仍null",
        }
    ]
    assert HistorySourceClaimRequest.model_validate(value).facts[0].claimed_at is None


def test_obvious_internal_time_contradiction_rejected_without_assuming_known_received_order():
    value = claim()
    value["facts"] = [
        {
            "fact_kind": "content_existed",
            "claimed_at": "2020-01-01T00:00:00Z",
            "proof_refs": [],
            "explanation": "明确内容存在时点",
        },
        {
            "fact_kind": "known_to_bidder",
            "claimed_at": "2019-01-01T00:00:00Z",
            "proof_refs": [],
            "explanation": "时间自相矛盾",
        },
    ]
    with pytest.raises(ValidationError):
        HistorySourceClaimRequest.model_validate(value)
