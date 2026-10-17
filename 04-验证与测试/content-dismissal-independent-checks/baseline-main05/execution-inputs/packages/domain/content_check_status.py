"""可选检测独立覆盖，未知次数保持null，不计入核心/评分分母。"""

from packages.domain.content_checks import KINDS
from packages.domain.runtime import clean, fetch


def view(c, review_id):
    row = fetch(
        c,
        "SELECT r.operation_id,i.manifest,o.state FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE r.id=%s",
        (review_id,),
    )
    items = []
    for kind, step in KINDS.items():
        enabled = bool(row["manifest"]["content_checks"].get(kind, False))
        plan = c.execute(
            "SELECT s.state,s.output FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s AND p.step_key=%s",
            (row["operation_id"], step),
        ).fetchone()
        output = (plan or {}).get("output") or {}
        state, gaps = "not_run", []
        if enabled and plan:
            actual = plan["state"]
            state = (
                output.get("check_state", "partial")
                if actual == "succeeded"
                else actual
                or (
                    "cancelled"
                    if row["state"] in ("cancelled", "cancel_requested", "failed")
                    else "queued"
                )
            )
            gaps = output.get("gaps", [])
            if actual == "failed":
                gaps = ["该项执行失败；已知关键问题仍保留，请查看失败原因及原页"]
        elif enabled:
            gaps = ["本轮冻结方法未登记该项检测，未运行不代表满足"]
        diagnostics = c.execute(
            "SELECT id,code,field_path,evidence_ids,action,message FROM validation_diagnostics WHERE operation_id=%s AND step_key=%s ORDER BY id",
            (row["operation_id"], step),
        ).fetchall()
        items.append(
            {
                "kind": kind,
                "enabled": enabled,
                "state": state,
                "planned_evidence_count": output.get("planned_evidence_count"),
                "read_evidence_count": output.get("read_evidence_count"),
                "new_finding_count": len(output["issues"]) if "issues" in output else None,
                "gaps": gaps,
                "diagnostic_count": len(diagnostics),
                "diagnostics": diagnostics,
                "core_affected_rule_ids": output.get("core_affected_rule_ids", []),
                "error_code": output.get("error_code"),
            }
        )
    return clean(
        {
            "review_id": review_id,
            "operation_id": row["operation_id"],
            "checks": items,
            "scope_note": "独立可选检测范围，不计入核心规则或评分分母；无新发现/未运行不解决旧关键问题",
        }
    )
