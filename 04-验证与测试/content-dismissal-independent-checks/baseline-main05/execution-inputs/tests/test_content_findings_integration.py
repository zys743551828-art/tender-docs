"""独立检测发布的真实非ownerPG/fence机制；显式页与模型结果夹具，0收费。"""

import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_content_checks import assertion
from test_core_recheck_scope_integration import add_document

from apps.worker import content_handlers, engine
from packages.domain.content_checks import prepare, validate_output
from packages.domain.finding_lifecycle import lifecycle
from packages.domain.review_data import read_bundle, source_closure
from packages.domain.rule_sets import rules
from packages.domain.runtime import clean, connection, enqueue_plan, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONTENT_FINDINGS_INTEGRATION") != "1", reason="需要专用真实PG"
)


@pytest.mark.parametrize("invalid", [False, True])
def test_verified_conflict_or_engineering_diagnostic_publishes_once_under_fence(
    monkeypatch, invalid
):
    with connection() as c:
        actor, bid, material, rule_set, rule_id, review, input_id, command = [
            uuid4() for _ in range(8)
        ]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成独立检测发布')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成独立检测发布','{}',%s)",
            (bid, actor),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        body = add_document(
            c, bid, actor, [("usable", "工程A标段一工期90天"), ("usable", "工程A标段一工期120天")]
        )
        tender = add_document(c, bid, actor, [("usable", "工程A标段一工期必须一致且等于90天")])
        for role, doc in [("body", body), ("tender", tender)]:
            c.execute(
                "INSERT INTO material_assignments(bid_id,snapshot_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,0)",
                (bid, material, doc["version"], role),
            )
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha(str(rule_set))),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'mandatory','本工程工期必须一致','[]','[]',%s,'[]')",
            (rule_id, bid, rule_set, tender["evidence"]),
        )
        refs = [body["ref"], tender["ref"] | {"kind": "tender"}]
        manifest = clean(
            {
                "bid_id": bid,
                "body_version_id": body["version"],
                "rule_set_id": rule_set,
                "source_refs": refs,
                "as_of": c.execute("SELECT clock_timestamp() AS at").fetchone()["at"],
                "parse_run_ids": [body["parse"], tender["parse"]],
                "content_checks": {"consistency": True, "scope_residue": False},
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
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'content_findings_probe',%s,%s,%s)",
            (command, actor, bid, uuid4().hex, sha(str(command))),
        )
        # document处理器只用于隔离fence机制；发布函数仍是实际content_handlers.publish，无权限放宽。
        operation = enqueue_plan(
            c,
            bid,
            command,
            "content-findings-probe",
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
                sha("synthetic"),
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
    candidate = {
        "rule_id": str(rule_id),
        "severity": "critical",
        "statement": "不可信模型文字9999吨",
        "left": assertion(str(body["evidence"][0]), "工程A标段一工期90天", "90"),
        "right": assertion(str(body["evidence"][1]), "工程A标段一工期120天", "120"),
    }
    if invalid:
        candidate["left"]["quote"] = "原件没有的错误文字90天"
    result = validate_output(
        "consistency",
        {"read_evidence_ids": list(prepared["blocks"]), "issues": [candidate], "gaps": []},
        prepared,
        evidence,
    )
    monkeypatch.setattr(engine, "publish", content_handlers.publish)
    lease = engine.acquire(message)
    outcome = {"row": row, "evidence": evidence, "outcome": result}
    assert lease and engine.finish(message, lease, outcome)
    assert engine.finish(message, lease, outcome) is False and engine.acquire(message) is None
    with connection(True) as c:
        findings = c.execute("SELECT * FROM findings WHERE review_id=%s", (review,)).fetchall()
        diagnostics = c.execute(
            "SELECT * FROM validation_diagnostics WHERE operation_id=%s", (operation["id"],)
        ).fetchall()
        assert len(findings) == (0 if invalid else 1)
        assert len(diagnostics) == (1 if invalid else 0)
        if not invalid:
            assert len(findings[0]["evidence_ids"]) == 2 and "9999" not in findings[0]["statement"]
            assert lifecycle(c, findings[0]) == "open"
        else:
            effect = c.execute(
                "SELECT result FROM effect_receipts WHERE operation_id=%s", (operation["id"],)
            ).fetchone()["result"]
            assert effect["check_state"] == "partial" and effect["core_affected_rule_ids"] == [
                str(rule_id)
            ]
        from jsonschema import Draft202012Validator

        from apps.api.main import app
        from packages.domain.content_check_status import view

        status = view(c, review)
        assert len(status["checks"][0]["diagnostics"]) == (1 if invalid else 0)
        actual = app.openapi()
        Draft202012Validator(
            {
                "$ref": "#/components/schemas/ContentCheckStatusView",
                "components": actual["components"],
            }
        ).validate(status)
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s", (operation["id"],)
            ).fetchone()["n"]
            == 0
        )
