"""Sourced denominator enrichment of immutable, complete score judgments.

No model/network/config port exists here. The API must obtain basis from the SQL
authority, and verify its scope before persisting this separate derived artifact.
"""

from decimal import Decimal

from packages.model_governance import features
from packages.model_governance.common import GovernanceError, decimal


def derive(*, binding, packages, result, source_result_digest, method_digest):
    if (
        result.get("schema") != "complete-score-judgments/1"
        or result.get("method_digest") != method_digest
        or result.get("execution_purpose") != "score"
        or not packages
        or any(package.get("method_digest") != method_digest for package in packages)
    ):
        raise GovernanceError("DENOMINATOR_RESULT_MISMATCH", "Exact frozen scoring method required")
    expected = {rule["id"] for rule in binding["scope"]["rules"]}
    if (
        len(expected) != len(packages)
        or {p["rule"]["id"] for p in packages} != expected
        or len(result["items"]) != len(packages)
    ):
        raise GovernanceError("DENOMINATOR_RULE_SCOPE", "No necessary scoring rule may be omitted")
    authoritative = {rule["id"]: decimal(rule["maximum"]) for rule in binding["scope"]["rules"]}
    if any(decimal(p["rule"]["maximum"]) != authoritative[p["rule"]["id"]] for p in packages):
        raise GovernanceError("DENOMINATOR_RULE_SCALE", "Rule points changed after scope freeze")
    maximum = None
    reasons = []
    if binding.get("current") is not True or binding.get("recorded_status") != "usable":
        reasons.append("technical_maximum_authority_unavailable")
    elif binding.get("scale_description") != "未加权原始分":
        reasons.append("unsupported_rule_scale")
    else:
        maximum = decimal(binding.get("recorded_maximum"))
        if maximum <= 0:
            raise GovernanceError("DENOMINATOR_UNKNOWN", "Positive sourced total required")
    rule_total = sum(authoritative.values(), Decimal(0))
    if maximum is not None and rule_total > maximum:
        maximum = None
        reasons.append("rule_maximum_sum_exceeds_sourced_total")
    by_digest = {item["package_digest"]: item for item in result["items"]}
    if len(by_digest) != len(result["items"]):
        raise GovernanceError("DENOMINATOR_DUPLICATE_RESULT", "One result per complete chapter")
    calls = [j["call_id"] for item in result["items"] for j in item["judgments"]]
    if len(calls) != len(set(calls)):
        raise GovernanceError("DENOMINATOR_CALL_REUSE", "Independent calls cannot count twice")
    valid_count = 0
    covered = Decimal(0)
    for package in packages:
        item = by_digest.get(package["digest"])
        if item is None:
            raise GovernanceError("DENOMINATOR_RESULT_MISMATCH", "Frozen package result missing")
        checked = features.build(
            [package],
            [item],
            technical_maximum=package["rule"]["maximum"],
            maximum_evidence_verified=True,
        )
        if checked["status"] == "complete":
            valid_count += 1
            covered += authoritative[package["rule"]["id"]]
    vector = features.build(
        packages,
        result["items"],
        technical_maximum=str(maximum) if maximum is not None else None,
        maximum_evidence_verified=maximum is not None,
    )
    if vector["status"] == "complete":
        vector["technical_maximum"] = format(decimal(vector["technical_maximum"]), "f")
        for detail in vector["rule_details"]:
            for field in ("maximum", "raw_points"):
                detail[field] = format(decimal(detail[field]), "f")
    return {
        "schema": "sourced-score-coverage/1",
        "binding_id": binding["id"],
        "source_result_digest": source_result_digest,
        "method_digest": method_digest,
        "technical_maximum": format(maximum, "f") if maximum is not None else None,
        "scale_description": binding.get("scale_description"),
        "coverage": {
            "valid_rules": valid_count,
            "necessary_rules": len(packages),
            "covered_rule_maximum": format(covered, "f"),
            "necessary_rule_maximum": format(rule_total, "f"),
            "technical_maximum": format(maximum, "f") if maximum is not None else None,
            "fraction": format(covered / maximum, "f") if maximum is not None else None,
            "status": "complete"
            if maximum is not None and covered == maximum and valid_count == len(packages)
            else "partial"
            if maximum is not None
            else "unavailable",
        },
        "items": result["items"],
        "features": vector,
        "prediction": {"mode": "unavailable", "value": None, "intervals": None},
        "formal_eligibility": "unavailable",
        "reasons": reasons,
    }
