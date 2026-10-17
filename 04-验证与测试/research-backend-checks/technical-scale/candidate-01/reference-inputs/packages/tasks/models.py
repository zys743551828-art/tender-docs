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


class RunRequest(StrictModel):
    rerun: bool = Field(default=False, strict=True)


class RunView(StrictModel):
    id: UUID
    bid_id: UUID
    definition_version_id: UUID
    root_operation_id: UUID
    final_operation_id: UUID | None
    state: Literal[
        "queued",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
        "awaiting_review",
        "blocked",
        "superseded",
        "unknown",
        "reconciliation_required",
    ]
    stage: Literal["preparation", "review", "execution"]
    frozen_manifest: dict
    result: dict | None
    error_code: str | None
    formal_decisions_created: Literal[False]
    coverage: Literal["registered_effectivity_only_semantic_unknown", "not_formal_score"]


class RunPage(StrictModel):
    items: list[RunView]
    next_after: UUID | None


class TaskKindView(StrictModel):
    task_kind: Literal["denial_check", "scoring_review", "supplement_impact", "chapter_suggestion"]
    name: str
    purpose: Literal["compliance", "assist"]
    execution: Literal[
        "unavailable",
        "partial",
        "existing_review_control_plane",
        "existing_authoring_proposal_queue",
    ]
    reason: str | None = None
    formal_scoring_eligible: Literal[False] | None = None
    coverage: Literal["registered_effectivity_only"] | None = None
    semantic_impact_coverage: Literal["unknown"] | None = None
    formal_decisions_allowed: Literal[False] | None = None
    automatic_apply: Literal[False] | None = None


class TaskKindPage(StrictModel):
    items: list[TaskKindView]
    default_trigger: Literal["manual"]
    saving_preset_creates_run: Literal[False]


class TaskNotificationPayload(StrictModel):
    key: str
    run_id: UUID
    state: str
    error_code: str | None
    run_ref: str


class TaskNotification(StrictModel):
    id: UUID
    payload: TaskNotificationPayload
    created_at: datetime


class TaskNotificationPage(StrictModel):
    items: list[TaskNotification]
    next_after: UUID | None


class MissedTaskSchedule(StrictModel):
    from_at: datetime = Field(alias="from")
    through: datetime
    count: None
    count_status: Literal["not_enumerated"]
    resolution: Literal["skipped_run_current_once_available"]


class TaskScheduleRecoveryPayload(StrictModel):
    schedule_id: UUID
    definition_id: UUID
    run_current_once_ref: str
    definition_version_id: UUID
    next_fire_at: datetime
    missed: MissedTaskSchedule | None
    automatic_catchup_runs: Literal[0]


class TaskScheduleRecovery(StrictModel):
    id: UUID
    payload: TaskScheduleRecoveryPayload
    created_at: datetime


class TaskScheduleRecoveryPage(StrictModel):
    items: list[TaskScheduleRecovery]
    next_after: UUID | None
    run_current_once_supported: Literal[True]
