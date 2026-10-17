"""事实值和证据身份不可变；每次变更追加新集，沿用必须可证明等价。"""

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring.content import empty_content
from packages.authoring.repository import assert_sources
from packages.domain.human_decisions import append_decision
from packages.domain.review_data import source_closure
from packages.domain.rule_sets import current_decision
from packages.domain.runtime import DomainError, audit, clean, fetch, sha, uid, validate
from packages.rag.indexing import ensure_parse_sources, parse_source_id

FIELDS = ("subject", "scope", "name", "value", "unit", "evidence_ids")


def identity(fact):
    value = {key: fact[key] for key in FIELDS}
    value["evidence_ids"] = sorted(set(map(str, value["evidence_ids"])))
    return sha(value)


def evidence_basis(c, bid, evidence_ids, *, publish=True):
    ids = sorted(set(map(str, evidence_ids)))
    rows = c.execute(
        "SELECT e.id,e.parse_id,p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id WHERE e.bid_id=%s AND e.id=ANY(%s::uuid[]) ORDER BY e.id",
        (bid, ids),
    ).fetchall()
    if not ids or len(rows) != len(ids):
        raise DomainError("FACT_EVIDENCE_REQUIRED", "请提供本项目已核验原件的逐页依据", 422)
    refs = []
    for row in rows:
        source = parse_source_id(row["parse_id"])
        if publish:
            _, source = ensure_parse_sources(
                c, UUID(str(bid)), row["parse_id"], publish_readable=True
            )
        head = fetch(c, "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (source,))
        refs.append(
            clean(
                {
                    "source_id": source,
                    "source_revision": head["revision"],
                    "evidence_id": row["id"],
                    "document_version_id": row["document_version_id"],
                }
            )
        )
    content = empty_content("事实依据核验", 0)
    content["attrs"]["source_refs"] = refs
    assert_sources(c, bid, content)
    closure = source_closure(c, [{"id": ref["source_id"]} for ref in refs], lock=True)
    return {"references": refs, "source_closure_digest": sha(closure)}


def decision_basis(c, decision_id):
    row = c.execute(
        "SELECT payload->'basis' AS basis FROM audit_entries WHERE event_type='human.decision_recorded' AND payload->>'decision_id'=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (str(decision_id),),
    ).fetchone()
    return row["basis"] if row else None


def carryover(c, fact_id):
    row = c.execute(
        "SELECT payload->'carryovers' AS items,created_at FROM audit_entries WHERE event_type='facts.set_replaced' AND payload->'carryovers' @> %s::jsonb ORDER BY created_at DESC,id DESC LIMIT 1",
        (Jsonb([{"new_fact_id": str(fact_id)}]),),
    ).fetchone()
    if not row:
        return None
    item = next(item for item in row["items"] if item["new_fact_id"] == str(fact_id))
    return clean(
        {
            "from_fact_id": item["from_fact_id"],
            "from_decision_id": item["from_decision_id"],
            "equivalence_digest": item["equivalence_digest"],
            "adopted_at": row["created_at"],
        }
    )


def current_facts(c, bid):
    head = c.execute("SELECT * FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone() or {
        "bid_id": bid,
        "fact_set_id": None,
        "revision": 0,
    }
    rows = (
        c.execute(
            "SELECT * FROM fact_items WHERE bid_id=%s AND fact_set_id=%s ORDER BY id",
            (bid, head["fact_set_id"]),
        ).fetchall()
        if head["fact_set_id"]
        else []
    )
    return head, rows


def replace_set(
    c,
    bid,
    actor,
    reason,
    *,
    kind,
    removed_id=None,
    new_fact=None,
    command_id=None,
    confirmation_only=False,
):
    """调用者已锁bid；所有克隆/头/映射/真实确认在同一事务，无外部模型调用。"""
    if kind not in ("add", "replace", "withdraw") or not reason.strip():
        raise DomainError("FACT_CHANGE_INVALID", "请填写事实更正理由", 422)
    c.execute(
        "INSERT INTO fact_heads(bid_id,revision) VALUES(%s,0) ON CONFLICT(bid_id) DO NOTHING",
        (bid,),
    )
    head, previous = current_facts(c, bid)
    if removed_id is not None and str(removed_id) not in {str(row["id"]) for row in previous}:
        raise DomainError("FACT_HEAD_CHANGED", "事实集已变化，请核对当前事实", 409)
    if kind in ("replace", "withdraw") and removed_id is None:
        raise DomainError("FACT_CHANGE_INVALID", "更正或撤回必须指定当前事实", 422)
    if new_fact is not None:
        validate("FactInput", new_fact)
        if any(not new_fact[key].strip() for key in ("subject", "scope", "name", "reason")):
            raise DomainError("FACT_CHANGE_INVALID", "请完整填写主体、范围、名称和依据理由", 422)
        try:
            new_basis = evidence_basis(c, bid, new_fact["evidence_ids"])
        except ValueError as error:
            raise DomainError(
                "FACT_SOURCE_NOT_EFFECTIVE", "事实来源已撤回或身份不一致，请先核验来源", 409
            ) from error
    else:
        new_basis = None
    set_id = uid()
    remaining = [row for row in previous if confirmation_only or str(row["id"]) != str(removed_id)]
    all_values = [clean({key: row[key] for key in FIELDS}) for row in remaining]
    if new_fact is not None:
        all_values.append({key: new_fact[key] for key in FIELDS})
    c.execute(
        "INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)",
        (
            set_id,
            bid,
            sha({"values": all_values, "previous": head["fact_set_id"], "reason": reason}),
        ),
    )
    mappings = []
    for old in remaining:
        new_id = uid()
        _insert_fact(c, bid, set_id, new_id, old)
        decision = current_decision(c, bid, "fact", old["id"])
        if not decision:
            continue
        basis = decision_basis(c, decision["id"])
        try:
            now_basis = evidence_basis(c, bid, old["evidence_ids"])
        except (DomainError, ValueError):
            continue
        if basis != {"fact_identity": identity(old), "evidence": now_basis}:
            continue
        c.execute(
            "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'fact',%s,1,%s)",
            (bid, new_id, decision["id"]),
        )
        mappings.append(
            {
                "new_fact_id": str(new_id),
                "from_fact_id": str(old["id"]),
                "from_decision_id": str(decision["id"]),
                "equivalence_digest": sha(basis),
            }
        )
    added_id = None
    if new_fact is not None:
        added_id = uid()
        _insert_fact(c, bid, set_id, added_id, new_fact)
        append_decision(
            c,
            bid,
            actor,
            "fact",
            added_id,
            {
                "decision": "confirmed",
                "reason": new_fact["reason"],
                "evidence_ids": new_fact["evidence_ids"],
            },
            command_id=command_id,
            basis={"fact_identity": identity(new_fact), "evidence": new_basis},
        )
    c.execute(
        "INSERT INTO fact_set_changes(id,bid_id,new_set_id,previous_set_id,changed_fact_id,kind,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (uid(), bid, set_id, head["fact_set_id"], removed_id, kind, reason, actor),
    )
    c.execute(
        "UPDATE fact_heads SET fact_set_id=%s,revision=revision+1 WHERE bid_id=%s", (set_id, bid)
    )
    audit(
        c,
        actor,
        bid,
        "facts.set_replaced",
        {
            "previous_set_id": str(head["fact_set_id"]) if head["fact_set_id"] else None,
            "fact_set_id": str(set_id),
            "kind": kind,
            "confirmation_only": confirmation_only,
            "changed_fact_id": str(removed_id) if removed_id else None,
            "added_fact_id": str(added_id) if added_id else None,
            "carryovers": mappings,
        },
        command_id,
    )
    return set_id, added_id


def _insert_fact(c, bid, set_id, fact_id, value):
    c.execute(
        "INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[])",
        (
            fact_id,
            bid,
            set_id,
            value["subject"],
            value["scope"],
            value["name"],
            Jsonb(value["value"]),
            value["unit"],
            value["evidence_ids"],
        ),
    )
