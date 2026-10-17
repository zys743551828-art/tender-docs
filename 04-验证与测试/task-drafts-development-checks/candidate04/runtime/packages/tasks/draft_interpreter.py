"""Bounded Chinese task-draft interpreter. It cannot save templates or execute work."""

import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.tasks.models import SourceSelection, StrictModel, Trigger

TaskKind = Literal["denial_check", "scoring_review", "supplement_impact", "chapter_suggestion"]


class TaskDraftRequest(StrictModel):
    request: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    section_id: UUID | None = None
    source_selection: SourceSelection = Field(default_factory=SourceSelection)


class TaskDraftUnresolvedField(StrictModel):
    field: str
    reason: str


class TaskDraftInterpretation(StrictModel):
    interpreter: Literal["bounded-zh-task-draft/1"] = "bounded-zh-task-draft/1"
    raw_request: str
    name: str
    task_kind: TaskKind | None
    instructions: str
    section_id: UUID | None
    source_selection: SourceSelection
    trigger: Trigger
    target_description: str
    output_description: str
    unresolved: list[TaskDraftUnresolvedField]
    ready_for_template_form: bool
    template_saved: Literal[False] = False
    run_created: Literal[False] = False
    formal_decisions_allowed: Literal[False] = False


KINDS: dict[TaskKind, tuple[str, str]] = {
    "denial_check": ("否决核对", r"废标|否决|强制要求|强制条款"),
    "scoring_review": ("评分复核", r"评分|估分|技术分"),
    "supplement_impact": ("已登记补遗关系核对", r"补遗|答疑变更|变更影响"),
    "chapter_suggestion": ("章节建议", r"章节|改写|扩写|缩写|编写建议"),
}


def interpret(body: TaskDraftRequest) -> TaskDraftInterpretation:
    text = body.request.strip()
    unresolved = []

    def unknown(field, reason):
        unresolved.append(TaskDraftUnresolvedField(field=field, reason=reason))

    matches = [kind for kind, (_, pattern) in KINDS.items() if re.search(pattern, text)]
    task_kind = matches[0] if len(matches) == 1 else None
    if task_kind is None:
        unknown("task_kind", "请选择一种任务；当前请求未识别或同时包含多种任务")
    if re.search(r"不要|无需|不做|不执行|别做|除了|而非|不是", text):
        task_kind = None
        unknown("task_kind", "请求包含否定或排除条件，请在表单明确任务类型")
    section = body.section_id if task_kind == "chapter_suggestion" else None
    if task_kind == "chapter_suggestion" and section is None:
        unknown("section_id", "请从当前项目章节列表选择目标；不从文字或UUID猜选")
    elif body.section_id is not None and task_kind != "chapter_suggestion":
        unknown("section_id", "所选章节与任务类型不一致，请明确目标")

    if re.search(r"全库|所有项目|其他项目|其它项目|外部链接|https?://", text):
        unknown("source_selection", "当前只支持本项目可信来源，请明确选择范围")
    elif (
        re.search(r"指定|这些|这几份|附件|某份", text)
        and body.source_selection.mode != "selected_versions"
    ):
        unknown("source_selection", "请求指向特定资料，请从可信版本列表明确选择")

    trigger = Trigger()
    temporal = re.search(
        r"每天|每日|每周|每月|每隔|每小时|每分钟|定时|定期|自动|更新后|上传后|资料更新", text
    )
    daily = re.search(
        r"(?:每天|每日)\s*(?:早上|上午)?\s*([0-9]{1,2})(?:[:：]([0-9]{2})|点(?:([0-9]{1,2})分)?)",
        text,
    )
    time_mentions = re.findall(r"[0-9]{1,2}(?:[:：][0-9]{2}|点)", text)
    ambiguous_time = re.search(
        r"下午|晚上|凌晨|中午|半|一刻|左右|前后|UTC|GMT|时区|每周|每月|每隔|每小时|每分钟|或者|或是",
        text,
        re.I,
    )
    event = re.search(r"(?:资料更新后|资料更新时|上传后|更新后)", text)
    if temporal:
        if daily and len(time_mentions) == 1 and not ambiguous_time and not event:
            hour, minute = int(daily[1]), int(daily[2] or daily[3] or "0")
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                trigger = Trigger(mode="schedule", cron=f"{minute} {hour} * * *")
            else:
                unknown("trigger", "定时时刻无效，请选择合法时刻和时区")
        elif (
            event
            and not daily
            and not ambiguous_time
            and not re.search(r"每天|每日|定时|定期", text)
        ):
            trigger = Trigger(mode="material_updated")
        else:
            unknown("trigger", "触发条件或时刻不明确，草案保留手动；请在表单确认")

    if re.search(
        r"自动确认|确认所有|自动提交|登记提交|核验成绩|发布模型|发布知识|替我确认|直接应用", text
    ):
        unknown("instructions", "任务只能产出建议或核对结果，不能代替正式确认、提交、核验或发布")
    name = KINDS[task_kind][0] if task_kind else "待明确任务"
    outputs = {
        "denial_check": "否决及强制项核对结果与证据，未运行或失败保留未完成",
        "scoring_review": "评分判断候选；未具备完整输入和模型资格时明确不可执行或不可可靠估分",
        "supplement_impact": "已登记补遗关系核对；当前语义影响尚未检查",
        "chapter_suggestion": "所选章节修改提案，须预览并显式采纳，不自动改稿",
    }
    return TaskDraftInterpretation(
        raw_request=body.request,
        name=name,
        task_kind=task_kind,
        instructions=text,
        section_id=section,
        source_selection=body.source_selection,
        trigger=trigger,
        target_description="所选章节"
        if task_kind == "chapter_suggestion"
        else "本项目当前正文与所选来源",
        output_description=outputs[task_kind] if task_kind else "请先选择任务类型和输出",
        unresolved=unresolved,
        ready_for_template_form=not unresolved,
    )
