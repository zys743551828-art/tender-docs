"""报价实际输入；不接用户表达式、任意脚本或客户端确认身份。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

DECIMAL_PATTERN = r"^(0|[1-9][0-9]{0,23})(\.[0-9]{1,12})?$"
DecimalString = Annotated[str, Field(pattern=DECIMAL_PATTERN)]

SUPPORTED_FIELDS = {
    "method",
    "maximum",
    "minimum_price",
    "maximum_price",
    "minimum_valid_bids",
    "above_coefficient",
    "below_coefficient",
    "amount_unit",
    "currency",
    "rounding_policy",
    "baseline_set",
    "exclusions",
    "intermediate_rounding",
    "clamping",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormulaParameters(Strict):
    method: Literal["lowest_valid_ratio", "mean_valid_deviation"]
    maximum: str = Field(pattern=DECIMAL_PATTERN)
    minimum_price: str | None = Field(pattern=DECIMAL_PATTERN)
    maximum_price: str | None = Field(pattern=DECIMAL_PATTERN)
    minimum_valid_bids: StrictInt = Field(ge=1, le=201)
    above_coefficient: str = Field(pattern=DECIMAL_PATTERN)
    below_coefficient: str = Field(pattern=DECIMAL_PATTERN)


class EvidenceQuote(Strict):
    source_id: UUID
    source_revision: StrictInt = Field(ge=1)
    document_version_id: UUID
    parse_id: UUID
    physical_page: StrictInt = Field(ge=1)
    evidence_id: UUID
    quote: str = Field(min_length=1, max_length=20000)
    supported_fields: list[str] = Field(min_length=1, max_length=14)


class Interpretation(Strict):
    baseline_set: Literal["own_and_all_numerically_eligible_assumptions"]
    exclusions: Literal["none"]
    random_parameters: Literal["none"]
    intermediate_rounding: Literal["none"]
    clamping: Literal["zero_to_maximum"]


class RegisterFormula(Strict):
    title: str = Field(min_length=1, max_length=200)
    formula: FormulaParameters
    amount_unit: Literal["元", "万元"]
    currency: Literal["CNY"] | None
    rounding_policy: Literal["half_up_2dp", "half_even_2dp"]
    interpretation: Interpretation
    terms_complete_confirmed: Literal[True]
    matches_source_confirmed: Literal[True]
    evidence_quotes: list[EvidenceQuote] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("terms_complete_confirmed", "matches_source_confirmed", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("请明确核对并确认完整条款与受限计算参数一致")
        return value


class CurveRange(Strict):
    lower: str = Field(pattern=DECIMAL_PATTERN)
    upper: str = Field(pattern=DECIMAL_PATTERN)
    points: StrictInt = Field(ge=2, le=101)


class PricingRequest(Strict):
    formula_version_id: UUID
    candidate_price: str = Field(pattern=DECIMAL_PATTERN)
    competitor_price_assumptions: list[DecimalString] = Field(max_length=200)
    rounding_policy: Literal["half_up_2dp", "half_even_2dp"]
    candidate_prices: list[DecimalString] = Field(max_length=50)
    curve_range: CurveRange | None
    amount_unit: Literal["元", "万元"]
    currency: Literal["CNY"] | None


class SavePlan(Strict):
    simulation_id: UUID
    reason: str = Field(min_length=1, max_length=4000)


class FormulaSummary(Strict):
    formula_version_id: UUID
    formula_digest: str
    title: str
    method: str
    maximum: str
    amount_unit: str
    currency: str | None
    rounding_policy: str
    confirmed_at: datetime
