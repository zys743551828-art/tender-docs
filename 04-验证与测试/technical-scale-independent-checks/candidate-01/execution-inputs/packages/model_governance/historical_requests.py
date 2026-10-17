"""Closed actual proof/subject requests; historical input request remains a draft."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FactKind = Literal[
    "content_existed", "published", "effective", "known_to_bidder", "received_by_bidder"
]
SHA = r"^[0-9a-f]{64}$"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("reason", "explanation", check_fields=False)
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("具体依据或说明不能为空")
        return value


class ProofReference(Closed):
    document_version_id: UUID
    blob_digest: str = Field(pattern=SHA)
    parse_id: UUID
    evidence_id: UUID
    physical_page: int = Field(strict=True, ge=1)
    evidence_digest: str = Field(pattern=SHA)
    quote: str = Field(min_length=1, max_length=16000)
    explanation: str = Field(min_length=1, max_length=4000)


class HistoricalFact(Closed):
    fact_kind: FactKind
    claimed_at: datetime | None
    valid_until: datetime | None = None
    proof_refs: list[ProofReference] = Field(max_length=50)
    explanation: str = Field(min_length=1, max_length=4000)

    @field_validator("claimed_at", "valid_until", mode="before")
    @classmethod
    def explicit_time_or_unknown(cls, value):
        if value is None or isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise ValueError("未知时间只能null，不能用0或Unix数值时间代替")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("请使用明确带时区的ISO时间，未知为null") from None

    @model_validator(mode="after")
    def typed_interval(self):
        for value in (self.claimed_at, self.valid_until):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("历史时间必须明确时区，未知为null")
        if self.valid_until is not None and (
            self.fact_kind != "effective"
            or self.claimed_at is None
            or self.valid_until <= self.claimed_at
        ):
            raise ValueError("只有已知生效事实可填写严格晚于起点的截止时刻")
        return self


class HistorySourceClaimRequest(Closed):
    submission_id: UUID
    document_version_id: UUID
    historical_subject_authority_id: UUID | None = None
    facts: list[HistoricalFact] = Field(min_length=1, max_length=5)
    declared_data_origin: Literal["real", "synthetic", "uncertain"]
    supersedes_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def unique_facts(self):
        if len({fact.fact_kind for fact in self.facts}) != len(self.facts):
            raise ValueError("每类历史事实最多一条；冲突需明确追加更正")
        by_kind = {fact.fact_kind: fact.claimed_at for fact in self.facts}
        existed = by_kind.get("content_existed")
        if existed is not None and any(
            value is not None and value < existed for value in by_kind.values()
        ):
            raise ValueError("公布、生效、知晓或取得不能早于同一精确内容的已填存在时点")
        return self


class HistoricalIdentityProjection(Closed):
    bidder_id: UUID
    bidder_name: str = Field(min_length=1, max_length=500)
    round_id: UUID
    engineering_project_id: UUID
    engineering_title: str = Field(min_length=1, max_length=1000)
    region: str | None
    engineering_type: str | None
    lot: str | None
    round_no: int = Field(strict=True, ge=1)


class HistoricalSubjectRequest(Closed):
    submission_id: UUID
    proposed_identity: HistoricalIdentityProjection | None = None
    proof_refs: list[ProofReference] = Field(max_length=50)
    declared_data_origin: Literal["real", "synthetic", "uncertain"]
    supersedes_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=4000)


class HistoricalConfirmationRequest(Closed):
    reason: str = Field(min_length=1, max_length=4000)
    # No confirmed/verified boolean. The command is a human decision, and the server must
    # independently check all exact current proof bindings before appending a decision.


class HistoricalInputRequest(Closed):
    submission_id: UUID
    historical_subject_authority_id: UUID
    source_claim_ids: list[UUID] = Field(min_length=1, max_length=1000)
    material_version_ids: list[UUID] = Field(min_length=1, max_length=1000)
    rule_set_id: UUID
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def unique_versions(self):
        for values in (self.source_claim_ids, self.material_version_ids):
            if len(values) != len(set(values)):
                raise ValueError("同一来源不能重复计入冻结范围")
        return self
