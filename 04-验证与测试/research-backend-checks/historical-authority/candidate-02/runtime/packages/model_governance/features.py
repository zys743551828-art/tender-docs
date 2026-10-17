"""Versioned clean features from complete packages and exactly three valid judgments."""

from collections import Counter
from decimal import Decimal

from packages.model_governance.common import GovernanceError, decimal, digest

SCHEMA = "complete-three-band-features/1"
FEATURES = ("raw_percent", "modal_share", "spread_percent", "evidence_share")


def build(packages, results, *, technical_maximum, maximum_evidence_verified):
    reasons = []
    if maximum_evidence_verified is not True:
        reasons.append("technical_maximum_not_verified")
    try:
        maximum = decimal(technical_maximum)
        if maximum <= 0:
            raise GovernanceError("MAXIMUM_UNKNOWN", "Positive verified maximum required")
        if not packages or len(packages) != len(results):
            raise GovernanceError("INCOMPLETE_SCOPE", "Every necessary rule requires a result")
        by_digest = {item["package_digest"]: item for item in results}
        if len(by_digest) != len(results):
            raise GovernanceError("DUPLICATE_RULE_RESULT", "Duplicate package result")
        seen_rules, seen_calls = set(), set()
        total = raw = modal = spread = cited = Decimal(0)
        detail = []
        for package in packages:
            if set(package) != {
                "purpose",
                "rule",
                "body_version_id",
                "parse_id",
                "sections",
                "method_digest",
                "digest",
            } or set(package["rule"]) != {
                "id",
                "statement",
                "conditions",
                "exceptions",
                "bands",
                "maximum",
            }:
                raise GovernanceError(
                    "INPUT_NOT_CLEAN", "Unexpected input field cannot enter feature computation"
                )
            frozen = {k: v for k, v in package.items() if k != "digest"}
            if package["digest"] != digest(frozen) or package["purpose"] != "formal_score":
                raise GovernanceError("PACKAGE_CORRUPTED", "Frozen clean package digest mismatch")
            rule = package["rule"]
            if rule["id"] in seen_rules:
                raise GovernanceError("DUPLICATE_RULE", "Rule counted twice")
            seen_rules.add(rule["id"])
            cap = decimal(rule["maximum"])
            if cap <= 0:
                raise GovernanceError("RULE_SCALE_UNKNOWN", "Rule maximum unavailable")
            bands = {band["id"]: decimal(band["points"]) for band in rule["bands"]}
            if (
                len(bands) != len(rule["bands"])
                or not bands
                or any(p > cap for p in bands.values())
            ):
                raise GovernanceError("BAND_SCALE_UNKNOWN", "Unambiguous bounded points required")
            if not package["sections"] or any(
                not s["pages"] or not s["blocks"] for s in package["sections"]
            ):
                raise GovernanceError("INCOMPLETE_SCOPE", "Complete nonempty chapters required")
            allowed = {
                str(b["evidence_id"]): b["text"] for s in package["sections"] for b in s["blocks"]
            }
            for section in package["sections"]:
                for block in section["blocks"]:
                    import hashlib

                    if (
                        block["physical_page"] not in section["pages"]
                        or hashlib.sha256(block["text"].encode()).hexdigest() != block["digest"]
                    ):
                        raise GovernanceError(
                            "CHAPTER_CORRUPTED", "Complete original evidence mismatch"
                        )
            result = by_digest[package["digest"]]
            judgments = result["judgments"]
            if (
                result["valid_replicates"] != 3
                or len(judgments) != 3
                or {j["replicate"] for j in judgments} != {1, 2, 3}
                or any(type(j["replicate"]) is not int for j in judgments)
            ):
                raise GovernanceError(
                    "THREE_VALID_REQUIRED", "Exactly three independent valid judgments required"
                )
            values, identifiers, evidence_count = [], [], 0
            for judgment in judgments:
                call = judgment["call_id"]
                payload = judgment["payload"]
                if not call or call in seen_calls:
                    raise GovernanceError("CALL_REUSED", "Independent call identity required")
                seen_calls.add(call)
                if (
                    set(payload) != {"band_id", "reason", "evidence", "coverage", "status"}
                    or payload["status"] != "valid"
                    or payload["coverage"] != "complete"
                    or not payload["reason"]
                    or payload["band_id"] not in bands
                ):
                    raise GovernanceError(
                        "THREE_VALID_REQUIRED", "Invalid, unread or unknown band cannot become zero"
                    )
                for evidence in payload["evidence"]:
                    if (
                        set(evidence) != {"evidence_id", "quote"}
                        or not evidence["quote"]
                        or evidence["quote"] not in allowed.get(str(evidence["evidence_id"]), "")
                    ):
                        raise GovernanceError("INVALID_EVIDENCE", "Quote outside frozen full input")
                values.append(bands[payload["band_id"]])
                identifiers.append(payload["band_id"])
                evidence_count += bool(payload["evidence"])
            mean = sum(values) / Decimal(3)
            total += cap
            raw += mean
            modal += cap * Decimal(max(Counter(identifiers).values())) / Decimal(3)
            spread += max(values) - min(values)
            cited += cap * Decimal(evidence_count) / Decimal(3)
            detail.append(
                {
                    "rule_id": rule["id"],
                    "maximum": str(cap),
                    "raw_points": str(mean),
                    "band_counts": dict(Counter(identifiers)),
                    "call_ids": [j["call_id"] for j in judgments],
                }
            )
        if total != maximum:
            raise GovernanceError(
                "SCALE_MISMATCH", "All necessary rule maxima must equal verified total"
            )
        if reasons:
            return {"schema": SCHEMA, "status": "unavailable", "values": None, "reasons": reasons}
        values = [
            float(raw * 100 / total),
            float(modal / total),
            float(spread * 100 / total),
            float(cited / total),
        ]
        return {
            "schema": SCHEMA,
            "status": "complete",
            "feature_names": list(FEATURES),
            "values": values,
            "technical_maximum": str(maximum),
            "rule_details": detail,
            "reasons": [],
            "input_digest": digest(
                {"packages": [p["digest"] for p in packages], "results": results}
            ),
        }
    except (GovernanceError, KeyError, TypeError) as error:
        reasons.append(
            error.code if isinstance(error, GovernanceError) else "MALFORMED_COMPLETE_INPUT"
        )
        return {"schema": SCHEMA, "status": "unavailable", "values": None, "reasons": reasons}
