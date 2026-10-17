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
