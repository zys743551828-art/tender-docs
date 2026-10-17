"""Member-private immutable draft revisions; no template, schedule or work is created."""

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, fetch, sha, uid
from packages.tasks.draft_interpreter import DraftInterpretation, DraftRequest, interpret
from packages.tasks.models import StrictModel, TemplateRequest
from packages.tasks.templates import assert_targets


class DraftView(StrictModel):
    id: UUID
    bid_id: UUID
    created_by: UUID
    revision: int
    created_at: datetime
    interpretation: DraftInterpretation
    configuration_digest: str
    explicitly_edited: bool


def current(c, bid, actor, identity):
    row = fetch(
        c,
        """SELECT payload FROM audit_entries WHERE bid_id=%s AND actor_id=%s
        AND event_type='task.draft_revision' AND payload->>'id'=%s
        ORDER BY (payload->>'revision')::integer DESC LIMIT 1""",
        (bid, actor, str(identity)),
    )
    result = DraftView.model_validate(row["payload"])
    if (
        result.id != identity
        or result.bid_id != bid
        or result.created_by != actor
        or sha(result.interpretation.model_dump(mode="json")) != result.configuration_digest
    ):
        raise DomainError("TASK_DRAFT_INTEGRITY", "任务草案完整性核验失败，请保留原草案", 409)
    return result


def persist(c, *, bid, actor, identity, revision, interpretation, edited, command):
    selected = interpretation
    if selected.section_id:
        fetch(c, "SELECT id FROM sections WHERE bid_id=%s AND id=%s", (bid, selected.section_id))
    for version in selected.source_selection.version_ids:
        fetch(c, "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s", (bid, version))
    created = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    result = DraftView(
        id=identity,
        bid_id=bid,
        created_by=actor,
        revision=revision,
        created_at=created,
        interpretation=selected,
        configuration_digest=sha(selected.model_dump(mode="json")),
        explicitly_edited=edited,
    )
    # Shared project SSE gets only a notification reference, never this private request.
    c.execute(
        """INSERT INTO audit_entries(id,actor_id,bid_id,command_id,event_type,payload)
        VALUES(%s,%s,%s,%s,'task.draft_revision',%s)""",
        (uid(), actor, bid, command, Jsonb(result.model_dump(mode="json"))),
    )
    audit(
        c, actor, bid, "task.draft_changed", {"draft_id": identity, "revision": revision}, command
    )
    return result


def create(c, *, bid, actor, request: DraftRequest, command):
    return persist(
        c,
        bid=bid,
        actor=actor,
        identity=uid(),
        revision=1,
        interpretation=interpret(request),
        edited=False,
        command=command,
    )


def revise(c, *, previous: DraftView, configuration: TemplateRequest, command):
    assert_targets(c, previous.bid_id, configuration)
    original = previous.interpretation
    proposed = original.model_copy(
        update={
            "name": configuration.name,
            "task_kind": configuration.task_kind,
            "instructions": configuration.instructions,
            "section_id": configuration.section_id,
            "source_selection": configuration.source_selection,
            "trigger": configuration.trigger,
            "unresolved": [],
            "ready_for_template_form": True,
            "target_description": "所选章节"
            if configuration.task_kind == "chapter_suggestion"
            else "本项目当前正文与所选来源",
        }
    )
    # Obtain the output description from the selected controlled task, not raw instructions.
    from packages.tasks.draft_interpreter import KINDS

    controlled = interpret(
        DraftRequest(
            request=KINDS[configuration.task_kind][0],
            section_id=configuration.section_id,
            source_selection=configuration.source_selection,
        )
    )
    proposed = proposed.model_copy(update={"output_description": controlled.output_description})
    return persist(
        c,
        bid=previous.bid_id,
        actor=previous.created_by,
        identity=previous.id,
        revision=previous.revision + 1,
        interpretation=proposed,
        edited=True,
        command=command,
    )
