"""有据补遗效力：义务按条款结束，历史原件和其他条款保留。"""

from botocore.exceptions import BotoCoreError, ClientError
from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.fact_sets import decision_basis, evidence_basis
from packages.domain.human_decisions import append_decision
from packages.domain.input_changes import invalidate_pending
from packages.domain.review_data import _with_evidence
from packages.domain.rule_corrections import COLUMNS, _insert
from packages.domain.rule_sets import current_decision, rules, semantic_key, validate_addition
from packages.domain.runtime import DomainError, audit, clean, fetch, sha, uid

EVENT = "sources.effectivity_decided"
ACTIONS = ("whole_replace", "partial_replace", "defer")


def verify_originals(objects):
    for item in objects:
        try:
            stream = storage.client().get_object(Bucket=storage.bucket(), Key=item["object_key"])[
                "Body"
            ]
            try:
                raw = stream.read()
            finally:
                stream.close()
        except (BotoCoreError, ClientError) as error:
            raise DomainError(
                "SOURCE_ORIGINAL_UNAVAILABLE",
                "效力核验原件暂时无法读取，输入已保留",
                503,
                retryable=True,
            ) from error
        if len(raw) != item["byte_size"] or sha(raw) != item["digest"]:
            raise DomainError(
                "SOURCE_ORIGINAL_DIGEST_MISMATCH", "效力依据的原件实际字节校验失败", 409
            )


def termination_basis(c, bid, rule):
    """按精确语义及逐字证据身份寻找人工效力决定；不冒造一次规则驳回。"""
    row = c.execute(
        "SELECT id,actor_id,created_at,payload FROM audit_entries WHERE bid_id=%s AND event_type=%s AND payload->'ended_rules' @> %s::jsonb ORDER BY created_at DESC,id DESC LIMIT 1",
        (
            bid,
            EVENT,
            Jsonb(
                [
                    {
                        "semantic_key": semantic_key(rule),
                        "evidence_ids": sorted(map(str, rule["evidence_ids"])),
                    }
                ]
            ),
        ),
    ).fetchone()
    return clean(row) if row else None


def termination_keys(c, bid):
    rows = c.execute(
        "SELECT payload->'ended_rules' AS items FROM audit_entries WHERE bid_id=%s AND event_type=%s ORDER BY created_at,id",
        (bid, EVENT),
    ).fetchall()
    return {
        (item["semantic_key"], tuple(sorted(item["evidence_ids"])))
        for row in rows
        for item in row["items"]
    }


def rule_documents(c, bid, item):
    rows = c.execute(
        "SELECT DISTINCT p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id WHERE e.bid_id=%s AND e.id=ANY(%s::uuid[]) ORDER BY p.document_version_id",
        (bid, item["evidence_ids"]),
    ).fetchall()
    return {str(row["document_version_id"]) for row in rows}


def evidence_impacts(c, bid, evidence_ids):
    """检索最终使用前的具体效力提示；旧原件可查证，不能当当前义务默默回答。"""
    selected = set(map(str, evidence_ids))
    if not selected:
        return []
    rows = c.execute(
        "SELECT id,payload FROM audit_entries WHERE bid_id=%s AND event_type=%s ORDER BY created_at DESC,id DESC",
        (bid, EVENT),
    ).fetchall()
    impacts = []
    for row in rows:
        payload = row["payload"]
        affected = [
            item for item in payload["ended_rules"] if selected.intersection(item["evidence_ids"])
        ]
        if affected:
            originals = c.execute(
                "SELECT id,statement FROM rule_items WHERE bid_id=%s AND id=ANY(%s::uuid[]) ORDER BY id",
                (bid, [item["rule_id"] for item in affected]),
            ).fetchall()
            impacts.append(
                clean(
                    {
                        "decision_id": row["id"],
                        "action": payload["action"],
                        "old_document_version_id": payload["old_document_version_id"],
                        "new_document_version_id": payload["new_document_version_id"],
                        "affected_rules": originals,
                        "reason": payload["reason"],
                        "state": "superseded",
                    }
                )
            )
    pending = c.execute(
        "SELECT r.id,r.statement,r.evidence_ids FROM rule_items r JOIN bid_profiles p ON p.bid_id=r.bid_id AND p.current_rule_set_id=r.rule_set_id WHERE r.bid_id=%s AND r.provenance_status='needs_revalidation' AND r.evidence_ids && %s::uuid[] ORDER BY r.id",
        (bid, sorted(selected)),
    ).fetchall()
    if pending:
        impacts.append(
            clean(
                {
                    "decision_id": None,
                    "action": "defer",
                    "old_document_version_id": None,
                    "new_document_version_id": None,
                    "affected_rules": [
                        {"id": item["id"], "statement": item["statement"]} for item in pending
                    ],
                    "reason": "这些要求的效力或适用关系尚待核验",
                    "state": "needs_revalidation",
                }
            )
        )
    return impacts


def _quoted_basis(body, documents):
    new_id = body["new_document_version_id"]
    chosen = [
        doc
        for doc in documents
        if str(doc["document_version_id"]) == new_id and doc["role"] != "body"
    ]
    if len(chosen) != 1:
        raise DomainError(
            "SOURCE_EFFECTIVITY_NEW_SOURCE_INVALID", "请选择当前生效资料中的补遗原件", 422
        )
    blocks = {str(block["id"]): block for block in chosen[0]["blocks"]}
    if not body["scope_basis"]:
        raise DomainError("SOURCE_EFFECTIVITY_QUOTE_REQUIRED", "请核对替代范围的逐字原页依据", 422)
    for quote in body["scope_basis"]:
        block = blocks.get(quote["evidence_id"])
        if not block or not quote["quote"].strip() or quote["quote"] not in block["text_content"]:
            raise DomainError(
                "SOURCE_EFFECTIVITY_QUOTE_INVALID", "效力依据必须逐字对应所选补遗原页", 422
            )
    if body["action"] == "whole_replace" and any(
        marker in quote["quote"]
        for quote in body["scope_basis"]
        for marker in (
            "其他条款不变",
            "其余条款不变",
            "其他要求不变",
            "其余要求不变",
            "其他内容不变",
            "其余内容不变",
            "仅替换",
            "仅修改",
            "局部替代",
        )
    ):
        raise DomainError(
            "SOURCE_EFFECTIVITY_SCOPE_CONFLICT",
            "所选原文含明确局部范围，不能确认整份替代；请核对局部影响",
            422,
        )
    return chosen[0], blocks


def decide(c, bid, actor, command_id, body):
    """调用者已在bid锁内核对完整效力投影ETag；事务失败不产生部分新包。"""
    action = body["action"]
    if action not in ACTIONS or not body["reason"].strip():
        raise DomainError("SOURCE_EFFECTIVITY_INVALID", "请明确本次效力动作及具体依据", 422)
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    if str(profile["current_rule_set_id"]) != body["base_rule_set_id"]:
        raise DomainError("RULE_SET_REVISION_CONFLICT", "要求清单已变化，请重新核对影响", 412)
    previous = fetch(
        c,
        "SELECT * FROM rule_sets WHERE bid_id=%s AND id=%s",
        (bid, profile["current_rule_set_id"]),
    )
    originals = rules(c, previous["id"])
    old_version = body["old_document_version_id"]
    fetch(c, "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s", (bid, old_version))
    if body["new_document_version_id"]:
        fetch(
            c,
            "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s",
            (bid, body["new_document_version_id"]),
        )
    if old_version == body["new_document_version_id"]:
        raise DomainError("SOURCE_EFFECTIVITY_SAME_SOURCE", "请分别选择原资料和补遗资料", 422)
    bindings = {str(item["id"]): rule_documents(c, bid, item) for item in originals}
    eligible = {identity for identity, sources in bindings.items() if old_version in sources}
    if not eligible:
        raise DomainError(
            "SOURCE_EFFECTIVITY_NO_RULES", "原资料没有可对应的当前条款，请核对来源身份", 422
        )
    selected = set(body["rule_ids"])
    if not selected <= eligible or (action != "whole_replace" and not selected):
        raise DomainError(
            "SOURCE_EFFECTIVITY_RULE_SCOPE_INVALID", "所选影响条款与原资料不对应", 422
        )
    mixed = {identity for identity in eligible if bindings[identity] != {old_version}}
    if action == "whole_replace":
        if selected:
            raise DomainError(
                "SOURCE_EFFECTIVITY_WHOLE_SCOPE",
                "整份替代由服务器列出全部受影响条款，请勿混用局部选择",
                422,
            )
        selected = eligible - mixed
        unresolved = mixed
    else:
        unresolved = selected if action == "defer" else set()
    ended = selected if action != "defer" else set()
    additions = []
    if action != "defer":
        selected_source = c.execute(
            "SELECT a.document_version_id,a.role,a.ordinal,v.blob_id,v.obtained_at,b.digest,b.object_key,b.byte_size,p.id AS parse_id,p.parser_digest,p.config_digest,p.page_count,p.coverage,p.gaps FROM material_assignments a JOIN document_versions v ON v.id=a.document_version_id AND v.bid_id=a.bid_id JOIN blobs b ON b.id=v.blob_id LEFT JOIN LATERAL(SELECT * FROM parse_runs WHERE document_version_id=v.id ORDER BY created_at DESC,id DESC LIMIT 1)p ON true WHERE a.bid_id=%s AND a.snapshot_id=%s AND a.document_version_id=%s AND a.role IN('tender','supplement','drawing','boq') ORDER BY a.role,a.ordinal",
            (bid, profile["current_material_id"], body["new_document_version_id"]),
        ).fetchall()
        documents = _with_evidence(c, bid, selected_source)
        _, blocks = _quoted_basis(body, documents)
        evidence_basis(c, bid, [quote["evidence_id"] for quote in body["scope_basis"]])
        additions = [
            validate_addition(value, bid, list(blocks.values())) for value in body["additions"]
        ]
        for value in additions:
            evidence_basis(c, bid, value["evidence_ids"])
    elif body["scope_basis"] or body["additions"]:
        raise DomainError(
            "SOURCE_EFFECTIVITY_DEFER_NOT_CONFIRMATION",
            "保存待核验只记录具体歧义，不确认替代或新增条款",
            422,
        )
    if action == "defer" and all(
        item["provenance_status"] == "needs_revalidation"
        for item in originals
        if str(item["id"]) in selected
    ):
        audit(
            c,
            actor,
            bid,
            "sources.effectivity_note_recorded",
            {
                "rule_set_id": str(previous["id"]),
                "rule_ids": sorted(selected),
                "reason": body["reason"],
                "automatic_review_started": False,
            },
            command_id,
        )
        return previous["id"]
    new_set = uid()
    prepared, carries, ended_rules, mappings = [], {}, [], []
    for old in originals:
        identity = str(old["id"])
        value = {key: old[key] for key in COLUMNS}
        if value["origin_kind"] != "model_extracted":
            value["origin_kind"] = "carried_human"
        if identity in ended:
            value["provenance_status"] = "superseded"
            ended_rules.append(
                {
                    "rule_id": identity,
                    "semantic_key": semantic_key(old),
                    "evidence_ids": sorted(map(str, old["evidence_ids"])),
                }
            )
        elif identity in unresolved:
            value["provenance_status"] = "needs_revalidation"
        new_id = uid()
        prepared.append((new_id, value))
        mappings.append(
            {
                "from_rule_id": identity,
                "new_rule_id": str(new_id),
                "provenance_status": value["provenance_status"],
            }
        )
        decision = current_decision(c, bid, "rule", old["id"])
        if value["provenance_status"] == "effective":
            try:
                basis = evidence_basis(c, bid, old["evidence_ids"])
            except (DomainError, ValueError):
                value["provenance_status"] = "needs_revalidation"
                mappings[-1]["provenance_status"] = "needs_revalidation"
            else:
                if decision and (
                    decision["decision"] != "confirmed"
                    or decision_basis(c, decision["id"]) == basis
                ):
                    carries[new_id] = (old, decision)
        elif (
            old["provenance_status"] == "superseded"
            and decision
            and decision["decision"] == "rejected"
        ):
            carries[new_id] = (old, decision)
    existing = {
        (semantic_key(value), tuple(sorted(map(str, value["evidence_ids"]))))
        for _, value in prepared
        if value["provenance_status"] != "superseded"
    }
    for value in additions:
        key = (semantic_key(value), tuple(sorted(value["evidence_ids"])))
        if key in existing:
            raise DomainError("RULE_ALREADY_EXISTS", "新增条款与保留条款重复，请核对影响清单", 409)
        existing.add(key)
    c.execute(
        "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            new_set,
            bid,
            profile["current_material_id"],
            previous["revision"] + 1,
            sha({"parent": str(previous["id"]), "decision": body, "rules": clean(prepared)}),
            "partial"
            if unresolved
            or any(value["provenance_status"] == "needs_revalidation" for _, value in prepared)
            else previous["coverage"],
        ),
    )
    for new_id, value in prepared:
        _insert(c, bid, new_set, new_id, value)
        if new_id in carries:
            old, decision = carries[new_id]
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
                            "evidence_ids": sorted(map(str, value["evidence_ids"])),
                        }
                    ),
                ),
            )
    added_ids = []
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
            basis=evidence_basis(c, bid, value["evidence_ids"]),
        )
        added_ids.append(str(new_id))
    c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (new_set, bid))
    invalidate_pending(c, bid, "生效要求已更新，待重新评审", "SOURCE_EFFECTIVITY_CHANGED")
    audit(
        c,
        actor,
        bid,
        EVENT,
        {
            "from_rule_set_id": str(previous["id"]),
            "rule_set_id": str(new_set),
            "action": action,
            "old_document_version_id": old_version,
            "new_document_version_id": body["new_document_version_id"],
            "scope_basis": body["scope_basis"],
            "reason": body["reason"],
            "ended_rules": ended_rules,
            "rule_mappings": mappings,
            "added_rule_ids": added_ids,
            "automatic_review_started": False,
        },
        command_id,
    )
    return new_set
