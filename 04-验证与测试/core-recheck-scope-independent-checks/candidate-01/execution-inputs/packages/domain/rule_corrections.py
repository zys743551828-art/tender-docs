"""一次补充并确认：不可变新包、真实人工决定、旧决定有据沿用，同事务生效。"""

from psycopg.types.json import Jsonb

from packages.domain.human_decisions import append_decision
from packages.domain.input_changes import invalidate_pending
from packages.domain.review_data import effective_at, publish_sources, read_bundle, source_closure
from packages.domain.rule_sets import current_decision, rules, semantic_key, validate_addition
from packages.domain.runtime import DomainError, audit, fetch, sha, uid, validate

COLUMNS = (
    "kind",
    "statement",
    "conditions",
    "exceptions",
    "max_points",
    "evidence_ids",
    "bands",
    "applicability_scope",
    "origin_kind",
    "manual_origin_rule_id",
    "provenance_status",
)


def _insert(c, bid, set_id, rule_id, value):
    c.execute(
        "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands,applicability_scope,origin_kind,manual_origin_rule_id,provenance_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s,%s,%s)",
        (
            rule_id,
            bid,
            set_id,
            value["kind"],
            value["statement"],
            Jsonb(value["conditions"]),
            Jsonb(value["exceptions"]),
            value["max_points"],
            value["evidence_ids"],
            Jsonb(value["bands"]),
            Jsonb(value["applicability_scope"]),
            value["origin_kind"],
            value["manual_origin_rule_id"],
            value["provenance_status"],
        ),
    )


def correct_rules(c, bid, actor, command_id, body):
    """调用者已在bid锁下校验当前RuleSetView强ETag；失败由领域事务整体回滚。"""
    validate("RuleSetCorrectionRequest", body)
    if not body["reason"].strip():
        raise DomainError("CORRECTION_REASON_REQUIRED", "请填写补充条款的原文依据及理由", 422)
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    if str(profile["current_rule_set_id"]) != body["base_rule_set_id"]:
        raise DomainError("RULE_SET_REVISION_CONFLICT", "要求清单已更新，请核对当前清单", 412)
    previous = fetch(
        c, "SELECT * FROM rule_sets WHERE id=%s AND bid_id=%s", (body["base_rule_set_id"], bid)
    )
    if previous["material_id"] != profile["current_material_id"]:
        raise DomainError("RULE_SOURCE_STALE", "资料已变化，请先核对当前生效要求", 409)
    documents = read_bundle(c, bid, profile["current_material_id"])
    sources = [doc for doc in documents if doc["role"] != "body"]
    refs = publish_sources(c, bid, sources)
    closure = source_closure(c, refs, lock=True)
    effective_at(closure, c.execute("SELECT clock_timestamp() AS at").fetchone()["at"])
    blocks = [block for doc in sources for block in doc["blocks"]]
    additions = [validate_addition(value, bid, blocks) for value in body["additions"]]
    originals = rules(c, previous["id"])
    existing = {
        (semantic_key(value), tuple(sorted(map(str, value["evidence_ids"])))) for value in originals
    }
    for value in additions:
        key = (semantic_key(value), tuple(sorted(value["evidence_ids"])))
        if key in existing:
            raise DomainError("RULE_ALREADY_EXISTS", "该条款及依据已在当前清单，无需重复补入", 409)
        existing.add(key)
    new_set = uid()
    c.execute(
        "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            new_set,
            bid,
            previous["material_id"],
            previous["revision"] + 1,
            sha(
                {
                    "parent": str(previous["id"]),
                    "parent_digest": previous["digest"],
                    "additions": additions,
                    "source_closure": closure,
                }
            ),
            previous["coverage"],
        ),
    )
    for old in originals:
        new_id = uid()
        value = {key: old[key] for key in COLUMNS}
        if value["origin_kind"] != "model_extracted":
            value["origin_kind"] = "carried_human"
        _insert(c, bid, new_set, new_id, value)
        decision = current_decision(c, bid, "rule", old["id"])
        if decision and old["provenance_status"] == "effective":
            c.execute(
                "INSERT INTO rule_carryovers(bid_id,new_rule_id,from_rule_id,from_decision_id,equivalence_digest) VALUES(%s,%s,%s,%s,%s)",
                (
                    bid,
                    new_id,
                    old["id"],
                    decision["id"],
                    sha(
                        {
                            "rule": semantic_key(value),
                            "evidence_ids": sorted(map(str, old["evidence_ids"])),
                            "source_closure": closure,
                        }
                    ),
                ),
            )
    new_ids = []
    for value in additions:
        new_id = uid()
        value.update(
            origin_kind="human_added", manual_origin_rule_id=new_id, provenance_status="effective"
        )
        _insert(c, bid, new_set, new_id, value)
        append_decision(
            c,
            bid,
            actor,
            "rule",
            new_id,
            {
                "decision": "confirmed",
                "reason": body["reason"],
                "evidence_ids": value["evidence_ids"],
            },
            command_id=command_id,
            basis={"rule_identity": semantic_key(value), "source_closure_digest": sha(closure)},
        )
        new_ids.append(str(new_id))
    c.execute(
        "INSERT INTO rule_set_corrections(id,bid_id,from_rule_set_id,to_rule_set_id,command_id,actor_id,reason,additions) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uid(),
            bid,
            previous["id"],
            new_set,
            command_id,
            actor,
            body["reason"],
            Jsonb(body["additions"]),
        ),
    )
    c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (new_set, bid))
    invalidate_pending(c, bid, "本轮要求已更正，请使用新要求重新评审", "RULE_SET_CHANGED")
    audit(
        c,
        actor,
        bid,
        "rules.corrected",
        {
            "from_rule_set_id": str(previous["id"]),
            "rule_set_id": str(new_set),
            "added_rule_ids": new_ids,
            "automatic_review_started": False,
        },
        command_id,
    )
    return new_set


def reject_rule(c, bid, actor, rule_id, body, *, command_id):
    """有理由驳回解释改变当前有效义务，追加新包；不改原条款或自动评审。"""
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    previous = fetch(
        c,
        "SELECT * FROM rule_sets WHERE id=%s AND bid_id=%s",
        (profile["current_rule_set_id"], bid),
    )
    items = rules(c, previous["id"])
    if str(rule_id) not in {str(item["id"]) for item in items}:
        raise DomainError("RULE_SET_REVISION_CONFLICT", "当前规则包已变化，请核对后提交", 409)
    decision = append_decision(c, bid, actor, "rule", rule_id, body, command_id=command_id)
    new_set = uid()
    c.execute(
        "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            new_set,
            bid,
            previous["material_id"],
            previous["revision"] + 1,
            sha(
                {
                    "parent": str(previous["id"]),
                    "digest": previous["digest"],
                    "rejected_rule_id": str(rule_id),
                    "decision_id": str(decision["id"]),
                }
            ),
            previous["coverage"],
        ),
    )
    for old in items:
        new_id = uid()
        value = {key: old[key] for key in COLUMNS}
        if value["origin_kind"] != "model_extracted":
            value["origin_kind"] = "carried_human"
        if str(old["id"]) == str(rule_id):
            value["provenance_status"] = "superseded"
        _insert(c, bid, new_set, new_id, value)
        prior = current_decision(c, bid, "rule", old["id"])
        if prior:
            c.execute(
                "INSERT INTO rule_carryovers(bid_id,new_rule_id,from_rule_id,from_decision_id,equivalence_digest) VALUES(%s,%s,%s,%s,%s)",
                (
                    bid,
                    new_id,
                    old["id"],
                    prior["id"],
                    sha(
                        {
                            "rule": semantic_key(value),
                            "evidence_ids": sorted(map(str, old["evidence_ids"])),
                            "termination_decision": str(prior["id"])
                            if value["provenance_status"] == "superseded"
                            else None,
                        }
                    ),
                ),
            )
    c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (new_set, bid))
    invalidate_pending(c, bid, "本轮要求已更正，请使用新要求重新评审", "RULE_SET_CHANGED")
    audit(
        c,
        actor,
        bid,
        "rules.interpretation_rejected",
        {
            "from_rule_set_id": str(previous["id"]),
            "rule_set_id": str(new_set),
            "rejected_rule_id": str(rule_id),
            "decision_id": str(decision["id"]),
            "automatic_review_started": False,
        },
        command_id,
    )
    return decision, new_set
