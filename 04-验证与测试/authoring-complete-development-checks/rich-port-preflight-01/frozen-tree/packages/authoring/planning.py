"""Page-budget attribution is human planning, with exact source/confirmation guard."""

from uuid import UUID

from packages.authoring import format_bindings
from packages.domain.runtime import DomainError, fetch


def requirement_basis(c, bid, payload):
    if payload["budget_basis"] != "requirement":
        return None
    identity = payload["budget_rule_id"]
    if not identity or identity not in payload["rule_ids"]:
        raise DomainError(
            "AUTHORING_BUDGET_BASIS_REQUIRED", "要求页预算必须选择对应的当前要求", 422
        )
    rule = fetch(
        c,
        "SELECT r.* FROM rule_items r JOIN bid_profiles p ON p.bid_id=r.bid_id AND p.current_rule_set_id=r.rule_set_id WHERE r.bid_id=%s AND r.id=%s AND r.provenance_status='effective'",
        (bid, UUID(identity)),
    )
    refs = payload["source_refs"]
    if not refs or not {ref["evidence_id"] for ref in refs} <= set(map(str, rule["evidence_ids"])):
        raise DomainError(
            "AUTHORING_BUDGET_BASIS_REQUIRED", "要求页预算的原页依据必须属于该要求", 422
        )
    return format_bindings.confirmed_basis(c, bid, rule)


def assert_budget(c, bid, row):
    expected = requirement_basis(c, bid, row["payload"])
    if expected != row["payload"].get("budget_proof"):
        raise DomainError(
            "AUTHORING_BUDGET_BASIS_STALE", "页预算的要求确认依据已变化，请重新核对计划", 409
        )


def insert_template_plan(c, bid, draft, section, section_revision, actor, directory, item):
    from psycopg.types.json import Jsonb

    from packages.authoring.snapshot_structure import plan_digest
    from packages.domain.runtime import uid

    pages = item.get("planned_pages")
    payload = {
        "goal": item.get("goal", ""),
        "key_points": [],
        "rule_ids": [],
        "source_refs": [],
        "pending_facts": [],
        "budget_basis": "template" if pages is not None else "manual",
        "budget_rule_id": None,
        "budget_note": "目录模板提供的编写建议，不代表本项目事实/资格",
        "budget_proof": None,
    }
    if pages is not None:
        payload["template_budget_basis"] = {
            "template_version_id": directory["id"],
            "template_key": item["key"],
            "planned_pages": pages,
        }
    revision = c.execute(
        "SELECT coalesce(max(revision),0)+1 AS n FROM authoring_plan_versions WHERE section_id=%s",
        (section,),
    ).fetchone()["n"]
    value = {
        "id": uid(),
        "bid_id": bid,
        "draft_id": draft,
        "section_id": section,
        "revision": revision,
        "base_section_revision": section_revision,
        "state": "not_started",
        "planned_pages": pages,
        "payload": payload,
    }
    c.execute(
        "INSERT INTO authoring_plan_versions(id,bid_id,draft_id,section_id,revision,base_section_revision,state,planned_pages,payload,digest,created_by) VALUES(%s,%s,%s,%s,%s,%s,'not_started',%s,%s,%s,%s)",
        (
            value["id"],
            bid,
            draft,
            section,
            revision,
            section_revision,
            pages,
            Jsonb(payload),
            plan_digest(value),
            actor,
        ),
    )
    return str(value["id"])
