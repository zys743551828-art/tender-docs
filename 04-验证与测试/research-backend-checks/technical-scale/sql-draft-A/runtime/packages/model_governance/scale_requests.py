"""Closed first increment for sourced technical-total denominators.

This schema is software only until its separate authority SQL/API is reviewed. It
never promotes an official outcome, a component sum or a weight to a denominator.
Official aggregation execution is intentionally a separate method increment.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAXIMUM = Decimal("1000000000")
DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,8})?$")


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScaleContext(Closed):
    purpose: Literal["current_prebid", "historical_backtest"]
    submission_id: UUID | None = None
    historical_subject_authority_id: UUID | None = None
    claim_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.purpose == "current_prebid":
            if (
                self.submission_id is not None
                or self.historical_subject_authority_id is not None
                or self.claim_ids
            ):
                raise ValueError("当前满分不能借历史证明改变事前时点或身份")
        elif self.submission_id is None or self.historical_subject_authority_id is None:
            raise ValueError("历史满分须明确实际实交和已确认历史主体，不需要先创建历史评分输入")
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("历史来源声明不可重复")
        return self


class ScaleAggregation(Closed):
    status: Literal["unknown", "unsupported"]
    description: str = Field(min_length=1, max_length=4000)

    @field_validator("description")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请说明尚未明确或暂不支持的官方聚合口径")
        return value


class ScaleEvidence(Closed):
    document_version_id: UUID
    blob_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    parse_id: UUID
    physical_page: int = Field(strict=True, ge=1)
    evidence_id: UUID
    quote: str = Field(min_length=1, max_length=16000)
    supported_fields: list[Literal["technical_maximum", "scale_description", "aggregation"]] = (
        Field(min_length=1, max_length=3)
    )

    @field_validator("supported_fields")
    @classmethod
    def unique_fields(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("每条原文的支持字段不可重复")
        return value


class TechnicalScaleRequest(Closed):
    intent: Literal["save_pending", "confirm"]
    context: ScaleContext
    technical_maximum: str | None
    scale_description: str = Field(max_length=4000)
    aggregation: ScaleAggregation
    evidence_refs: list[ScaleEvidence] = Field(max_length=50)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("technical_maximum", mode="before")
    @classmethod
    def finite_explicit_decimal(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or DECIMAL.fullmatch(value) is None:
            raise ValueError("满分必须是有限的正Decimal字符串，不能由权重、条款和或标签补数")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ValueError("满分无效") from None
        if not amount.is_finite() or amount <= 0 or amount > MAXIMUM:
            raise ValueError("满分须大于0且不超过1000000000")
        return value

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请填写本次确认或保存依据")
        return value

    @model_validator(mode="after")
    def confirmation_needs_explicit_evidence(self):
        keys = [(item.evidence_id, item.quote) for item in self.evidence_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("同一精确原文请合并支持字段")
        if self.intent == "confirm":
            supported = {field for item in self.evidence_refs for field in item.supported_fields}
            if (
                self.technical_maximum is None
                or not self.scale_description.strip()
                or not {"technical_maximum", "scale_description"} <= supported
            ):
                raise ValueError("确认满分须有明确量尺和各字段的招标／补遗精确原文")
        return self


class TechnicalScaleCorrection(TechnicalScaleRequest):
    base_version_id: UUID


class TechnicalScaleWithdrawal(Closed):
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("请说明撤回依据")
        return value
