"""Software-only closed schema/evidence; no real authority/SQL qualification claim."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.domain.runtime import DomainError
from packages.model_governance.scale_evidence import validate_maximum_declaration
from packages.model_governance.scale_requests import TechnicalScaleRequest


def value():
    return {
        "intent": "confirm",
        "context": {"purpose": "current_prebid"},
        "technical_maximum": "60.00",
        "scale_description": "未加权原始分",
        "aggregation": {"status": "unknown", "description": "暂无原文支持可执行聚合"},
        "evidence_refs": [
            {
                "document_version_id": str(uuid4()),
                "blob_digest": "a" * 64,
                "parse_id": str(uuid4()),
                "physical_page": 1,
                "evidence_id": str(uuid4()),
                "quote": "技术总分满分为60分，量尺为未加权原始分。",
                "supported_fields": ["technical_maximum", "scale_description"],
            }
        ],
        "reason": "显式合成原文一次确认",
    }


def test_total_can_be_confirmable_while_aggregation_unknown_without_model_eligibility():
    body = TechnicalScaleRequest.model_validate(value())
    assert body.technical_maximum == "60.00" and body.aggregation.status == "unknown"
    assert "formal_eligibility" not in TechnicalScaleRequest.model_fields


@pytest.mark.parametrize(
    "maximum", [None, 60, True, "0", "-1", "NaN", "Infinity", "1e2", "1000000001", "0.000000001"]
)
def test_confirm_requires_bounded_positive_decimal_string(maximum):
    with pytest.raises(ValidationError):
        TechnicalScaleRequest.model_validate(value() | {"technical_maximum": maximum})


def test_pending_can_preserve_missing_denominator_without_inventing_zero():
    body = TechnicalScaleRequest.model_validate(
        value()
        | {
            "intent": "save_pending",
            "technical_maximum": None,
            "scale_description": "",
            "evidence_refs": [],
        }
    )
    assert body.technical_maximum is None


@pytest.mark.parametrize(
    "extra",
    [
        {"prediction_timing": "prebid"},
        {"confirmed_by": str(uuid4())},
        {"maximum_usable": True},
        {"technical_maximum_source": "official_label"},
    ],
)
def test_client_cannot_select_server_authority(extra):
    with pytest.raises(ValidationError):
        TechnicalScaleRequest.model_validate(value() | extra)


def test_historical_scale_does_not_require_a_fake_old_review_or_existing_input():
    request = value() | {
        "context": {
            "purpose": "historical_backtest",
            "submission_id": str(uuid4()),
            "historical_subject_authority_id": str(uuid4()),
            "material_snapshot_id": str(uuid4()),
            "claim_ids": [str(uuid4())],
        }
    }
    body = TechnicalScaleRequest.model_validate(request)
    assert body.context.submission_id is not None
    with pytest.raises(ValidationError):
        TechnicalScaleRequest.model_validate(
            value() | {"context": request["context"] | {"review_id": str(uuid4())}}
        )


@pytest.mark.parametrize(
    "text",
    [
        "综合总分满分60分，量尺为未加权原始分。",
        "技术权重60%，量尺为未加权原始分。",
        "技术条款分值之和60分，量尺为未加权原始分。",
        "官方技术成绩60分，量尺为未加权原始分。",
        "非技术总分满分60分，量尺为未加权原始分。",
        "原技术总分满分60分，现改80分，量尺为未加权原始分。",
        "不采用（技术总分满分60分，量尺为未加权原始分）。",
        "技术总分满分60分，量尺为未加权原始分。技术满分另为80分。",
    ],
)
def test_complete_statement_positive_grammar_rejects_unrelated_or_ambiguous_context(text):
    with pytest.raises(DomainError):
        validate_maximum_declaration(
            source_text=text, quote=text, maximum="60", scale_description="未加权原始分"
        )


def test_selected_numeric_quote_cannot_launder_a_full_block():
    with pytest.raises(DomainError):
        validate_maximum_declaration(
            source_text="综合总分满分60分，量尺为未加权原始分。",
            quote="60",
            maximum="60",
            scale_description="未加权原始分",
        )


def test_supported_complete_declaration_retains_decimal_and_scale():
    text = "技术总分满分为60分，量尺为未加权原始分。"
    assert (
        str(
            validate_maximum_declaration(
                source_text=text, quote=text, maximum="60.00", scale_description="未加权原始分"
            )
        )
        == "60"
    )


@pytest.mark.parametrize("maximum,scale", [("80", "未加权原始分"), ("60", "加权分")])
def test_human_confirmation_cannot_change_explicit_number_or_scale(maximum, scale):
    text = "技术总分满分为60分，量尺为未加权原始分。"
    with pytest.raises(DomainError):
        validate_maximum_declaration(
            source_text=text, quote=text, maximum=maximum, scale_description=scale
        )


def test_seven_actual_operations_have_closed_responses_without_score_execution():
    from fastapi import FastAPI

    from apps.api.technical_scale_routes import router
    from packages.model_governance.scale_contracts import extend_openapi

    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    operations = [
        (path, method)
        for path, item in actual["paths"].items()
        for method in item
        if method in {"get", "post"}
    ]
    assert len(operations) == 7
    for path, method in operations:
        for status, response in actual["paths"][path][method]["responses"].items():
            if status in {"200", "201"}:
                name = response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                assert actual["components"]["schemas"][name]["additionalProperties"] is False
    assert actual["components"]["schemas"]["TechnicalScaleCapabilities"]["properties"][
        "score_input_integration"
    ] == {"const": False}


def test_scale_description_cannot_absorb_contradictory_trailing_technical_total():
    text = "技术总分满分60分，量尺为未加权原始分，但技术总分现为80分。"
    with pytest.raises(DomainError):
        validate_maximum_declaration(
            source_text=text,
            quote=text,
            maximum="60",
            scale_description="未加权原始分，但技术总分现为80分",
        )


def ancestor_claim_case():
    bid, submission, subject, pdf, word = [str(uuid4()) for _ in range(5)]
    originals = [
        {"id": str(uuid4()), "document_version_id": doc, "digest": digest}
        for doc, digest in [(pdf, "a" * 64), (word, "b" * 64)]
    ]
    # The only parsed/quoted evidence is in the controlled PDF. Word has provenance
    # and a historical claim, never a fabricated text/evidence/chunk object.
    refs = [
        {
            "document_version_id": pdf,
            "heads": [
                {"document_version_id": pdf, "digest": "a" * 64},
                {"document_version_id": word, "digest": "b" * 64},
            ],
        }
    ]
    claims = [
        {
            "id": str(uuid4()),
            "bid_id": bid,
            "submission_id": submission,
            "subject_id": subject,
            "document_version_id": item["document_version_id"],
            "document_digest": item["digest"],
            "manual_decision": "confirmed",
            "is_current_head": True,
            "submission_current": True,
            "related_subject_current": True,
            "proofs_current": True,
            "facts": [
                {"fact_kind": kind, "claimed_at": "2018-01-01T00:00:00+00:00", "valid_until": None}
                for kind in ["content_existed", "known_to_bidder", "effective"]
            ],
            "proof_refs": [
                {"fact_kind": kind} for kind in ["content_existed", "known_to_bidder", "effective"]
            ],
        }
        for item in originals
    ]
    return {
        "refs": refs,
        "claims": claims,
        "originals": originals,
        "bid": bid,
        "submission": submission,
        "subject": subject,
        "submitted_at": "2019-01-01T00:00:00+00:00",
    }


def test_historical_scale_claims_cover_controlled_pdf_and_unparsed_word_parent():
    from packages.model_governance.scale_repository import require_historical_claim_closure

    case = ancestor_claim_case()
    require_historical_claim_closure(**case)
    assert len(case["refs"]) == 1 and len(case["claims"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        "missing_parent",
        "extra_unrelated",
        "parent_withdrawn",
        "parent_future_known",
        "parent_bad_digest",
        "parent_wrong_subject",
        "parent_wrong_submission",
        "parent_wrong_bid",
        "parent_not_head",
        "parent_missing_effective",
        "known_before_existence",
        "ancestor_digest_wrong",
        "missing_original_bytes",
    ],
)
def test_historical_parent_claim_and_ancestor_qualification_never_skip_non_quote_documents(change):
    from copy import deepcopy

    from packages.model_governance.scale_repository import require_historical_claim_closure

    case = ancestor_claim_case()
    parent = case["claims"][1]
    if change == "missing_parent":
        case["claims"].pop()
    elif change == "extra_unrelated":
        extra = deepcopy(parent)
        extra["document_version_id"] = str(uuid4())
        case["claims"].append(extra)
    elif change == "parent_withdrawn":
        parent["proofs_current"] = False
    elif change == "parent_future_known":
        parent["facts"][1]["claimed_at"] = "2020-01-01T00:00:00+00:00"
    elif change == "parent_bad_digest":
        parent["document_digest"] = "c" * 64
    elif change.startswith("parent_wrong_"):
        parent[change.removeprefix("parent_wrong_") + "_id"] = str(uuid4())
    elif change == "parent_not_head":
        parent["is_current_head"] = False
    elif change == "parent_missing_effective":
        parent["facts"].pop()
    elif change == "known_before_existence":
        parent["facts"][1]["claimed_at"] = "2017-01-01T00:00:00+00:00"
    elif change == "ancestor_digest_wrong":
        case["refs"][0]["heads"][1]["digest"] = "c" * 64
    elif change == "missing_original_bytes":
        case["originals"].pop()
    with pytest.raises(DomainError):
        require_historical_claim_closure(**case)
