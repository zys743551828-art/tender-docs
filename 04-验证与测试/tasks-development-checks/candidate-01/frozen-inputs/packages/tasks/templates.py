"""Immutable template revisions; saving and pausing never enqueue a model operation."""

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, fetch, sha, uid
from packages.tasks.models import TemplateRequest
from packages.tasks.scheduling import Schedule

PURPOSE = {
    "denial_check": "compliance",
    "scoring_review": "compliance",
    "supplement_impact": "assist",
    "chapter_suggestion": "assist",
}


def clock(c):
    return c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]


def current(c, bid, definition):
    row = fetch(
        c,
        """SELECT d.*,v.id AS version_id,v.config,v.digest FROM task_definitions d
           JOIN task_definition_versions v ON v.definition_id=d.id AND v.revision=d.revision
           WHERE d.bid_id=%s AND d.id=%s""",
        (bid, definition),
    )
    if row["config"].get("schema") != "task-template/1" or sha(row["config"]) != row["digest"]:
        raise DomainError("TASK_TEMPLATE_INTEGRITY", "任务模板完整性检查未通过，请查看原版本", 409)
    return row


def view(c, bid, definition):
    row = current(c, bid, definition)
    scheduled = c.execute(
        """SELECT id,next_fire_at FROM schedules WHERE definition_id=%s
           AND definition_revision=%s AND NOT paused ORDER BY id""",
        (definition, row["revision"]),
    ).fetchone()
    return clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            "revision": row["revision"],
            "version_id": row["version_id"],
            "digest": row["digest"],
            "purpose": row["purpose"],
            "paused": row["paused"],
            "configuration": row["config"]["definition"],
            "next_run_at": scheduled["next_fire_at"] if scheduled else None,
            "run_created": False,
            "active_runs_cancelled": False,
        }
    )


def assert_targets(c, bid, definition):
    if definition.section_id:
        fetch(c, "SELECT id FROM sections WHERE bid_id=%s AND id=%s", (bid, definition.section_id))
    for version in definition.source_selection.version_ids:
        fetch(c, "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s", (bid, version))


def append_version(c, bid, definition, revision, request, paused):
    config = {
        "schema": "task-template/1",
        "definition": request.model_dump(mode="json"),
        "paused": paused,
        "formal_decisions_allowed": False,
    }
    version = uid()
    c.execute(
        """INSERT INTO task_definition_versions
           (id,bid_id,definition_id,revision,instructions,config,digest)
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (version, bid, definition, revision, request.instructions, Jsonb(config), sha(config)),
    )
    # Superseded schedules cannot fire; already accepted operations and their inputs stay intact.
    c.execute("UPDATE schedules SET paused=true WHERE definition_id=%s", (definition,))
    if request.trigger.mode == "schedule" and not paused:
        schedule = Schedule.parse(request.trigger.cron, request.trigger.timezone)
        c.execute(
            """INSERT INTO schedules(id,bid_id,definition_id,definition_revision,
               cron,timezone,next_fire_at,paused) VALUES(%s,%s,%s,%s,%s,%s,%s,false)""",
            (
                uid(),
                bid,
                definition,
                revision,
                schedule.cron,
                schedule.timezone,
                schedule.next_after(clock(c)),
            ),
        )
    return version


def create(c, bid, actor, request, command):
    assert_targets(c, bid, request)
    definition = uid()
    c.execute(
        """INSERT INTO task_definitions(id,bid_id,revision,name,purpose,instructions,paused)
           VALUES(%s,%s,1,%s,%s,%s,false)""",
        (definition, bid, request.name, PURPOSE[request.task_kind], request.instructions),
    )
    version = append_version(c, bid, definition, 1, request, False)
    audit(
        c,
        actor,
        bid,
        "task.template_created",
        {"definition_id": definition, "version_id": version},
        command,
    )
    return view(c, bid, definition)


def revise(c, bid, definition, actor, request, command):
    row = current(c, bid, definition)
    assert_targets(c, bid, request)
    revision = row["revision"] + 1
    version = append_version(c, bid, definition, revision, request, row["paused"])
    c.execute(
        """UPDATE task_definitions SET revision=%s,name=%s,purpose=%s,instructions=%s
           WHERE id=%s""",
        (revision, request.name, PURPOSE[request.task_kind], request.instructions, definition),
    )
    audit(
        c,
        actor,
        bid,
        "task.template_revised",
        {"definition_id": definition, "version_id": version},
        command,
    )
    return view(c, bid, definition)


def pause(c, bid, definition, actor, paused, command):
    row = current(c, bid, definition)
    if row["paused"] == paused:
        return view(c, bid, definition)
    request = TemplateRequest.model_validate(row["config"]["definition"])
    revision = row["revision"] + 1
    version = append_version(c, bid, definition, revision, request, paused)
    c.execute(
        "UPDATE task_definitions SET revision=%s,paused=%s WHERE id=%s",
        (revision, paused, definition),
    )
    audit(
        c,
        actor,
        bid,
        "task.template_pause_changed",
        {
            "definition_id": definition,
            "version_id": version,
            "paused": paused,
            "active_runs_cancelled": False,
        },
        command,
    )
    return view(c, bid, definition)


def page(c, bid, limit, after=None):
    fetch(c, "SELECT id FROM bids WHERE id=%s", (bid,))
    rows = c.execute(
        """SELECT id FROM task_definitions WHERE bid_id=%s AND (%s::uuid IS NULL OR id>%s::uuid)
           ORDER BY id LIMIT %s""",
        (bid, after, after, limit + 1),
    ).fetchall()
    return {
        "items": [view(c, bid, row["id"]) for row in rows[:limit]],
        "has_more": len(rows) > limit,
        "last_id": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


def version(c, bid, definition, version_id):
    current(c, bid, definition)
    row = fetch(
        c,
        "SELECT * FROM task_definition_versions WHERE bid_id=%s AND definition_id=%s AND id=%s",
        (bid, definition, UUID(str(version_id))),
    )
    if sha(row["config"]) != row["digest"]:
        raise DomainError("TASK_TEMPLATE_INTEGRITY", "任务模板完整性检查未通过，请查看原版本", 409)
    return clean(row)
