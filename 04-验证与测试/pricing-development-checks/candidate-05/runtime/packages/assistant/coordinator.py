"""API-role final authority/materialization. No model/provider/network work here."""

import argparse
import signal
import threading
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from packages.assistant import actions, context, jobs
from packages.assistant.generation import citation_position, validate_answer, validate_generation
from packages.domain.runtime import DomainError, assert_role, audit, connection, fetch, sha, uid

STOP = threading.Event()


def publish(job_id):
    with connection() as c:
        jobs.require_schema(c)
        row = jobs.job(c, job_id)
        manifest = row["input_manifest"]
        if row["kind"] == "answer":
            bids = context.turn_bids(c, manifest["request"], row["bid_id"])
        else:
            bids = [row["bid_id"]]
        context.lock_scope(c, bids)
        if row["scope"] == "global":
            jobs.global_get(c, row["global_conversation_id"], row["actor_id"], lock=True)
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("assistant-publication:" + str(job_id),),
        )
        old = c.execute(
            "SELECT 1 FROM assistant_publications WHERE job_id=%s", (job_id,)
        ).fetchone()
        if old:
            return jobs.view(c, job_id)
        output = c.execute("SELECT * FROM assistant_outputs WHERE job_id=%s", (job_id,)).fetchone()
        if not output:
            return jobs.view(c, job_id)
        if sha(output["payload"]) != output["digest"]:
            raise DomainError("ASSISTANT_OUTPUT_CORRUPTED", "助手候选摘要校验失败", 409)
        if row["scope"] == "project":
            op = fetch(
                c,
                "SELECT state FROM operations WHERE id=%s AND bid_id=%s",
                (row["operation_id"], row["bid_id"]),
            )
        else:
            op = fetch(c, "SELECT state FROM assistant_global_runs WHERE id=%s", (job_id,))
        if op["state"] != "succeeded":
            return jobs.view(c, job_id)
        try:
            with c.transaction():
                if row["kind"] == "authoring":
                    from packages.authoring.generation import accept_generated_proposal
                    from packages.lifecycle.service import assert_work_writable

                    assert_work_writable(c, row["bid_id"])
                    context.assert_generation_context(c, row["bid_id"], manifest["project_memory"])
                    value = validate_generation(manifest, output["payload"], canonical=True)
                    result = accept_generated_proposal(
                        c, row["bid_id"], row["writing_plan_id"], value
                    )
                    actions.link(c, UUID(result["action_id"]), job_id, "authoring.apply_patch")
                    c.execute(
                        "INSERT INTO assistant_publications(job_id,scope,kind,state,action_id) VALUES(%s,'project','authoring','published',%s)",
                        (job_id, UUID(result["action_id"])),
                    )
                    audit(
                        c,
                        row["actor_id"],
                        row["bid_id"],
                        "authoring.proposal_ready",
                        {"job_id": job_id, "action_id": result["action_id"], "applied": False},
                    )
                else:
                    context.revalidate_turn(
                        c, manifest["context"], manifest["request"], row["bid_id"]
                    )
                    value = validate_answer(manifest, output["payload"])
                    source_map = {s["source_key"]: s for s in manifest["context"]["sources"]}
                    cards = []
                    rejected = []
                    for proposal in value["tool_proposals"]:
                        try:
                            with c.transaction():
                                cards.append(str(actions.prepare(c, row, proposal)))
                        except DomainError as error:
                            rejected.append(
                                {
                                    "tool": proposal["tool"],
                                    "target_bid_id": proposal["target_bid_id"],
                                    "error_code": error.code,
                                }
                            )
                    citations = [
                        quote
                        | {
                            "position": citation_position(
                                source_map[quote["source_key"]], quote["quote"]
                            )
                        }
                        for quote in value["citations"]
                    ]
                    suggestions = [
                        item
                        | {
                            "suggestion_id": str(uuid5(job_id, "issue:" + str(i))),
                            "record_kind": "assistant_suggestion",
                            "official_finding_id": None,
                            "resolved": False,
                        }
                        for i, item in enumerate(value["issue_suggestions"])
                    ]
                    parts = [
                        {"type": "text", "text": value["answer"]},
                        {
                            "type": "assistant_reference_answer",
                            "job_id": str(job_id),
                            "citations": citations,
                            "issue_suggestions": suggestions,
                            "action_ids": cards,
                            "rejected_proposals": rejected,
                            "gaps": value["gaps"],
                            "formal_scoring_input": False,
                            "all_actions_require_explicit_button": True,
                        },
                    ]
                    identity = uid()
                    if row["scope"] == "project":
                        c.execute(
                            "INSERT INTO messages(id,bid_id,conversation_id,role,parts,created_by) VALUES(%s,%s,%s,'assistant',%s,%s)",
                            (
                                identity,
                                row["bid_id"],
                                row["project_conversation_id"],
                                Jsonb(parts),
                                row["actor_id"],
                            ),
                        )
                        c.execute(
                            "UPDATE conversations SET revision=revision+1 WHERE id=%s",
                            (row["project_conversation_id"],),
                        )
                        c.execute(
                            "INSERT INTO assistant_publications(job_id,scope,kind,state,project_message_id) VALUES(%s,'project','answer','published',%s)",
                            (job_id, identity),
                        )
                    else:
                        c.execute(
                            "INSERT INTO assistant_global_messages(id,conversation_id,role,parts,created_by) VALUES(%s,%s,'assistant',%s,%s)",
                            (
                                identity,
                                row["global_conversation_id"],
                                Jsonb(parts),
                                row["actor_id"],
                            ),
                        )
                        c.execute(
                            "UPDATE assistant_global_conversations SET revision=revision+1 WHERE id=%s",
                            (row["global_conversation_id"],),
                        )
                        c.execute(
                            "INSERT INTO assistant_publications(job_id,scope,kind,state,global_message_id) VALUES(%s,'global','answer','published',%s)",
                            (job_id, identity),
                        )
                        jobs.global_event(
                            c,
                            row["global_conversation_id"],
                            "assistant.answer_ready",
                            {
                                "job_id": str(job_id),
                                "message_id": str(identity),
                                "action_ids": cards,
                            },
                        )
                    audit(
                        c,
                        row["actor_id"],
                        row["bid_id"],
                        "assistant.answer_ready",
                        {
                            "job_id": job_id,
                            "message_id": identity,
                            "action_ids": cards,
                            "formal_scoring_input": False,
                        },
                    )
        except DomainError as error:
            state = "stale" if error.status in (409, 412) else "failed"
            c.execute(
                "INSERT INTO assistant_publications(job_id,scope,kind,state,error_code) VALUES(%s,%s,%s,%s,%s)",
                (job_id, row["scope"], row["kind"], state, error.code),
            )
            if row["scope"] == "global":
                jobs.global_event(
                    c,
                    row["global_conversation_id"],
                    "assistant.candidate_not_adopted",
                    {"job_id": str(job_id), "state": state, "error_code": error.code},
                )
            audit(
                c,
                row["actor_id"],
                row["bid_id"],
                "assistant.candidate_not_adopted",
                {"job_id": job_id, "state": state, "error_code": error.code},
            )
        return jobs.view(c, job_id)


def tick(job_ids=None):
    with connection() as c:
        jobs.require_schema(c)
        rows = c.execute(
            "SELECT j.id FROM assistant_jobs j JOIN assistant_outputs o ON o.job_id=j.id LEFT JOIN assistant_publications p ON p.job_id=j.id WHERE p.job_id IS NULL AND (%s::uuid[] IS NULL OR j.id=ANY(%s::uuid[])) ORDER BY j.created_at,j.id LIMIT 100",
            (job_ids, job_ids),
        ).fetchall()
    for row in rows:
        publish(row["id"])
    sync_terminal_events(job_ids)
    expire_actions(job_ids)
    return len(rows)


def sync_terminal_events(job_ids=None):
    with connection() as c:
        rows = c.execute(
            """SELECT r.id,r.conversation_id,r.actor_id,r.state,r.error_code FROM assistant_global_runs r
            WHERE r.state IN ('succeeded','failed','cancelled') AND (%s::uuid[] IS NULL OR r.id=ANY(%s::uuid[]))
            AND NOT EXISTS(SELECT 1 FROM assistant_global_events e WHERE e.event_type='assistant.execution_finished' AND e.payload->>'job_id'=r.id::text)
            ORDER BY r.created_at,r.id LIMIT 100""",
            (job_ids, job_ids),
        ).fetchall()
    for row in rows:
        with connection() as c:
            jobs.global_get(c, row["conversation_id"], row["actor_id"], lock=True)
            event = uuid5(row["id"], "execution-finished")
            if not c.execute(
                "SELECT 1 FROM assistant_global_events WHERE event_id=%s", (event,)
            ).fetchone():
                jobs.global_event(
                    c,
                    row["conversation_id"],
                    "assistant.execution_finished",
                    {
                        "job_id": str(row["id"]),
                        "state": row["state"],
                        "error_code": row["error_code"],
                        "model_result_is_not_domain_approval": True,
                    },
                    event,
                )


def expire_actions(job_ids=None):
    with connection() as c:
        rows = c.execute(
            """SELECT l.action_id FROM assistant_action_links l JOIN proposed_actions a ON a.id=l.action_id
            WHERE l.state='pending' AND a.expires_at<=clock_timestamp() AND (%s::uuid[] IS NULL OR l.job_id=ANY(%s::uuid[]))
            ORDER BY a.expires_at,l.action_id LIMIT 100""",
            (job_ids, job_ids),
        ).fetchall()
    for row in rows:
        with connection() as c:
            (link, proposed), source = actions.lock(c, row["action_id"])
            if link["state"] != "pending" or proposed["expires_at"] > context.at(c):
                continue
            if c.execute(
                "SELECT 1 FROM action_consumptions WHERE action_id=%s", (row["action_id"],)
            ).fetchone():
                continue
            c.execute(
                "UPDATE assistant_action_links SET state='expired',revision=revision+1,resolved_at=clock_timestamp(),error_code='ACTION_EXPIRED' WHERE action_id=%s",
                (row["action_id"],),
            )
            audit(
                c,
                None,
                proposed["bid_id"],
                "assistant.action_expired",
                {
                    "action_id": row["action_id"],
                    "resolution_origin": "system_expiry",
                    "business_effect_executed": False,
                },
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--job-id", type=UUID, action="append", dest="job_ids")
    args = parser.parse_args()
    assert_role()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    while not STOP.is_set():
        count = tick(args.job_ids)
        if args.once:
            print({"materialized": count, "model_calls": 0})
            break
        STOP.wait(2)


if __name__ == "__main__":
    main()
