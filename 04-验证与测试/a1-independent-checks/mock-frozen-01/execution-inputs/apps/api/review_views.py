"""本轮冻结身份、覆盖和人工决定投影；空缺不填0或绿色通过。"""

from packages.domain.finding_lifecycle import (
    decision as finding_decision,
)
from packages.domain.finding_lifecycle import (
    lifecycle as finding_lifecycle,
)
from packages.domain.finding_lifecycle import (
    origin as finding_origin,
)
from packages.domain.finding_lifecycle import (
    visible_rows,
)
from packages.domain.review_data import effective_at, source_closure
from packages.domain.reviews import POLICY, method_profile
from packages.domain.rule_sets import current_decision, rules
from packages.domain.runtime import DomainError, clean, fetch, sha


def decision_view(row):
    return clean(
        {
            key: row[key]
            for key in (
                "id",
                "object_id",
                "object_revision",
                "decision",
                "reason",
                "actor_id",
                "created_at",
            )
        }
    )


def rule_view(c, rule_id):
    rule = fetch(c, "SELECT * FROM rule_items WHERE id=%s", (rule_id,))
    decision = current_decision(c, rule["bid_id"], "rule", rule_id)
    head = c.execute(
        "SELECT revision FROM decision_heads WHERE bid_id=%s AND object_kind='rule' AND object_id=%s",
        (rule["bid_id"], rule_id),
    ).fetchone()
    carry = c.execute(
        "SELECT from_rule_id,from_decision_id,equivalence_digest,adopted_at FROM rule_carryovers WHERE new_rule_id=%s",
        (rule_id,),
    ).fetchone()
    return clean(
        {
            "id": rule["id"],
            "rule_set_id": rule["rule_set_id"],
            "kind": rule["kind"],
            "statement": rule["statement"],
            "conditions": rule["conditions"],
            "exceptions": rule["exceptions"],
            "maximum": str(rule["max_points"]) if rule["max_points"] is not None else None,
            "evidence_ids": rule["evidence_ids"],
            "confirmation": decision["decision"] if decision else "pending",
            "decision_revision": head["revision"] if head else 1,
            "carryover": carry,
            "scope": rule["applicability_scope"],
            "bands": rule["bands"],
            "origin_kind": rule["origin_kind"],
            "manual_origin_rule_id": rule["manual_origin_rule_id"],
            "provenance_status": rule["provenance_status"],
        }
    )


def rule_set_view(c, rule_set):
    row = fetch(
        c,
        "SELECT r.*,m.revision AS material_revision FROM rule_sets r JOIN material_snapshots m ON m.id=r.material_id WHERE r.id=%s",
        (rule_set,),
    )
    return clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            "material_revision": row["material_revision"],
            "coverage": row["coverage"],
            "rules": [rule_view(c, r["id"]) for r in rules(c, rule_set)],
            "revision": row["revision"],
        }
    )


def fact_view(c, fact_id):
    row = fetch(c, "SELECT * FROM fact_items WHERE id=%s", (fact_id,))
    decision = current_decision(c, row["bid_id"], "fact", fact_id)
    head = c.execute(
        "SELECT revision FROM decision_heads WHERE bid_id=%s AND object_kind='fact' AND object_id=%s",
        (row["bid_id"], fact_id),
    ).fetchone()
    return clean(
        {
            key: row[key]
            for key in ("id", "subject", "scope", "name", "value", "unit", "evidence_ids")
        }
        | {
            "confirmation": decision["decision"] if decision else "pending",
            "decision_revision": head["revision"] if head else 1,
        }
    )


def finding_view(c, finding_id):
    row = fetch(c, "SELECT * FROM findings WHERE id=%s", (finding_id,))
    decision, revision = finding_decision(c, row)
    lifecycle = finding_lifecycle(c, row)
    origin = finding_origin(c, row)
    return clean(
        {
            key: row[key]
            for key in (
                "id",
                "review_id",
                "rule_id",
                "category",
                "severity",
                "verdict",
                "statement",
                "evidence_ids",
                "searched_ranges",
            )
        }
        | {
            "decision": decision_view(decision) if decision else None,
            "decision_revision": revision,
            "origin_review_id": origin["review_id"],
            "retained_known_critical": row["carried_from_id"] is not None
            and row["severity"] == "critical",
            "detection_run_state": "ran" if row["searched_ranges"] else "not_run_this_round",
            "lifecycle": lifecycle,
        }
    )


def _pending_layers(c, row):
    manifest = row["manifest"]
    rule_items = rules(c, manifest["rule_set_id"])
    core = [r for r in rule_items if r["kind"] in ("mandatory", "disqualification")]
    parses = c.execute(
        "SELECT * FROM parse_runs WHERE id=ANY(%s::uuid[])", (manifest["parse_run_ids"],)
    ).fetchall()
    gaps = list(dict.fromkeys(g for parse in parses for g in parse["gaps"]))
    checks = c.execute("SELECT * FROM matrix_checks WHERE review_id=%s", (row["id"],)).fetchall()
    done = sum(
        check["state"] == "succeeded"
        for check in checks
        if check["rule_id"] in {rule["id"] for rule in core}
    )
    extraction = fetch(c, "SELECT coverage FROM rule_sets WHERE id=%s", (manifest["rule_set_id"],))[
        "coverage"
    ]

    def layer(state, planned, completed, basis, missing):
        return {
            "state": state,
            "planned": planned,
            "completed": completed,
            "denominator_basis": basis,
            "gaps": missing,
        }

    return {
        "source_scope": layer(
            "partial" if gaps else "complete",
            sum(p["page_count"] for p in parses),
            sum(p["page_count"] for p in parses if p["coverage"] == "complete"),
            "本轮生效来源全部物理页的已知读取范围",
            gaps,
        ),
        "core_extraction": layer(
            "complete" if extraction == "complete" else "partial",
            len(core),
            len(core),
            "已识别核心要求；不等于真实漏提率",
            [] if extraction == "complete" else ["要求提取范围仍有已知缺口或待核验关系"],
        ),
        "judgment": layer(
            "failed"
            if row["state"] == "failed"
            else "complete"
            if done == len(core) and row["state"] == "succeeded"
            else "pending",
            len(core),
            done,
            "已识别核心条款核对执行",
            ["核心核对尚未完整结束"] if done < len(core) else [],
        ),
        "scoring": layer(
            "not_run",
            sum(r["kind"] == "scoring" for r in rule_items),
            0,
            "评分独立分母",
            ["正式评分尚未具备完整章节三次判断与模型准入"],
        ),
        "extraction_quality": "not_measured",
        "quality_report_id": None,
        "human_confirmation_pending": sum(
            rule_view(c, r["id"])["confirmation"] != "confirmed" for r in rule_items
        ),
    }


def review_row(c, review_id):
    return fetch(
        c,
        "SELECT r.*,i.manifest,i.input_digest,i.manifest_digest,i.method_digest,o.state,o.finished_at FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE r.id=%s",
        (review_id,),
    )


def review_view(c, review_id):
    row = review_row(c, review_id)
    profile = fetch(
        c,
        "SELECT p.*,b.settings FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
        (row["bid_id"],),
    )
    result = c.execute(
        "SELECT * FROM review_results WHERE review_id=%s ORDER BY created_at DESC LIMIT 1",
        (review_id,),
    ).fetchone()
    layers = result["coverage_layers"] if result else _pending_layers(c, row)
    layers = dict(layers)
    layers["human_confirmation_pending"] = sum(
        rule_view(c, r["id"])["confirmation"] != "confirmed"
        for r in rules(c, row["manifest"]["rule_set_id"])
    )
    coverage = (
        result["core_coverage"]
        if result
        else "failed"
        if row["state"] in ("failed", "cancelled")
        else "pending"
    )
    count = sum(
        finding_lifecycle(c, finding) not in ("dismissed", "resolved_by_recheck")
        for finding in visible_rows(c, review_id)
    )
    current = (
        str(profile["current_review_id"]) == str(review_id)
        and str(profile["current_body_id"]) == row["manifest"]["body_version_id"]
        and str(profile["current_material_id"]) == row["manifest"]["material_snapshot_id"]
        and str(profile["current_rule_set_id"]) == row["manifest"]["rule_set_id"]
        and row["method_digest"] == sha(method_profile())
        and row["manifest"]["policy_digest"] == sha(POLICY)
        and row["manifest"]["content_checks"]
        == {
            key: bool(profile["settings"].get(key, False))
            for key in ("consistency", "scope_residue")
        }
    )
    fact_head = c.execute(
        "SELECT fact_set_id FROM fact_heads WHERE bid_id=%s", (row["bid_id"],)
    ).fetchone()
    current = (
        current
        and (str(fact_head["fact_set_id"]) if fact_head and fact_head["fact_set_id"] else None)
        == row["manifest"]["fact_set_id"]
    )
    impact = 1
    try:
        closure = source_closure(c, row["manifest"]["source_refs"])
        impact = max(1, sum({source["id"]: source["revision"] or 0 for source in closure}.values()))
        for ref in row["manifest"]["source_refs"]:
            if (
                sha([source for source in closure if source["source_id"] == ref["id"]])
                != ref["lineage_manifest_digest"]
            ):
                current = False
        effective_at(closure, row["manifest"]["as_of"])
    except DomainError:
        current = False
    next_action = {
        "action": "inspect_findings",
        "reason": "查看已核对结果及未覆盖范围",
        "target_url": f"/bids/{row['bid_id']}/review",
        "blocked": coverage != "complete",
    }
    if not current:
        next_action.update(
            action="review_current_input",
            reason="本轮为历史输入，请核对当前资料后重新评审",
            target_url=f"/bids/{row['bid_id']}/materials",
            blocked=True,
        )
    elif coverage == "pending":
        next_action.update(
            action="wait_review",
            reason="本轮正在核对，可以离开后回来查看",
            target_url=f"/bids/{row['bid_id']}/review",
            blocked=False,
        )
    return clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            "operation_id": row["operation_id"],
            "input_digest": row["input_digest"],
            "core_coverage": coverage,
            "risk_count": count if result or count else None,
            "current": current,
            "prediction": {
                "review_id": row["id"],
                "mode": "unavailable"
                if profile["settings"].get("show_prediction", True)
                else "hidden",
                "maximum": None,
                "reasons": ["正式评分尚未完成独立模型准入；当前核心核对不产生预测分"],
                "method_digest": row["method_digest"],
            },
            "next_action": next_action,
            "source_impact_revision": impact,
            "manifest_digest": row["manifest_digest"],
            "coverage_layers": layers,
        }
    )


def matrix_view(c, review_id):
    review = review_view(c, review_id)
    manifest = review_row(c, review_id)["manifest"]
    checks = {
        str(row["rule_id"]): row
        for row in c.execute(
            "SELECT * FROM matrix_checks WHERE review_id=%s", (review_id,)
        ).fetchall()
    }
    findings = visible_rows(c, review_id)
    rows = []
    for rule in rules(c, manifest["rule_set_id"]):
        check = checks.get(str(rule["id"]))
        rows.append(
            {
                "rule": rule_view(c, rule["id"]),
                "verdict": check["verdict"] if check else None,
                "finding_ids": [str(f["id"]) for f in findings if f["rule_id"] == rule["id"]],
                "evidence_ids": clean(check["evidence_ids"]) if check else [],
                "check_status": check["state"] if check else "pending",
                "error_code": check["error_code"] if check else None,
            }
        )
    return {
        "review_id": str(review_id),
        "coverage": review["core_coverage"],
        "rows": rows,
        "coverage_layers": review["coverage_layers"],
    }


def review_state(c, bid_id):
    row = fetch(
        c,
        "SELECT b.material_revision,p.current_body_id,p.current_material_id,p.current_rule_set_id,p.current_review_id,s.revision,s.pending_intent FROM bids b JOIN bid_profiles p ON p.bid_id=b.id JOIN review_slots s ON s.bid_id=b.id WHERE b.id=%s",
        (bid_id,),
    )
    intent = row["pending_intent"]
    request = None
    if intent:
        request = {
            key: intent.get(key)
            for key in (
                "request_id",
                "trigger",
                "body_version_id",
                "material_snapshot_id",
                "material_revision",
                "state",
                "prepare_operation_id",
                "review_id",
                "error_code",
                "gaps",
                "updated_at",
            )
        }
    return clean(
        {
            "bid_id": bid_id,
            "revision": row["revision"],
            "current_body_id": row["current_body_id"],
            "material_revision": row["material_revision"],
            "current_material_snapshot_id": row["current_material_id"],
            "current_rule_set_id": row["current_rule_set_id"],
            "request": request,
            "current_review": review_view(c, row["current_review_id"])
            if row["current_review_id"]
            else None,
        }
    )
