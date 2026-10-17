"""Immutable task runs over existing domain commands; no model calls or formal decisions."""

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, fetch, sha, uid
from packages.lifecycle.service import assert_work_writable, lock
from packages.tasks import inputs, templates


def operations(c, row):
    manifest = row["frozen_manifest"]
    ids = [row["operation_id"]]
    if manifest["task_kind"] == "denial_check":
        request = c.execute(
            "SELECT actor_id FROM commands WHERE id=%s", (manifest["review_request_id"],)
        ).fetchone()
        if request is None or str(request["actor_id"]) != manifest["review_request_actor_id"]:
            return ids
        resolved = inputs.prepared_context(c, row["bid_id"], row["id"])
        if (
            resolved is None
            or str(resolved["actor_id"]) != manifest["actor_id"]
            or str(resolved["command_id"]) != manifest["request_id"]
        ):
            return ids
        linked = c.execute(
            "SELECT a.command_id,a.payload,r.operation_id,i.manifest FROM audit_entries a JOIN review_runs r ON r.id=(a.payload->>'review_id')::uuid AND r.bid_id=a.bid_id JOIN review_inputs i ON i.id=r.input_id WHERE a.bid_id=%s AND a.event_type='review.frozen' AND a.payload->>'request_id'=%s",
            (row["bid_id"], manifest["review_request_id"]),
        ).fetchall()
        valid = []
        for link in linked:
            m = link["manifest"]
            context = resolved["payload"]["context"]
            if (
                str(m.get("body_version_id")) == str(context["body_version_id"])
                and str(m.get("material_snapshot_id")) == str(context["material_snapshot_id"])
                and str(m.get("rule_set_id")) == str(context["rule_set_id"])
                and str(m.get("fact_set_id"))
                == str((context["fact_head"] or {}).get("fact_set_id"))
                and m.get("method_digest") == context["method_digest"]
                and m.get("material_revision") == context["material_revision"]
                and m.get("content_checks") == context["content_checks"]
                and set(m.get("parse_run_ids", []))
                >= {d["parse_id"] for d in context["documents"] if d["parse_id"]}
            ):
                valid.append(link)
        if len(linked) == 1 and len(valid) == 1:
            ids.append(valid[0]["operation_id"])
    return ids


def billing_operations(c, row):
    ids = operations(c, row)
    frozen = row["frozen_manifest"]
    if frozen["task_kind"] == "denial_check":
        possible = c.execute(
            "SELECT r.operation_id FROM audit_entries a JOIN review_runs r ON r.id=(a.payload->>'review_id')::uuid AND r.bid_id=a.bid_id WHERE a.bid_id=%s AND a.event_type='review.frozen' AND a.payload->>'request_id'=%s",
            (row["bid_id"], frozen["review_request_id"]),
        ).fetchall()
        ids.extend(r["operation_id"] for r in possible if r["operation_id"] not in ids)
        preparation = c.execute(
            "SELECT id FROM operations WHERE bid_id=%s AND command_id=%s AND kind IN('prepare-review','review')",
            (row["bid_id"], frozen["review_request_id"]),
        ).fetchall()
        ids.extend(r["id"] for r in preparation if r["id"] not in ids)
    return ids


def view(c, bid, identity):
    row = fetch(c, "SELECT * FROM task_runs WHERE bid_id=%s AND id=%s", (bid, identity))
    frozen = row["frozen_manifest"]
    ids = operations(c, row)
    root = fetch(
        c, "SELECT id,state FROM operations WHERE bid_id=%s AND id=%s", (bid, row["operation_id"])
    )
    result = c.execute(
        "SELECT result FROM effect_receipts WHERE operation_id=%s ORDER BY effect_key LIMIT 1",
        (ids[-1],),
    ).fetchone()
    state = root["state"]
    stage = "execution"
    error = None
    if frozen["task_kind"] == "denial_check":
        stage = "preparation"
        if len(ids) > 1:
            actual = fetch(
                c, "SELECT id,state FROM operations WHERE bid_id=%s AND id=%s", (bid, ids[-1])
            )
            state = actual["state"]
            stage = "review"
        elif state == "succeeded":
            state = "awaiting_review"
        slot = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bid,)
        ).fetchone()
        intent = (slot or {}).get("pending_intent") or {}
        if (
            len(ids) == 1
            and intent.get("request_id") == frozen.get("review_request_id", frozen["request_id"])
            and intent.get("state") in ("blocked", "superseded")
        ):
            state = intent["state"]
            error = intent.get("error_code")
        if len(ids) == 1 and intent.get("request_id") != frozen.get(
            "review_request_id", frozen["request_id"]
        ):
            superseding = c.execute(
                "SELECT 1 FROM audit_entries WHERE bid_id=%s AND event_type='review.requested' AND payload->>'previous_request_id'=%s LIMIT 1",
                (bid, frozen["review_request_id"]),
            ).fetchone()
            state = "superseded" if superseding else "unknown"
            error = (
                "TASK_REVIEW_REQUEST_SUPERSEDED" if superseding else "TASK_REVIEW_LINK_UNRESOLVED"
            )
    if frozen["task_kind"] == "denial_check" and len(ids) == 1:
        reported = c.execute(
            "SELECT 1 FROM audit_entries WHERE bid_id=%s AND event_type='review.frozen' AND payload->>'request_id'=%s LIMIT 1",
            (bid, frozen["review_request_id"]),
        ).fetchone()
        if reported:
            state = "unknown"
            error = "TASK_REVIEW_LINK_UNRESOLVED"
    if c.execute(
        "SELECT 1 FROM model_calls WHERE operation_id=ANY(%s::uuid[]) AND state IN('reserved','unknown') LIMIT 1",
        (billing_operations(c, row),),
    ).fetchone():
        state = "reconciliation_required"
        error = "MODEL_CALL_REQUIRES_RECONCILIATION"
    return clean(
        {
            "id": row["id"],
            "bid_id": bid,
            "definition_version_id": row["definition_version_id"],
            "root_operation_id": row["operation_id"],
            "final_operation_id": ids[-1] if len(ids) > 1 else None,
            "state": state,
            "stage": stage,
            "frozen_manifest": frozen,
            "result": (
                {"review_operation_ref": f"/api/v1/operations/{ids[-1]}"}
                if stage == "review"
                else result["result"]
                if result and stage != "preparation"
                else None
            ),
            "error_code": error,
            "formal_decisions_created": False,
            "coverage": "registered_effectivity_only_semantic_unknown"
            if frozen["task_kind"] == "supplement_impact"
            else "not_formal_score",
        }
    )


def _prior(c, bid, version, digest):
    return c.execute(
        "SELECT * FROM task_runs WHERE bid_id=%s AND definition_version_id=%s AND frozen_manifest->>'input_digest'=%s ORDER BY frozen_manifest->>'accepted_at',id",
        (bid, version, digest),
    ).fetchall()


def dispatch(
    c,
    *,
    bid,
    definition,
    actor,
    command,
    trigger="manual",
    event_id=None,
    scheduled_at=None,
    rerun=False,
):
    lock(c, bid)
    assert_work_writable(c, bid)
    row = templates.current(c, bid, definition)
    if row["paused"]:
        raise DomainError("TASK_TEMPLATE_PAUSED", "模板已暂停；在途任务没有取消", 409)
    config = row["config"]["definition"]
    kind = config["task_kind"]
    if kind == "scoring_review":
        raise DomainError(
            "TASK_SCORING_NOT_QUALIFIED",
            "正式评分能力尚未开放，未创建评分运行",
            409,
            {"coverage": "unavailable", "formal_scoring_eligible": False},
        )
    if kind == "denial_check" and config["source_selection"]["mode"] != "current_project":
        raise DomainError(
            "TASK_REVIEW_SCOPE_FIXED",
            "否决核对使用完整当前项目资料，不用选定片段缩小正式核对范围",
            422,
        )
    if kind == "denial_check":
        from packages.domain import reviews

        if getattr(reviews, "TASK_INPUT_GUARD", None) is not inputs.review_guard:
            raise DomainError(
                "TASK_REVIEW_EXECUTOR_NOT_READY",
                "任务评审的固定输入保护尚未接通，未创建新运行",
                503,
            )
    context = inputs.capture(c, bid, config)
    digest = sha({"version_id": row["version_id"], "purpose": row["purpose"], "context": context})
    # Fee safety excludes template identity/instructions: a new version/nonce cannot bypass unknown cost.
    scope = sha(
        {
            "task_kind": kind,
            "body": context["body_version_id"],
            "material": context["material_snapshot_id"],
            "section": context["section"],
        }
    )
    for old in c.execute(
        "SELECT * FROM task_runs WHERE bid_id=%s AND frozen_manifest->>'fee_scope'=%s", (bid, scope)
    ).fetchall():
        if c.execute(
            "SELECT 1 FROM model_calls WHERE operation_id=ANY(%s::uuid[]) AND state IN('reserved','unknown') LIMIT 1",
            (billing_operations(c, old),),
        ).fetchone():
            raise DomainError(
                "MODEL_CALL_REQUIRES_RECONCILIATION",
                "原调用费用未知，未授权新尝试",
                409,
                {"new_attempt_authorized": False},
            )
    if kind == "denial_check":
        candidates = c.execute(
            "SELECT * FROM task_runs WHERE bid_id=%s AND frozen_manifest->>'fee_scope'=%s ORDER BY frozen_manifest->>'accepted_at' DESC,id DESC",
            (bid, scope),
        ).fetchall()
        for old in candidates:
            resolved = inputs.prepared_context(c, bid, old["id"])
            expected = (
                resolved["payload"]["context"] if resolved else old["frozen_manifest"]["context"]
            )
            previous = view(c, bid, old["id"])
            if inputs.equivalent_context(
                expected, context, preparing=not bool(resolved)
            ) and previous["state"] in ("queued", "running", "cancel_requested", "awaiting_review"):
                audit(
                    c,
                    actor,
                    bid,
                    "task.run_reused",
                    {
                        "run_id": old["id"],
                        "definition_version_id": row["version_id"],
                        "trigger": trigger,
                        "trigger_event_id": event_id,
                        "scheduled_at": scheduled_at,
                    },
                    command,
                )
                return previous, True
    prior = _prior(c, bid, row["version_id"], digest)
    if prior and (
        not rerun
        or view(c, bid, prior[-1]["id"])["state"]
        in ("queued", "running", "cancel_requested", "awaiting_review")
    ):
        audit(
            c,
            actor,
            bid,
            "task.run_reused",
            {
                "run_id": prior[-1]["id"],
                "definition_version_id": row["version_id"],
                "trigger": trigger,
                "trigger_event_id": event_id,
                "scheduled_at": scheduled_at,
            },
            command,
        )
        return view(c, bid, prior[-1]["id"]), True
    identity = uid()
    frozen = clean(
        {
            "schema": "task-run/1",
            "definition_id": definition,
            "definition_version_id": row["version_id"],
            "configuration": config,
            "task_kind": kind,
            "purpose": row["purpose"],
            "actor_id": actor,
            "request_id": command,
            "context": context,
            "input_digest": digest,
            "fee_scope": scope,
            "trigger": trigger,
            "trigger_event_id": event_id,
            "scheduled_at": scheduled_at,
            "accepted_at": templates.clock(c),
            "formal_decisions_allowed": False,
        }
    )
    if kind == "denial_check":
        from packages.domain.reviews import queue_intent

        slot = fetch(c, "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bid,))
        previous = slot["pending_intent"] or {}
        equivalent = (
            not rerun
            and previous.get("state") in ("preparing", "queued")
            and previous.get("body_version_id") == context["body_version_id"]
            and previous.get("material_snapshot_id") == context["material_snapshot_id"]
            and previous.get("material_revision") == context["material_revision"]
            and previous.get("content_checks") == context["content_checks"]
            and not previous.get("task_binding")
        )
        if equivalent:
            intent = previous
            operation = UUID(intent["prepare_operation_id"])
        else:
            accepted = queue_intent(
                c,
                bid,
                command,
                {
                    "body_version_id": context["body_version_id"],
                    "material_revision": context["material_revision"],
                    "trigger": "manual",
                    "rerun_nonce": str(command) if rerun else None,
                },
            )
            operation = UUID(accepted["operation"]["id"])
            intent = fetch(c, "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bid,))[
                "pending_intent"
            ]
        request_actor = fetch(
            c, "SELECT actor_id FROM commands WHERE id=%s", (intent["request_id"],)
        )["actor_id"]
        frozen["review_request_id"] = intent["request_id"]
        frozen["review_request_actor_id"] = str(request_actor)
        intent["task_binding"] = {
            "run_id": str(identity),
            "actor_id": str(actor),
            "input_digest": digest,
        }
        c.execute("UPDATE review_slots SET pending_intent=%s WHERE bid_id=%s", (Jsonb(intent), bid))
    elif kind == "chapter_suggestion":
        from packages.assistant.jobs import queue_generation

        section = context["section"]
        accepted = queue_generation(
            c,
            bid,
            actor,
            {
                "section_id": section["id"],
                "base_section_revision": section["head_revision"],
                "instruction": config["instructions"],
                "source_version_ids": [d["id"] for d in context["documents"]],
            },
            command,
        )
        operation = UUID(accepted["operation"]["id"])
        frozen["assistant_job_id"] = accepted["id"]
    else:
        from packages.domain.source_effectivity import evidence_impacts

        evidence: list[str] = []
        for document in context["documents"]:
            if document["parse_id"]:
                evidence.extend(
                    str(r["id"])
                    for r in c.execute(
                        "SELECT id FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s ORDER BY id LIMIT 2001",
                        (bid, document["parse_id"]),
                    ).fetchall()
                )
        if len(evidence) > 2000:
            raise DomainError("TASK_SCOPE_TOO_LARGE", "补遗证据超过单次范围，未截断执行", 413)
        result = {
            "impacts": evidence_impacts(c, bid, evidence),
            "coverage": "registered_effectivity_only",
            "semantic_impact_coverage": "unknown",
            "formal_decisions_created": False,
        }
        operation = uid()
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'task-registered-impact',%s,'succeeded','task-local/1',clock_timestamp())",
            (operation, bid, command, digest),
        )
        c.execute(
            "INSERT INTO effect_receipts(operation_id,effect_key,result) VALUES(%s,'registered-effectivity-impact',%s)",
            (operation, Jsonb(result)),
        )
    c.execute(
        "INSERT INTO task_runs(id,bid_id,definition_version_id,operation_id,frozen_manifest) VALUES(%s,%s,%s,%s,%s)",
        (identity, bid, row["version_id"], operation, Jsonb(frozen)),
    )
    audit(
        c,
        actor,
        bid,
        "task.run_created",
        {
            "run_id": identity,
            "definition_version_id": row["version_id"],
            "operation_id": operation,
            "input_digest": digest,
        },
        command,
    )
    return view(c, bid, identity), False


def notify(c, bid, identity):
    lock(c, bid)
    value = view(c, bid, identity)
    if value["state"] not in (
        "succeeded",
        "failed",
        "cancelled",
        "blocked",
        "superseded",
        "unknown",
        "reconciliation_required",
    ):
        return False
    key = sha(
        {
            "run_id": identity,
            "state": value["state"],
            "error": value["error_code"],
            "result": value["result"],
        }
    )
    if c.execute(
        "SELECT 1 FROM audit_entries WHERE bid_id=%s AND event_type='task.notification' AND payload->>'key'=%s",
        (bid, key),
    ).fetchone():
        return False
    audit(
        c,
        UUID(value["frozen_manifest"]["actor_id"]),
        bid,
        "task.notification",
        {
            "key": key,
            "run_id": identity,
            "state": value["state"],
            "error_code": value["error_code"],
            "run_ref": f"/api/v1/bids/{bid}/task-runs/{identity}",
        },
    )
    return True
