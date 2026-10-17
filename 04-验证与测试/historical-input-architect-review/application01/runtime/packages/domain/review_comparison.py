"""两轮真实冻结输入比较；不以当前最新轮的结论回写所选历史轮。"""

from difflib import SequenceMatcher

from packages.domain.runtime import sha


def original_digests(root, evidence_ids):
    wanted = set(evidence_ids)
    return sorted(
        {
            doc["digest"]
            for doc in root["documents"]
            if any(str(block["id"]) in wanted for block in doc["blocks"])
        }
    )


def rule_identity(root, rule, *, include_provenance=True):
    semantic = {
        key: rule[key]
        for key in (
            "kind",
            "statement",
            "conditions",
            "exceptions",
            "max_points",
            "applicability_scope",
            "provenance_status",
        )
    }
    if not include_provenance:
        semantic.pop("provenance_status")
    semantic["bands"] = [
        {key: value for key, value in band.items() if key != "evidence_ids"}
        for band in rule["bands"]
    ]
    return sha({"rule": semantic, "originals": original_digests(root, rule["evidence_ids"])})


def axes(snapshot):
    root, manifest = snapshot["root"], snapshot["manifest"]
    return {
        "method_changed": manifest["method_digest"],
        "policy_changed": manifest["policy_digest"],
        "rules_or_maxima_changed": sorted(rule_identity(root, rule) for rule in root["rules"]),
        "facts_changed": sorted(
            sha(
                {
                    "values": {
                        key: fact[key] for key in ("subject", "scope", "name", "value", "unit")
                    },
                    "originals": original_digests(root, fact["evidence_ids"]),
                }
            )
            for fact in root["confirmed_facts"]
        ),
        "identity_changed": root.get("identity", {}).get("digest"),
        "source_effectivity_changed": sorted(
            sha(ref) for ref in manifest["source_refs"] if ref["kind"] != "body"
        ),
        "content_check_scope_changed": manifest["content_checks"],
        "parsing_method_changed": sorted(
            {(doc["role"], doc["parser_digest"], doc["config_digest"]) for doc in root["documents"]}
        ),
    }


def body(root, version_id):
    return next(doc for doc in root["documents"] if str(doc["document_version_id"]) == version_id)


def changes(left, right):
    a = [
        block["text_content"]
        for block in body(left["root"], left["manifest"]["body_version_id"])["blocks"]
    ]
    b = [
        block["text_content"]
        for block in body(right["root"], right["manifest"]["body_version_id"])["blocks"]
    ]
    if len(a) > 5000 or len(b) > 5000 or sum(map(len, a + b)) > 400000:
        return {
            "state": "partial",
            "items": [],
            "gaps": ["完整文字差异超过本批确定性范围，未截取后声称无变化；原件仍可逐页对照"],
            "total_change_groups": None,
        }
    edits = [
        {
            "kind": tag,
            "left_block_range": [i1, i2],
            "right_block_range": [j1, j2],
            "left_text": a[i1:i2],
            "right_text": b[j1:j2],
        }
        for tag, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
        if tag != "equal"
    ]
    return {
        "state": "partial" if len(edits) > 2000 else "complete",
        "items": edits[:2000],
        "gaps": ["文字差异组超过展示上限，保留前2000组并明确未覆盖其余范围"]
        if len(edits) > 2000
        else [],
        "total_change_groups": len(edits),
    }


def compare(plan):
    left, right = plan["left"], plan["right"]
    la, ra = axes(left), axes(right)
    breaks = [name for name in la if la[name] != ra[name]]
    if left["state"] != "succeeded" or right["state"] != "succeeded":
        breaks.append("review_execution_incomplete")
    if left["core_coverage"] != "complete" or right["core_coverage"] != "complete":
        breaks.append("core_coverage_incomplete")
    if not left["sources_valid"] or not right["sources_valid"]:
        breaks.append("source_effectivity_not_current")
    if any(
        side["root"].get("identity", {}).get("binding_status") != "confirmed"
        for side in (left, right)
    ):
        breaks.append("identity_not_frozen_or_unconfirmed")
    left_rule_keys = {
        rule["id"]: rule_identity(left["root"], rule, include_provenance=False)
        for rule in left["root"]["rules"]
    }
    right_rule_keys = {
        rule["id"]: rule_identity(right["root"], rule, include_provenance=False)
        for rule in right["root"]["rules"]
    }
    right_checks = {right_rule_keys.get(check["rule_id"]): check for check in right["checks"]}
    left_roots = {item["root_id"]: item for item in left["findings"]}
    right_roots = {item["root_id"]: item for item in right["findings"]}
    items = []
    for root_id, old in left_roots.items():
        actual = right_roots.get(root_id)
        key = left_rule_keys.get(old["rule_id"])
        checked = right_checks.get(key) if key else None
        state = "not_rechecked"
        basis = "所选新版没有该问题的完整有效复核；后续其他轮次的解决状态不能回填这里"
        if actual and actual["verdict"] in ("violated", "not_found") and actual["freshly_checked"]:
            state, basis = "persisting", "所选新版仍明确检出同一根问题"
        elif (
            checked
            and old.get("core_recheck_applicable", True)
            and checked["state"] == "succeeded"
            and checked["verdict"] in ("satisfied", "not_applicable")
            and right["state"] == "succeeded"
            and right["sources_valid"]
            and (
                body(left["root"], left["manifest"]["body_version_id"])["digest"]
                != body(right["root"], right["manifest"]["body_version_id"])["digest"]
                or checked.get("obligation_ended", False)
            )
            and checked.get("range_eligible", False)
            and not checked.get("core_affected", False)
        ):
            state, basis = "resolved_by_recheck", "所选新版以真实新稿及对应有效规则完成明确复核"
        elif actual and actual["verdict"] in ("violated", "not_found"):
            state, basis = "not_rechecked", "保留先前问题，本轮未执行该问题的对应复核"
        items.append(
            {
                "root_id": root_id,
                "left_finding_id": old["id"],
                "right_finding_id": actual["id"] if actual else None,
                "status": state,
                "basis": basis,
                "statement": (actual or old)["statement"],
            }
        )
    for root_id, new in right_roots.items():
        if root_id not in left_roots:
            items.append(
                {
                    "root_id": root_id,
                    "left_finding_id": None,
                    "right_finding_id": new["id"],
                    "status": "new",
                    "basis": "所选新版新增根问题；不会与旧问题的解决相抵消",
                    "statement": new["statement"],
                }
            )
    return {
        "comparison_method_digest": plan["comparison_method_digest"],
        "left_review_id": left["review_id"],
        "right_review_id": right["review_id"],
        "left_manifest": left["manifest"],
        "right_manifest": right["manifest"],
        "comparable_for_content_attribution": not breaks,
        "break_reasons": breaks,
        "score_delta": None,
        "score_delta_reason": "尚无两轮合格正式评分，未计算提升分；不可比规则或方法也不能据百分制归因",
        "findings": items,
        "body_changes": changes(left, right),
        "scope_note": "原始变化可查看；不能把采纳建议、后续轮次结果或当前人工状态冒充这两轮已证实改进",
    }
