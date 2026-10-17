"""新事实不能仅凭相同问题类型沿用旧误报决定；非ownerPG实际fenced发布。"""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_content_checks import assertion
from test_content_findings_integration import (
    test_verified_conflict_or_engineering_diagnostic_publishes_once_under_fence as seed_first,
)
from test_core_recheck_scope_integration import add_document

from apps.worker import content_handlers, engine
from packages.domain.content_checks import prepare, validate_output
from packages.domain.finding_lifecycle import decision, lifecycle, origin
from packages.domain.human_decisions import append_decision
from packages.domain.review_data import read_bundle, source_closure
from packages.domain.rule_sets import rules
from packages.domain.runtime import assert_role, audit, clean, connection, enqueue_plan, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONTENT_DECISION_APPLICABILITY_INTEGRATION") != "1", reason="需要专用真实PG"
)


@pytest.mark.parametrize(
    "case",
    [
        "identical",
        "reversed_sides",
        "changed_values",
        "changed_method",
        "changed_source_head",
        "legacy_changed_values",
    ],
)
def test_new_body_facts_require_new_decision_while_identical_frozen_body_may_reuse(
    monkeypatch, case
):
    changed = case in ("changed_values", "legacy_changed_values")
    new_decision_required = case in (
        "changed_values",
        "changed_method",
        "changed_source_head",
        "legacy_changed_values",
    )
    assert_role()
    assert_role(True)
    old = {}
    seed_first(monkeypatch, False, capture=old)
    bid, actor, root = old["bid"], old["actor"], old["findings"][0]
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,)).fetchone()
        decided = uuid4()
        c.execute(
            "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'finding',%s,1,'dismiss','仅旧90/120两侧证据的有理由误报决定，未处理任何新稿',%s,%s)",
            (decided, bid, root["id"], root["evidence_ids"], actor),
        )
        c.execute(
            "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'finding',%s,2,%s)",
            (bid, root["id"], decided),
        )
        audit(
            c,
            actor,
            bid,
            "finding.decided",
            {
                "finding_id": str(root["id"]),
                "decision_id": str(decided),
                "decision": "dismiss",
                "fixture": "explicit human decision artifact, not HTTP workflow acceptance",
            },
        )
        body = (
            add_document(
                c,
                bid,
                actor,
                [("usable", "工程A标段一工期95天"), ("usable", "工程A标段一工期125天")],
            )
            if changed
            else old["body"]
        )
        material, rule_set, rule_id, review, input_id, command = [uuid4() for _ in range(6)]
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,2,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        for role, doc in [("body", body), ("tender", old["tender"])]:
            c.execute(
                "INSERT INTO material_assignments(bid_id,snapshot_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,0)",
                (bid, material, doc["version"], role),
            )
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha(str(rule_set))),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands,max_points,applicability_scope) SELECT %s,bid_id,%s,kind,statement,conditions,exceptions,evidence_ids,bands,max_points,applicability_scope FROM rule_items WHERE id=%s",
            (rule_id, rule_set, old["rule_id"]),
        )
        refs = [body["ref"], old["tender"]["ref"] | {"kind": "tender"}]
        if case == "changed_source_head":
            c.execute("SELECT pg_advisory_xact_lock(90127,1)")
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s",
                (body["ref"]["id"],),
            )
            refs = [
                ref | {"lineage_manifest_digest": sha(source_closure(c, [ref]))} for ref in refs
            ]
        manifest = clean(
            old["manifest"]
            | {
                "body_version_id": body["version"],
                "rule_set_id": rule_set,
                "source_refs": refs,
                "parse_run_ids": [body["parse"], old["tender"]["parse"]],
                "as_of": c.execute("SELECT clock_timestamp() AS at").fetchone()["at"],
            }
        )
        evidence = clean(
            {
                "documents": read_bundle(c, bid, material),
                "rules": rules(c, rule_set),
                "confirmed_facts": [],
                "source_closure": source_closure(c, refs),
            }
        )
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'content_decision_applicability_probe',%s,%s,%s)",
            (command, actor, bid, uuid4().hex, sha(str(command))),
        )
        operation = enqueue_plan(
            c,
            bid,
            command,
            "content-decision-applicability",
            {"id": review},
            stages=[{"key": "consistency-check", "depends_on": []}],
            handler_version="document/1",
            topic="tender.task.document.v1",
        )
        c.execute(
            "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)",
            (
                input_id,
                bid,
                material,
                body["version"],
                sha(str(input_id)),
                sha("synthetic-new-method" if case == "changed_method" else "synthetic"),
                Jsonb(manifest),
                body["blob"],
                sha(manifest),
                manifest["as_of"],
                sha("context"),
            ),
        )
        c.execute(
            "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (review, bid, input_id, operation["id"]),
        )
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s", (operation["id"],)
        ).fetchone()["payload"]
    row = {"manifest": manifest, "review_id": str(review)}
    prepared = prepare("consistency", row, evidence, evidence["documents"])
    left_value, right_value = ("95", "125") if changed else ("90", "120")
    candidate = {
        "rule_id": str(rule_id),
        "severity": "critical",
        "statement": "合成真实新矛盾",
        "left": assertion(str(body["evidence"][0]), f"工程A标段一工期{left_value}天", left_value),
        "right": assertion(
            str(body["evidence"][1]), f"工程A标段一工期{right_value}天", right_value
        ),
    }
    if case == "reversed_sides":
        candidate["left"], candidate["right"] = candidate["right"], candidate["left"]
    result = validate_output(
        "consistency",
        {"read_evidence_ids": list(prepared["blocks"]), "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    assert result["check_state"] == "complete" and len(result["issues"]) == 1
    publisher = content_handlers.publish
    if case == "legacy_changed_values":
        frozen = (
            Path(__file__).resolve().parents[1]
            / "docs/a1-backend-development-checks/frozen-inputs-05/apps/worker/content_handlers.py"
        )
        spec = importlib.util.spec_from_file_location("baseline05_publisher", frozen)
        assert spec and spec.loader
        old_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old_module)
        publisher = old_module.publish
    monkeypatch.setattr(engine, "publish", publisher)
    lease = engine.acquire(message)
    assert lease and engine.finish(
        message, lease, {"row": row, "evidence": evidence, "outcome": result}
    )
    with connection(True) as c:
        actual = c.execute("SELECT * FROM findings WHERE review_id=%s", (review,)).fetchone()
        assert actual is not None and actual["severity"] == "critical"
        before, _ = decision(c, root)
        assert before["id"] == decided and lifecycle(c, root) == "dismissed"
        assert set(actual["evidence_ids"]) == set(body["evidence"])
        if changed:
            assert body["version"] != old["body"]["version"]
            assert body["ref"]["digest"] != old["body"]["ref"]["digest"]
            assert set(actual["evidence_ids"]).isdisjoint(root["evidence_ids"])
        if new_decision_required:
            assert lifecycle(c, actual) == "open"
            if case != "legacy_changed_values":
                assert origin(c, actual)["id"] != root["id"]
            assert decision(c, actual)[0] is None
        else:
            assert lifecycle(c, actual) == "dismissed" and origin(c, actual)["id"] == root["id"]
        record = c.execute(
            "SELECT payload FROM audit_entries WHERE event_type='content_check.finding_recorded' AND payload->>'finding_id'=%s",
            (str(actual["id"]),),
        ).fetchone()["payload"]
        if new_decision_required:
            assert record.get("decision_carry_basis") is None
        else:
            assert record["decision_carry_basis"]["from_decision_id"] == str(decided)
            assert record["decision_carry_basis"]["from_root_id"] == str(root["id"])
            assert len(record["decision_carry_basis"]["assertions_digest"]) == 64
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s", (operation["id"],)
            ).fetchone()["n"]
            == 0
        )
    if case == "legacy_changed_values":
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,)).fetchone()
            newly_decided = append_decision(
                c,
                bid,
                actor,
                "finding",
                actual["id"],
                {
                    "decision": "dismiss",
                    "reason": "仅对新95/125两侧证据重新人工核验",
                    "evidence_ids": [str(i) for i in actual["evidence_ids"]],
                },
            )
            assert newly_decided["object_id"] == actual["id"]
            assert lifecycle(c, actual) == "dismissed"
            assert decision(c, root)[0]["id"] == decided
