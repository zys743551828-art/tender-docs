"""所选真实轮次比较的可信输入和恢复读取；不触发模型重跑。"""

from packages.domain.core_recheck_scope import evaluate
from packages.domain.finding_lifecycle import origin, visible_rows
from packages.domain.review_data import effective_at, source_closure
from packages.domain.runtime import ROOT, DomainError, clean, fetch, now, sha


def method_digest():
    return sha(
        {
            name: sha((ROOT / name).read_bytes())
            for name in [
                "packages/domain/comparisons.py",
                "packages/domain/review_comparison.py",
                "packages/domain/core_recheck_scope.py",
                "apps/worker/comparison_handlers.py",
            ]
        }
    )


def metadata(c, bid, review_id):
    row = fetch(
        c,
        "SELECT r.id,r.operation_id,i.manifest,i.manifest_digest,i.evidence_manifest_blob_id,o.state,rr.core_coverage FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id LEFT JOIN review_results rr ON rr.review_id=r.id WHERE r.id=%s AND r.bid_id=%s",
        (review_id, bid),
    )
    manifest = row["manifest"]
    closure, valid = [], False
    try:
        closure = source_closure(c, manifest["source_refs"])
        effective_at(closure, manifest["as_of"])
        effective_at(closure, now())
        valid = all(
            sha([s for s in closure if s["source_id"] == ref["id"]])
            == ref["lineage_manifest_digest"]
            for ref in manifest["source_refs"]
        )
    except DomainError:
        pass
    checks = []
    for item in c.execute(
        "SELECT * FROM matrix_checks WHERE review_id=%s ORDER BY rule_id", (review_id,)
    ).fetchall():
        rule = fetch(c, "SELECT provenance_status FROM rule_items WHERE id=%s", (item["rule_id"],))
        ended = rule["provenance_status"] == "superseded"
        scope = evaluate(
            c,
            manifest=manifest,
            rule_id=item["rule_id"],
            evidence_ids=item["evidence_ids"],
            operation_id=row["operation_id"],
        )
        checks.append(
            {
                "rule_id": str(item["rule_id"]),
                "state": item["state"],
                "verdict": item["verdict"],
                "obligation_ended": ended and item["verdict"] == "not_applicable",
                "range_eligible": scope["eligible"] or ended,
                "core_affected": bool(scope["diagnostic_ids"]),
            }
        )
    findings = []
    for item in visible_rows(c, review_id):
        root = origin(c, item)
        detector = c.execute(
            "SELECT 1 FROM audit_entries WHERE bid_id=%s AND event_type='content_check.finding_recorded' AND payload->>'finding_id'=%s LIMIT 1",
            (bid, str(root["id"])),
        ).fetchone()
        findings.append(
            {
                "id": str(item["id"]),
                "root_id": str(root["id"]),
                "rule_id": str(item["rule_id"]) if item["rule_id"] else None,
                "verdict": item["verdict"],
                "statement": item["statement"],
                "freshly_checked": bool(item["searched_ranges"]),
                "core_recheck_applicable": not bool(detector),
            }
        )
    return clean(
        {
            "review_id": str(review_id),
            "manifest": manifest,
            "manifest_digest": row["manifest_digest"],
            "evidence_manifest_blob_id": row["evidence_manifest_blob_id"],
            "state": row["state"],
            "core_coverage": row["core_coverage"] or "pending",
            "sources_valid": valid,
            "authority_digest": sha(closure),
            "checks": checks,
            "findings": findings,
        }
    )


def pair_metadata(c, bid, left, right):
    if str(left) == str(right):
        raise DomainError("COMPARISON_SAME_REVIEW", "请选择两个不同的实际评审轮次", 422)
    return {"left": metadata(c, bid, left), "right": metadata(c, bid, right)}


def request_row(c, comparison_id):
    return fetch(
        c,
        "SELECT a.bid_id,a.payload,o.state,o.id AS operation_id FROM audit_entries a JOIN operations o ON o.id=(a.payload->>'comparison_id')::uuid WHERE a.event_type='comparison.requested' AND a.payload->>'comparison_id'=%s",
        (str(comparison_id),),
    )


def resource(c, comparison_id):
    row = request_row(c, comparison_id)
    effect = c.execute(
        "SELECT result FROM effect_receipts WHERE operation_id=%s AND effect_key='compare-reviews'",
        (row["operation_id"],),
    ).fetchone()
    output = effect["result"] if effect else {}
    current = pair_metadata(
        c, row["bid_id"], row["payload"]["left_review_id"], row["payload"]["right_review_id"]
    )
    return clean(
        {
            "id": comparison_id,
            "bid_id": row["bid_id"],
            "operation_id": row["operation_id"],
            "state": row["state"],
            "left_review_id": row["payload"]["left_review_id"],
            "right_review_id": row["payload"]["right_review_id"],
            "result_blob_id": output.get("result_blob_id"),
            "result_digest": output.get("result_digest"),
            "error_code": output.get("error_code"),
            "comparison_method_digest": row["payload"]["comparison_method_digest"],
            "current_context_matches": sha(current) == row["payload"]["context_digest"]
            and method_digest() == row["payload"]["comparison_method_digest"],
        }
    )
