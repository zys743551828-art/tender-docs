"""Manual, sourced format fields override templates; unknown semantics stay unknown."""

from copy import deepcopy

from packages.authoring.content import empty_content
from packages.authoring.repository import assert_sources
from packages.domain.fact_sets import decision_basis, evidence_basis
from packages.domain.rule_sets import current_decision, semantic_key
from packages.domain.runtime import DomainError, clean, fetch, sha


def project_rules(c, bid):
    return c.execute(
        "SELECT r.* FROM rule_items r JOIN bid_profiles p ON p.bid_id=r.bid_id AND p.current_rule_set_id=r.rule_set_id WHERE r.bid_id=%s AND r.provenance_status='effective' ORDER BY r.id",
        (bid,),
    ).fetchall()


def confirmed_basis(c, bid, rule):
    decision = current_decision(c, bid, "rule", rule["id"])
    if not decision or decision["decision"] != "confirmed":
        raise DomainError(
            "AUTHORING_FORMAT_RULE_UNCONFIRMED", "格式绑定需要当前有据确认的有效要求", 409
        )
    basis = evidence_basis(c, bid, rule["evidence_ids"], publish=False)
    if basis != decision_basis(c, decision["id"]):
        raise DomainError(
            "AUTHORING_FORMAT_RULE_STALE", "格式要求的确认依据已变化，请重新核对要求", 409
        )
    cursor = rule
    seen = set()
    while cursor["id"] != decision["object_id"]:
        if cursor["id"] in seen:
            raise DomainError("AUTHORING_FORMAT_RULE_STALE", "格式要求沿用关系不明确", 409)
        seen.add(cursor["id"])
        mapping = c.execute(
            "SELECT * FROM rule_carryovers WHERE bid_id=%s AND new_rule_id=%s", (bid, cursor["id"])
        ).fetchone()
        if (
            mapping is None
            or mapping["from_decision_id"] != decision["id"]
            or mapping["equivalence_digest"] != semantic_key(rule)
        ):
            raise DomainError("AUTHORING_FORMAT_RULE_STALE", "格式要求沿用基准不一致", 409)
        cursor = fetch(
            c, "SELECT * FROM rule_items WHERE bid_id=%s AND id=%s", (bid, mapping["from_rule_id"])
        )
        if semantic_key(cursor) != semantic_key(rule):
            raise DomainError("AUTHORING_FORMAT_RULE_STALE", "格式要求与原确认语义不一致", 409)
    return clean(
        {
            "rule_id": rule["id"],
            "rule_semantic_digest": semantic_key(rule),
            "decision_id": decision["id"],
            "decision_revision": decision["decision_revision"],
            "evidence_basis": basis,
        }
    )


def evaluate(c, bid, style, bindings):
    rules = project_rules(c, bid)
    by_id = {str(r["id"]): r for r in rules}
    effective = deepcopy(style)
    manifest = []
    conflicts = []
    fields = set()
    for binding in bindings:
        rule = by_id.get(binding["rule_id"])
        if rule is None or binding["field"] in fields:
            raise DomainError(
                "AUTHORING_FORMAT_BINDING_INVALID", "格式字段不能重复，要求必须属于当前有效包", 422
            )
        fields.add(binding["field"])
        basis = confirmed_basis(c, bid, rule)
        refs = binding["source_refs"]
        if not refs or not {ref["evidence_id"] for ref in refs} <= set(
            map(str, rule["evidence_ids"])
        ):
            raise DomainError(
                "AUTHORING_FORMAT_BINDING_INVALID", "格式绑定须提供该要求的真实原页依据", 422
            )
        checked = empty_content("格式依据核验", 0)
        checked["attrs"]["source_refs"] = refs
        assert_sources(c, bid, checked)
        value = clean(
            binding
            | {
                "basis": basis,
                "interpretation": "human_bound_field_semantics_not_automatically_verified",
            }
        )
        manifest.append(value)
        if effective[binding["field"]] != binding["value"]:
            conflicts.append(
                {
                    "field": binding["field"],
                    "template_value": effective[binding["field"]],
                    "project_value": binding["value"],
                    "rule_id": binding["rule_id"],
                    "resolution": "project_requirement_wins",
                }
            )
        effective[binding["field"]] = binding["value"]
    report = clean(
        {
            "coverage": "manual_field_bindings_only",
            "binding_manifest": manifest,
            "conflicts": conflicts,
            "effective_config": effective,
            "unjudged_rule_ids": list(by_id),
            "rule_context_digest": sha(clean(rules)),
            "requires_ack": bool(rules),
            "notice": "仅按人工有据绑定的字段优先应用；其他格式与暗标语义未自动核完，不代表要求或版面已通过",
        }
    )
    report["review_digest"] = sha(report)
    return report


def assert_snapshot(c, bid, extension):
    metadata = extension["metadata"]
    report = evaluate(
        c, bid, extension["style_template"]["config"], metadata.get("format_bindings", [])
    )
    if (
        metadata.get("format_binding_manifest", []) != report["binding_manifest"]
        or metadata.get("effective_style", extension["style_template"]["config"])
        != report["effective_config"]
    ):
        raise DomainError(
            "AUTHORING_FORMAT_BINDING_STALE", "格式绑定或确认依据变更，未继续生成", 409
        )
    if report["requires_ack"] and metadata.get("format_review_ack") != report["review_digest"]:
        raise DomainError(
            "AUTHORING_FORMAT_REVIEW_REQUIRED", "请先核对当前要求与版式的未判明范围再生成", 409
        )
    return report
