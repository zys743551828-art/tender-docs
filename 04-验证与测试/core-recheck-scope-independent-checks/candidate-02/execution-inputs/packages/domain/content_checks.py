"""独立可选检测：完整范围、双侧证据和工程断言；不混入评分覆盖。"""

import json

from packages.domain.engineering import claim, conflict, rejected
from packages.domain.runtime import DomainError, clean, sha

KINDS = {"consistency": "consistency-check", "scope_residue": "scope-residue-check"}


def output_schema():
    assertion = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "evidence_id",
            "quote",
            "subject",
            "attribute",
            "scope",
            "value",
            "unit",
            "relation",
        ],
        "properties": {
            **{
                name: {"type": "string", "minLength": 1, "maxLength": 200}
                for name in ("evidence_id", "subject", "attribute", "scope", "value")
            },
            "quote": {"type": "string", "minLength": 1, "maxLength": 20000},
            "unit": {"type": ["string", "null"], "maxLength": 30},
            "relation": {"enum": ["equal", "min", "max"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["read_evidence_ids", "issues", "gaps"],
        "properties": {
            "read_evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "gaps": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "issues": {
                "type": "array",
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rule_id", "severity", "statement", "left", "right"],
                    "properties": {
                        "rule_id": {"type": ["string", "null"]},
                        "severity": {"enum": ["critical", "major", "minor", "info"]},
                        "statement": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "left": assertion,
                        "right": assertion,
                    },
                },
            },
        },
    }


def prepare(kind, row, evidence, documents):
    body = next(
        (
            doc
            for doc in documents
            if str(doc["document_version_id"]) == row["manifest"]["body_version_id"]
        ),
        None,
    )
    if body is None:
        raise DomainError("DETECTOR_BODY_MISSING", "冻结正文不在该检测范围内", 409)
    sources = [doc for doc in documents if doc["role"] != "body"] if kind == "scope_residue" else []
    blocks = {str(block["id"]): block for doc in [body] + sources for block in doc["blocks"]}
    body_ids = {str(block["id"]) for block in body["blocks"]}
    source_ids = set(blocks) - body_ids
    payload = clean(
        {
            "check_kind": kind,
            "full_body_pages": body["pages"],
            "full_body_blocks": body["blocks"],
            "required_documents": [
                {
                    "document_version_id": doc["document_version_id"],
                    "role": doc["role"],
                    "pages": doc["pages"],
                    "blocks": doc["blocks"],
                    "coverage": doc["coverage"],
                    "gaps": doc["gaps"],
                }
                for doc in sources
            ],
            "effective_rules": [
                rule for rule in evidence["rules"] if rule["provenance_status"] == "effective"
            ],
            "confirmed_facts": evidence["confirmed_facts"],
            "known_gaps": list(
                dict.fromkeys(gap for doc in [body] + sources for gap in doc["gaps"])
            ),
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > 240000:
        raise DomainError(
            "DETECTOR_INPUT_RANGE_LIMIT",
            "完整相关范围超过当前检测上限；未以片段或截断冒充完整检测",
            413,
        )
    return {
        "payload": payload,
        "encoded": encoded,
        "blocks": blocks,
        "body_ids": body_ids,
        "source_ids": source_ids,
        "documents": [body] + sources,
    }


def validate_output(kind, data, prepared, evidence):
    diagnostics, accepted, affected = [], [], set()
    gaps = list(prepared["payload"]["known_gaps"]) + data["gaps"]
    if set(data["read_evidence_ids"]) != set(prepared["blocks"]):
        rejected("incomplete_range", "read_evidence_ids")
    rules = {
        rule["id"]: rule for rule in evidence["rules"] if rule["provenance_status"] == "effective"
    }
    for index, candidate in enumerate(data["issues"]):
        path = f"issues[{index}]"
        rule_id = candidate["rule_id"]
        try:
            if rule_id is not None and rule_id not in rules:
                rejected("source_not_allowed", path + ".rule_id")
            left = claim(candidate["left"], prepared["blocks"], path + ".left")
            right = claim(candidate["right"], prepared["blocks"], path + ".right")
            if left["evidence_id"] not in prepared["body_ids"] or right["evidence_id"] not in (
                prepared["body_ids"] if kind == "consistency" else prepared["source_ids"]
            ):
                rejected("source_not_allowed", path, [left["evidence_id"], right["evidence_id"]])
            if not conflict(left, right, scope_residue=kind == "scope_residue"):
                rejected(
                    "unsupported_quantity" if kind == "consistency" else "scope_mismatch",
                    path,
                    [left["evidence_id"], right["evidence_id"]],
                )
            if candidate["severity"] == "critical" and (
                rule_id is None or rules[rule_id]["kind"] not in ("mandatory", "disqualification")
            ):
                rejected(
                    "invalid_structure",
                    path + ".severity",
                    [left["evidence_id"], right["evidence_id"]],
                )
            if (
                candidate["severity"] == "critical"
                and left["attribute"] not in rules[rule_id]["statement"]
            ):
                rejected(
                    "scope_mismatch", path + ".rule_id", [left["evidence_id"], right["evidence_id"]]
                )
            statement = (
                f"{left['subject']}（{left['scope']}）的{left['attribute']}存在两处不相容的逐字断言："
                f"{left['relation']} {left['value']}{left['unit'] or ''}；"
                f"{right['relation']} {right['value']}{right['unit'] or ''}。请核对原页条件，不自动修改正文。"
            )
            accepted.append(
                candidate
                | {
                    "left": left,
                    "right": right,
                    "statement": statement,
                    "model_statement_digest": sha(candidate["statement"]),
                }
            )
        except DomainError as error:
            diagnostics.extend(error.details.get("diagnostics", []))
            if rule_id in rules and rules[rule_id]["kind"] in ("mandatory", "disqualification"):
                affected.add(rule_id)
    if diagnostics:
        gaps.append("部分候选断言未通过证据、主体范围或单位核验；已保留诊断，不能视为全项完整")
    if any(doc["coverage"] != "complete" for doc in prepared["documents"]):
        gaps.append("相关原件仍有未读或结构识别缺口")
    return {
        "kind": kind,
        "issues": accepted,
        "diagnostics": diagnostics,
        "core_affected_rule_ids": sorted(affected),
        "gaps": list(dict.fromkeys(gaps)),
        "check_state": "partial" if gaps else "complete",
        "planned_evidence_count": len(prepared["blocks"]),
        "read_evidence_count": len(data["read_evidence_ids"]),
    }
