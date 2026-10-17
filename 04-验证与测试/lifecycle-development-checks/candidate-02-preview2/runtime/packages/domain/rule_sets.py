"""不可变规则包与有据决定沿用；人工补漏的存续不取决于模型输出清单。"""

import re
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, clean, fetch, sha, uid, validate


def current_decision(c, bid, kind, object_id):
    direct = c.execute(
        "SELECT d.*,h.revision AS decision_revision FROM decision_heads h JOIN decisions d ON d.id=h.decision_id AND d.bid_id=h.bid_id WHERE h.bid_id=%s AND h.object_kind=%s AND h.object_id=%s",
        (bid, kind, object_id),
    ).fetchone()
    if direct or kind != "rule":
        return direct
    return c.execute(
        "SELECT d.*,1 AS decision_revision FROM rule_carryovers r JOIN decisions d ON d.id=r.from_decision_id AND d.bid_id=r.bid_id WHERE r.bid_id=%s AND r.new_rule_id=%s",
        (bid, object_id),
    ).fetchone()


def validate_addition(addition, bid, blocks, *, model=False):
    validate("RuleAddition", addition)
    if addition["scope"]["bid_id"] != str(bid):
        raise DomainError("RULE_SCOPE_MISMATCH", "条款适用范围必须属于当前项目", 422)
    by_id = {str(block["id"]): block for block in blocks}
    quotes, ids = [], []
    for item in addition["evidence_quotes"]:
        block = by_id.get(item["evidence_id"])
        if not block or item["quote"] not in block["text_content"]:
            raise DomainError(
                "RULE_QUOTE_INVALID", "条款依据必须逐字对应本轮生效资料的原页证据", 422
            )
        quotes.append(item["quote"])
        ids.append(item["evidence_id"])
    joined = "\n".join(quotes)
    if model and addition["statement"] not in joined:
        raise DomainError("RULE_STATEMENT_UNGROUNDED", "模型条款正文没有逐字依据", 422)
    for condition in addition["conditions"] + addition["exceptions"]:
        if condition not in joined:
            raise DomainError(
                "RULE_CONDITION_UNGROUNDED", "条件或例外必须保留逐字依据，不能省略或推定", 422
            )
    maximum = Decimal(addition["maximum"]) if addition["maximum"] is not None else None
    if maximum is not None and not re.search(
        r"(?<![0-9.])" + re.escape(addition["maximum"]) + r"(?![0-9.])", joined
    ):
        raise DomainError("RULE_POINTS_UNGROUNDED", "评分分值未找到逐字依据", 422)
    for band in addition["bands"]:
        if (
            not set(band["evidence_ids"]) <= set(ids)
            or maximum is None
            or Decimal(band["points"]) > maximum
            or band["description"] not in joined
        ):
            raise DomainError("RULE_BAND_INVALID", "评分档次须在同一条款依据内且不超过满分", 422)
    return {
        "kind": addition["kind"],
        "statement": addition["statement"],
        "conditions": addition["conditions"],
        "exceptions": addition["exceptions"],
        "max_points": maximum,
        "evidence_ids": list(dict.fromkeys(ids)),
        "bands": addition["bands"],
        "applicability_scope": addition["scope"],
        "origin_kind": "model_extracted",
        "manual_origin_rule_id": None,
        "provenance_status": "effective",
    }


def semantic_key(item):
    return sha(
        clean(
            {
                k: item[k]
                for k in (
                    "kind",
                    "statement",
                    "conditions",
                    "exceptions",
                    "max_points",
                    "bands",
                    "applicability_scope",
                )
            }
        )
    )


def rules(c, rule_set):
    return c.execute(
        "SELECT * FROM rule_items WHERE rule_set_id=%s ORDER BY kind,id", (rule_set,)
    ).fetchall()


def create_extracted_set(c, bid, material, items, coverage, documents):
    """Caller持有bid执行锁；只沿用真实旧决定，Worker不写人工决定。"""
    from packages.domain.source_effectivity import termination_keys

    ended_identities = termination_keys(c, bid)
    profile = fetch(
        c,
        "SELECT current_rule_set_id,current_material_id FROM bid_profiles WHERE bid_id=%s",
        (bid,),
    )
    old_items = rules(c, profile["current_rule_set_id"]) if profile["current_rule_set_id"] else []
    previous = {semantic_key(item): item for item in old_items}
    prepared: list[dict[str, Any]] = []
    keys: dict[str, int] = {}
    carries = {}
    for original in items:
        item = dict(original)
        key = semantic_key(item)
        if (key, tuple(sorted(map(str, item["evidence_ids"])))) in ended_identities:
            item["provenance_status"] = "superseded"
        if key in keys:
            # 同一解释且证据完全一致才可确定归并，避免重复计数。
            prepared[keys[key]]["evidence_ids"] = list(
                dict.fromkeys(prepared[keys[key]]["evidence_ids"] + item["evidence_ids"])
            )
            continue
        item["id"] = uid()
        keys[key] = len(prepared)
        prepared.append(item)
        if key in previous:
            old = previous[key]
            # 同一逐字证据才自动沿用普通模型条款决定。
            if set(map(str, old["evidence_ids"])) == set(map(str, item["evidence_ids"])):
                decision = current_decision(c, bid, "rule", old["id"])
                if decision:
                    carries[item["id"]] = (old, decision)
                    if (
                        old["provenance_status"] == "superseded"
                        and decision["decision"] == "rejected"
                    ):
                        item["provenance_status"] = "superseded"
                        if old["origin_kind"] != "model_extracted":
                            item.update(
                                origin_kind="carried_human",
                                manual_origin_rule_id=old["manual_origin_rule_id"],
                            )
    current_versions = {
        str(doc["document_version_id"]) for doc in documents if doc["role"] != "body"
    }
    for old in old_items:
        if old["origin_kind"] == "model_extracted" or old["provenance_status"] == "superseded":
            continue
        evidence = c.execute(
            "SELECT DISTINCT p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id WHERE e.bid_id=%s AND e.id=ANY(%s::uuid[])",
            (bid, old["evidence_ids"]),
        ).fetchall()
        effective = (
            bool(evidence)
            and {str(row["document_version_id"]) for row in evidence} <= current_versions
        )
        key = semantic_key(old)
        if key in keys:
            item = prepared[keys[key]]
        else:
            item = {
                k: old[k]
                for k in (
                    "kind",
                    "statement",
                    "conditions",
                    "exceptions",
                    "max_points",
                    "bands",
                    "applicability_scope",
                    "evidence_ids",
                )
            }
            item["id"] = uid()
            keys[key] = len(prepared)
            prepared.append(item)
        item.update(
            origin_kind="carried_human",
            manual_origin_rule_id=old["manual_origin_rule_id"],
            provenance_status="effective" if effective else "needs_revalidation",
        )
        decision = current_decision(c, bid, "rule", old["id"])
        if effective and decision:
            carries[item["id"]] = (old, decision)
        elif not effective:
            carries.pop(item["id"], None)
            coverage = "partial" if coverage != "conflict" else coverage
    identity, revision = (
        uid(),
        c.execute(
            "SELECT coalesce(max(revision),0)+1 AS next FROM rule_sets WHERE bid_id=%s", (bid,)
        ).fetchone()["next"],
    )
    c.execute(
        "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,%s,%s,%s)",
        (identity, bid, material, revision, sha(clean(prepared)), coverage),
    )
    for item in prepared:
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands,applicability_scope,origin_kind,manual_origin_rule_id,provenance_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s,%s,%s)",
            (
                item["id"],
                bid,
                identity,
                item["kind"],
                item["statement"],
                Jsonb(item["conditions"]),
                Jsonb(item["exceptions"]),
                item["max_points"],
                list(map(str, item["evidence_ids"])),
                Jsonb(item["bands"]),
                Jsonb(item["applicability_scope"]),
                item.get("origin_kind", "model_extracted"),
                item.get("manual_origin_rule_id"),
                item.get("provenance_status", "effective"),
            ),
        )
        if item["id"] in carries:
            old, decision = carries[item["id"]]
            c.execute(
                "INSERT INTO rule_carryovers(bid_id,new_rule_id,from_rule_id,from_decision_id,equivalence_digest) VALUES(%s,%s,%s,%s,%s)",
                (bid, item["id"], old["id"], decision["id"], semantic_key(item)),
            )
    if str(profile["current_material_id"]) == str(material):
        c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (identity, bid))
    return identity
