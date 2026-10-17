"""Strict template configuration; instructions remain data and do not grant tools."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.tasks.scheduling import Schedule


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Trigger(StrictModel):
    mode: Literal["manual", "material_updated", "schedule"] = "manual"
    cron: str | None = None
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.mode == "schedule":
            parsed = Schedule.parse(self.cron, self.timezone)
            self.cron = parsed.cron
        elif self.cron is not None:
            raise ValueError("只有显式定时触发可设置cron")
        else:
            # Validate the timezone even before a template gains a schedule.
            Schedule.parse("0 0 * * *", self.timezone)
        return self


class SourceSelection(StrictModel):
    mode: Literal["current_project", "selected_versions"] = "current_project"
    version_ids: list[UUID] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def exact_selection(self):
        if len(set(self.version_ids)) != len(self.version_ids):
            raise ValueError("来源版本不可重复")
        if (self.mode == "selected_versions") != bool(self.version_ids):
            raise ValueError("指定来源须提供实际版本；当前项目不接受额外版本列表")
        return self


class TemplateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    task_kind: Literal["denial_check", "scoring_review", "supplement_impact", "chapter_suggestion"]
    instructions: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    section_id: UUID | None = None
    source_selection: SourceSelection = Field(default_factory=SourceSelection)
    trigger: Trigger = Field(default_factory=Trigger)

    @model_validator(mode="after")
    def exact_target(self):
        if (self.task_kind == "chapter_suggestion") != (self.section_id is not None):
            raise ValueError("章节建议须指定真实章节；其他任务以项目当前正文为目标")
        return self


class PauseRequest(StrictModel):
    paused: bool = Field(strict=True)


class TemplateView(StrictModel):
    id: UUID
    bid_id: UUID
    revision: int = Field(ge=1)
    version_id: UUID
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    purpose: Literal["assist", "compliance"]
    paused: bool
    configuration: TemplateRequest
    next_run_at: datetime | None
    run_created: Literal[False]
    active_runs_cancelled: Literal[False]


class TemplatePage(StrictModel):
    items: list[TemplateView]
    next_cursor: str | None
    has_more: bool


class TemplateSnapshotConfig(StrictModel):
    schema_version: Literal["task-template/1"] = Field(alias="schema")
    definition: TemplateRequest
    paused: bool
    formal_decisions_allowed: Literal[False]


class TemplateVersionView(StrictModel):
    id: UUID
    bid_id: UUID
    definition_id: UUID
    revision: int = Field(ge=1)
    instructions: str
    config: TemplateSnapshotConfig
    digest: str = Field(pattern="^[0-9a-f]{64}$")
