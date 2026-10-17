"""API-role bounded trigger coordinator. No provider calls or browser-dependent timers."""

from datetime import timedelta
from uuid import UUID

from packages.domain.runtime import Command, DomainError, audit, clean, fetch, sha
from packages.lifecycle.service import assert_work_writable, lock
from packages.tasks import runs, templates
from packages.tasks.scheduling import Schedule, recover


def owner(c, bid, definition):
    row = fetch(
        c,
        "SELECT actor_id FROM audit_entries WHERE bid_id=%s AND event_type='task.template_created' AND payload->>'definition_id'=%s",
        (bid, str(definition)),
    )
    return row["actor_id"]


def receipt(c, bid, key):
    return c.execute(
        "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='task.trigger_received' AND payload->>'key'=%s",
        (bid, key),
    ).fetchone()


def accept(c, *, bid, definition, actor, trigger, key, event_id=None, scheduled_at=None):
    old = receipt(c, bid, key)
    if old:
        return old["payload"]
    cmd = Command(
        c,
        actor,
        "task-trigger",
        definition,
        key,
        {
            "trigger": trigger,
            "event_id": str(event_id) if event_id else None,
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        },
    )
    if cmd.replay:
        return cmd.replay["body"]
    try:
        with c.transaction():
            value, reused = runs.dispatch(
                c,
                bid=bid,
                definition=definition,
                actor=actor,
                command=cmd.id,
                trigger=trigger,
                event_id=event_id,
                scheduled_at=scheduled_at,
            )
        outcome = {
            "key": key,
            "trigger": trigger,
            "event_id": str(event_id) if event_id else None,
            "run_id": value["id"],
            "reused": reused,
            "state": "accepted",
            "error_code": None,
        }
    except DomainError as error:
        outcome = {
            "key": key,
            "trigger": trigger,
            "event_id": str(event_id) if event_id else None,
            "run_id": None,
            "reused": False,
            "state": "blocked",
            "error_code": error.code,
        }
    outcome["definition_version_id"] = str(templates.current(c, bid, definition)["version_id"])
    outcome["scheduled_at"] = scheduled_at.isoformat() if scheduled_at else None
    audit(c, actor, bid, "task.trigger_received", outcome, cmd.id)
    cmd.finish(outcome)
    return outcome


def material_committed(c, *, bid, event_id, limit=100):
    """Server hook accepts only a persisted materials.committed audit identity, never client events."""
    lock(c, bid)
    event = fetch(
        c,
        "SELECT * FROM audit_entries WHERE bid_id=%s AND id=%s AND event_type='materials.committed'",
        (bid, event_id),
    )
    profile = fetch(c, "SELECT current_material_id FROM bid_profiles WHERE bid_id=%s", (bid,))
    rows = c.execute(
        "SELECT d.id FROM task_definitions d JOIN task_definition_versions v ON v.definition_id=d.id AND v.revision=d.revision WHERE d.bid_id=%s AND NOT d.paused AND v.config->'definition'->'trigger'->>'mode'='material_updated' ORDER BY d.id LIMIT %s",
        (bid, limit + 1),
    ).fetchall()
    if len(rows) > limit:
        raise DomainError(
            "TASK_TRIGGER_SCOPE_TOO_LARGE", "事件模板数量超过本轮范围，未部分执行", 413
        )
    values = []
    for row in rows:
        definition = templates.current(c, bid, row["id"])
        key = sha({"event_id": event_id, "version": definition["version_id"]})
        old = receipt(c, bid, key)
        if old:
            values.append(old["payload"])
            continue
        activation = c.execute(
            "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='task.event_window_started' AND payload->>'definition_version_id'=%s",
            (bid, str(definition["version_id"])),
        ).fetchall()
        if len(activation) != 1 or str(event_id) in activation[0]["payload"]["existing_event_ids"]:
            outcome = {
                "key": key,
                "event_id": str(event_id),
                "definition_version_id": str(definition["version_id"]),
                "state": "ignored_before_activation",
                "run_id": None,
                "error_code": "TASK_EVENT_NOT_IN_ACTIVE_WINDOW",
                "reused": False,
            }
            audit(c, event["actor_id"], bid, "task.trigger_received", outcome, event["command_id"])
            values.append(outcome)
            continue
        if str(profile["current_material_id"]) != str(event["payload"]["snapshot_id"]):
            outcome = {
                "key": key,
                "event_id": str(event_id),
                "definition_version_id": str(definition["version_id"]),
                "state": "stale",
                "run_id": None,
                "error_code": "TASK_EVENT_SUPERSEDED",
                "reused": False,
            }
            audit(c, event["actor_id"], bid, "task.trigger_received", outcome, event["command_id"])
            values.append(outcome)
            continue
        actor = event["actor_id"] or owner(c, bid, definition["id"])
        values.append(
            accept(
                c,
                bid=bid,
                definition=definition["id"],
                actor=actor,
                trigger="material_updated",
                key=key,
                event_id=event_id,
            )
        )
    return values


def tick(c, *, bid, limit=50):
    """Caller performs bounded bid discovery without row locks; every mutation locks bid first."""
    lock(c, bid)
    assert_work_writable(c, bid)
    observed = templates.clock(c)
    rows = c.execute(
        "SELECT s.* FROM schedules s JOIN task_definitions d ON d.id=s.definition_id WHERE s.bid_id=%s AND NOT s.paused AND NOT d.paused AND d.revision=s.definition_revision AND s.next_fire_at<=%s ORDER BY s.next_fire_at,s.id LIMIT %s FOR UPDATE OF s",
        (bid, observed, limit),
    ).fetchall()
    outcomes = []
    for row in rows:
        definition = templates.current(c, bid, row["definition_id"])
        schedule = Schedule.parse(row["cron"], row["timezone"])
        due = row["next_fire_at"]
        if observed - due >= timedelta(minutes=1):
            recovery = recover(schedule, due, observed)
            value = {
                "schedule_id": str(row["id"]),
                "definition_id": str(row["definition_id"]),
                "run_current_once_ref": f"/api/v1/bids/{bid}/task-definitions/{row['definition_id']}/runs",
                "definition_version_id": str(definition["version_id"]),
                **clean(recovery),
            }
            audit(c, owner(c, bid, definition["id"]), bid, "task.schedule_skipped", value)
            outcomes.append(value)
        else:
            key = sha(
                {
                    "version_id": definition["version_id"],
                    "scheduled_at": due,
                    "target": definition["config"]["definition"]["section_id"],
                }
            )
            value = accept(
                c,
                bid=bid,
                definition=definition["id"],
                actor=owner(c, bid, definition["id"]),
                trigger="schedule",
                key=key,
                scheduled_at=due,
            )
            if value["run_id"]:
                operation = fetch(
                    c,
                    "SELECT operation_id FROM task_runs WHERE bid_id=%s AND id=%s",
                    (bid, UUID(value["run_id"])),
                )
                c.execute(
                    "INSERT INTO schedule_receipts(schedule_id,scheduled_at,operation_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (row["id"], due, operation["operation_id"]),
                )
            outcomes.append(value)
        c.execute(
            "UPDATE schedules SET next_fire_at=%s WHERE id=%s",
            (schedule.next_after(observed), row["id"]),
        )
    return outcomes


def reconcile_notifications(c, *, bid, limit=100, after=None):
    lock(c, bid)
    rows = c.execute(
        "SELECT id FROM task_runs WHERE bid_id=%s AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s",
        (bid, after, after, limit + 1),
    ).fetchall()
    return {
        "created": sum(runs.notify(c, bid, row["id"]) for row in rows[:limit]),
        "next_after": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


def pending_events(c, *, bid, limit=50):
    rows = c.execute(
        """SELECT a.id FROM audit_entries a WHERE a.bid_id=%s AND a.event_type='materials.committed'
        AND EXISTS(SELECT 1 FROM task_definitions d JOIN task_definition_versions v ON v.definition_id=d.id AND v.revision=d.revision
        WHERE d.bid_id=a.bid_id AND NOT d.paused AND v.config->'definition'->'trigger'->>'mode'='material_updated'
        AND NOT EXISTS(SELECT FROM audit_entries r WHERE r.bid_id=a.bid_id AND r.event_type='task.trigger_received' AND r.payload->>'event_id'=a.id::text AND r.payload->>'definition_version_id'=v.id::text))
        ORDER BY a.id LIMIT %s""",
        (bid, limit),
    ).fetchall()
    return [material_committed(c, bid=bid, event_id=row["id"]) for row in rows]
