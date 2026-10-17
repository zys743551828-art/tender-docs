"""Task draft ambiguity must remain visible and never grants execution authority."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.tasks.draft_interpreter import TaskDraftRequest, interpret
from packages.tasks.models import TemplateRequest


def test_simple_draft_can_prefill_existing_explicit_template_form():
    result = interpret(TaskDraftRequest(request="检查当前项目否决条款"))
    assert result.task_kind == "denial_check"
    assert result.trigger.mode == "manual" and result.unresolved == []
    form = TemplateRequest(
        name=result.name, task_kind=result.task_kind, instructions=result.instructions
    )
    assert form.trigger.mode == "manual"
    assert result.template_saved is False and result.run_created is False
    assert result.formal_decisions_allowed is False


@pytest.mark.parametrize(
    "text,cron",
    [
        ("每天9:30核对否决项", "30 9 * * *"),
        ("每日上午9点核对强制条款", "0 9 * * *"),
        ("每天23点59分核对废标项", "59 23 * * *"),
    ],
)
def test_explicit_supported_daily_time_is_only_a_proposed_configuration(text, cron):
    draft = interpret(TaskDraftRequest(request=text))
    assert draft.trigger.mode == "schedule" and draft.trigger.cron == cron
    assert draft.trigger.timezone == "Asia/Shanghai"
    assert draft.run_created is False and draft.template_saved is False


@pytest.mark.parametrize(
    "text",
    [
        "每天核对否决项",
        "每天9点半核对否决项",
        "每天9点左右核对否决项",
        "每周9点核对否决项",
        "每天25:00核对否决项",
        "每天9:70核对否决项",
        "每天9:00和10:00核对否决项",
        "每隔5分钟核对否决项",
        "自动核对否决项",
        "每天UTC9:00核对否决项",
        "每天9:00或者更新后核对否决项",
    ],
)
def test_ambiguous_or_unsupported_frequency_is_not_silently_scheduled(text):
    result = interpret(TaskDraftRequest(request=text))
    assert result.trigger.mode == "manual"
    assert any(item.field == "trigger" for item in result.unresolved)
    assert not result.ready_for_template_form


def test_material_event_is_proposed_without_consuming_any_prior_uploads():
    draft = interpret(TaskDraftRequest(request="资料更新后核对否决条款"))
    assert draft.trigger.mode == "material_updated"
    assert draft.template_saved is False


@pytest.mark.parametrize(
    "text", ["帮我看看", "检查否决并估分", "不要做否决检查，只给技术分", "除了否决项再看看其他"]
)
def test_unrecognized_compound_or_negated_task_requires_explicit_kind(text):
    result = interpret(TaskDraftRequest(request=text))
    assert result.task_kind is None
    assert any(item.field == "task_kind" for item in result.unresolved)


def test_chapter_id_is_supplied_from_trusted_selection_not_interpreted_from_text():
    identity = uuid4()
    no_selection = interpret(TaskDraftRequest(request=f"改写章节 {identity}"))
    assert no_selection.section_id is None and not no_selection.ready_for_template_form
    selected = interpret(TaskDraftRequest(request="给当前章节编写建议", section_id=identity))
    assert selected.section_id == identity and selected.ready_for_template_form


@pytest.mark.parametrize(
    "text",
    [
        "核对所有项目的否决项",
        "核对全库否决项",
        "用这些资料核对否决项",
        "核对附件中的否决项",
        "从https://example.test核对否决项",
    ],
)
def test_ambiguous_or_expanded_sources_never_implicitly_select_other_data(text):
    result = interpret(TaskDraftRequest(request=text))
    assert result.source_selection.mode == "current_project"
    assert any(item.field == "source_selection" for item in result.unresolved)


def test_instruction_does_not_authorize_formal_decisions():
    result = interpret(TaskDraftRequest(request="核对否决并自动确认所有规则、登记提交、发布模型"))
    assert any(item.field == "instructions" for item in result.unresolved)
    assert not result.formal_decisions_allowed


def test_request_is_bounded_and_rejects_extra_ids_and_empty_text():
    for data in (
        {"request": " "},
        {"request": "x" * 8001},
        {"request": "核对否决", "operation_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            TaskDraftRequest.model_validate(data)
