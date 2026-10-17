"""API-only trusted selection and structural memory. No model calls or source publication."""

from __future__ import annotations

import json
from uuid import UUID

from packages.authoring.repository import assert_sources, source_authority_manifest
from packages.domain.fact_sets import evidence_basis
from packages.domain.rule_sets import current_decision
from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.identity.repository import snapshot as identity_snapshot
from packages.knowledge.bridge import confirmed_fact
from packages.knowledge.rag_search import assert_trace_scope_current, trace_row
from packages.knowledge.sources import authority_manifest, lock_graph, lock_heads
from packages.rag.indexing import original_source_id, parse_source_id

MAX_INPUT_CHARACTERS = 100_000


def at(c):
    return c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]


def lock_scope(c, bids):
    ids = sorted(set(UUID(str(b)) for b in bids), key=str)
    if ids:
        rows = c.execute(
            "SELECT id FROM bids WHERE id=ANY(%s) ORDER BY id FOR UPDATE", (ids,)
        ).fetchall()
        if {row["id"] for row in rows} != set(ids):
            raise DomainError("ASSISTANT_TARGET_MISSING", "所选项目不存在", 404)
    lock_graph(c)


def project_memory(c, bid):
    heads = fetch(
        c,
        """SELECT b.id,b.title,b.revision,b.material_revision,b.settings_revision,
        p.current_body_id,p.current_material_id,p.current_rule_set_id,p.current_draft_id,p.current_review_id,
        p.phase,p.archived_at,d.revision AS draft_revision,h.fact_set_id,h.revision AS fact_revision
        FROM bids b LEFT JOIN bid_profiles p ON p.bid_id=b.id
        LEFT JOIN drafts d ON d.id=p.current_draft_id LEFT JOIN fact_heads h ON h.bid_id=b.id WHERE b.id=%s""",
        (bid,),
    )
    try:
        identity = identity_snapshot(c, bid)
    except DomainError as error:
        if error.code != "NOT_FOUND":
            raise
        identity = {"binding_status": "unavailable", "authority_id": None}
    clock = at(c)
    facts = c.execute(
        "SELECT f.* FROM fact_heads h JOIN fact_items f ON f.fact_set_id=h.fact_set_id AND f.bid_id=h.bid_id WHERE h.bid_id=%s ORDER BY f.id",
        (bid,),
    ).fetchall()
    decisions = c.execute(
        "SELECT object_kind,object_id,revision,decision_id FROM decision_heads WHERE bid_id=%s AND object_kind IN ('rule','fact','finding') ORDER BY object_kind,object_id",
        (bid,),
    ).fetchall()
    confirmed, gaps, basis = [], [], []
    for fact in facts:
        if identity.get("binding_status") == "confirmed" and confirmed_fact(c, bid, fact, clock):
            confirmed.append(
                {
                    k: fact[k]
                    for k in ("id", "subject", "scope", "name", "value", "unit", "evidence_ids")
                }
            )
            basis.append(evidence_basis(c, bid, fact["evidence_ids"], publish=False))
    if len(confirmed) != len(facts):
        gaps.append("未确认、无当前有效依据或主体待核的事实不作为结构记忆")
    rules = c.execute(
        "SELECT * FROM rule_items WHERE bid_id=%s AND rule_set_id=%s ORDER BY id",
        (bid, heads["current_rule_set_id"]),
    ).fetchall()
    trusted_rules = []
    for rule in rules:
        if rule["provenance_status"] != "effective":
            continue
        try:
            provenance = evidence_basis(c, bid, rule["evidence_ids"], publish=False)
        except (DomainError, ValueError):
            gaps.append("部分当前要求来源失效，未纳入回答依据")
            continue
        decision = current_decision(c, bid, "rule", rule["id"])
        trusted_rules.append(
            {
                "id": rule["id"],
                "kind": rule["kind"],
                "statement": rule["statement"],
                "decision": decision["decision"] if decision else "unconfirmed",
                "evidence_ids": rule["evidence_ids"],
            }
        )
        basis.append(provenance)
    findings = c.execute(
        """SELECT f.id,f.severity,f.verdict,f.statement,f.evidence_ids,f.review_id,
        i.body_version_id,i.material_snapshot_id FROM findings f
        JOIN review_runs r ON r.id=f.review_id AND r.bid_id=f.bid_id
        JOIN review_inputs i ON i.id=r.input_id AND i.bid_id=r.bid_id
        WHERE f.bid_id=%s AND f.review_id=%s ORDER BY f.id""",
        (bid, heads["current_review_id"]),
    ).fetchall()
    for finding in findings:
        finding["matches_current_body_and_material"] = (
            finding["body_version_id"] == heads["current_body_id"]
            and finding["material_snapshot_id"] == heads["current_material_id"]
        )
    omitted = {
        "confirmed_facts": max(0, len(confirmed) - 100),
        "rules": max(0, len(trusted_rules) - 100),
        "findings": max(0, len(findings) - 100),
    }
    if any(omitted.values()):
        gaps.append("结构记忆每类仅展示前100项；另有未提供项，不能作为完整评审范围")
    if findings:
        gaps.append("既有问题属于标明的评审版本；不以聊天或建议宣称新稿已复检或问题已解决")
    if identity.get("binding_status") != "confirmed":
        gaps.append("项目主体尚未确认，仅能做有限解释，不能当作事实资格")
    source_ids = sorted(
        {UUID(ref["source_id"]) for item in basis for ref in item["references"]}, key=str
    )
    source_authorities = authority_manifest(c, source_ids, clock) if source_ids else []
    return clean(
        {
            "heads": heads,
            "identity": identity,
            "decisions_digest": sha(decisions),
            "confirmed_facts": confirmed[:100],
            "rules": trusted_rules[:100],
            "findings": findings[:100],
            "full_structural_digest": sha(
                {"confirmed_facts": confirmed, "rules": trusted_rules, "findings": findings}
            ),
            "coverage": "partial",
            "omitted_counts": omitted,
            "evidence_basis_digest": sha(basis),
            "source_authorities": source_authorities,
            "gaps": list(dict.fromkeys(gaps)),
            "formal_scoring_input": False,
        }
    )


def selection_bids(c, selection, project_bid=None):
    result = set([UUID(str(project_bid))] if project_bid else [])
    for ref in selection:
        identity = UUID(ref["id"])
        if ref["kind"] == "project":
            bid = identity
        elif ref["kind"] == "document":
            bid = fetch(c, "SELECT bid_id FROM document_versions WHERE id=%s", (identity,))[
                "bid_id"
            ]
        elif ref["kind"] == "section":
            bid = fetch(c, "SELECT bid_id FROM sections WHERE id=%s", (identity,))["bid_id"]
        else:
            bid = fetch(
                c,
                "SELECT bid_id FROM retrieval.knowledge_retrieval_traces WHERE id=%s",
                (UUID(ref["trace_id"]),),
            )["bid_id"]
        if project_bid and bid != UUID(str(project_bid)):
            raise DomainError(
                "ASSISTANT_SOURCE_SCOPE_INVALID",
                "项目助手只接本项目资料或本项目已适用的知识检索命中",
                409,
            )
        result.add(bid)
    return sorted(result, key=str)


def document_source(c, identity, physical_page=None):
    doc = fetch(
        c,
        "SELECT v.id,v.bid_id,v.original_name,b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
        (identity,),
    )
    parse = c.execute(
        "SELECT id,coverage,page_count,gaps FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (identity,),
    ).fetchone()
    if not parse or parse["coverage"] == "failed":
        raise DomainError("ASSISTANT_SOURCE_NOT_READY", "资料尚无可核验解析", 409)
    pages = c.execute(
        "SELECT physical_page,quality,gaps FROM pages WHERE parse_id=%s AND (%s::integer IS NULL OR physical_page=%s) ORDER BY physical_page",
        (parse["id"], physical_page, physical_page),
    ).fetchall()
    if len(pages) != (1 if physical_page else parse["page_count"]) or any(
        p["quality"] == "failed" for p in pages
    ):
        raise DomainError(
            "ASSISTANT_SOURCE_NOT_READY", "所选范围包含缺失或失败原页，请选择可读的具体物理页", 409
        )
    source = parse_source_id(parse["id"])
    lock_heads(c, [source])
    authority = authority_manifest(c, [source], at(c))
    nodes = {row["ancestor_id"]: row for row in authority}
    identities = c.execute(
        "SELECT id,kind,document_version_id,digest FROM retrieval.source_nodes WHERE id=ANY(%s)",
        ([source, original_source_id(identity)],),
    ).fetchall()
    bound = len(identities) == 2 and all(
        n["kind"] == "document"
        and n["document_version_id"] == identity
        and n["digest"] == doc["digest"]
        for n in identities
    )
    edge = c.execute(
        "SELECT 1 FROM retrieval.lineage_edges WHERE child_id=%s AND parent_id=%s",
        (source, original_source_id(identity)),
    ).fetchone()
    if (
        str(original_source_id(identity)) not in nodes
        or any(
            nodes[str(n)]["digest"] != doc["digest"] for n in (source, original_source_id(identity))
        )
        or not bound
        or not edge
    ):
        raise DomainError("ASSISTANT_SOURCE_INVALID", "原件及解析来源身份不一致", 409)
    rows = c.execute(
        "SELECT id,physical_page,ordinal,text_content FROM evidence_blocks WHERE parse_id=%s AND (%s::integer IS NULL OR physical_page=%s) ORDER BY physical_page,ordinal LIMIT 501",
        (parse["id"], physical_page, physical_page),
    ).fetchall()
    if not rows or len(rows) > 500:
        raise DomainError(
            "ASSISTANT_SOURCE_TOO_LARGE", "所选资料过大或无文本，请选择具体物理页", 422
        )
    if any(not row["text_content"].strip() for row in rows):
        raise DomainError(
            "ASSISTANT_SOURCE_NOT_READY", "选定范围包含不可读证据，请缩小到可核验原页", 409
        )
    text = ""
    ranges = []
    for row in rows:
        text += f"[physical_page={row['physical_page']} evidence_id={row['id']}]\n"
        start = len(text)
        text += row["text_content"]
        ranges.append(
            {
                "start": start,
                "end": len(text),
                "physical_page": row["physical_page"],
                "evidence_id": str(row["id"]),
            }
        )
        text += "\n"
    return clean(
        {
            "bid_id": doc["bid_id"],
            "title": doc["original_name"],
            "text": text,
            "quote_ranges": ranges,
            "readability": {"coverage": parse["coverage"], "gaps": parse["gaps"], "pages": pages},
            "provenance": {
                "kind": "original_document",
                "document_version_id": identity,
                "parse_id": parse["id"],
                "physical_page": physical_page,
                "evidence_ids": [r["id"] for r in rows],
                "source_id": source,
                "source_authority": authority,
            },
            "formal_scoring_input": False,
        }
    )


def section_text(content):
    from packages.assistant.generation import node_text

    return "\n".join(node_text(node) for node in content.get("content", []))


def resolve(c, ref):
    identity = UUID(ref["id"])
    if ref["kind"] == "project":
        memory = project_memory(c, identity)
        value = {
            "bid_id": str(identity),
            "title": memory["heads"]["title"],
            "text": json.dumps(
                {k: v for k, v in memory.items() if k != "source_authorities"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "provenance": {"kind": "structured_project_memory", "memory": memory},
            "formal_scoring_input": False,
        }
    elif ref["kind"] == "document":
        value = document_source(c, identity, ref.get("physical_page"))
    elif ref["kind"] == "section":
        section = fetch(
            c,
            "SELECT s.*,r.content,r.digest FROM sections s JOIN section_revisions r ON r.section_id=s.id AND r.revision=s.head_revision WHERE s.id=%s",
            (identity,),
        )
        assert_sources(c, section["bid_id"], section["content"])
        value = clean(
            {
                "bid_id": section["bid_id"],
                "title": section["content"]["attrs"]["title"],
                "text": section_text(section["content"]),
                "provenance": {
                    "kind": "draft_section",
                    "section_id": identity,
                    "draft_id": section["draft_id"],
                    "revision": section["head_revision"],
                    "digest": section["digest"],
                    "authority": source_authority_manifest(
                        c, section["content"]["attrs"]["source_refs"]
                    ),
                },
                "formal_scoring_input": False,
            }
        )
    else:
        if not ref.get("trace_id"):
            raise DomainError("ASSISTANT_SOURCE_INVALID", "知识单元必须来自真实检索供给", 422)
        trace = trace_row(c, UUID(ref["trace_id"]))
        assert_trace_scope_current(c, trace["frozen_scope"], trace["bid_id"], trace["purpose"])
        unit = next((u for u in trace["selected_units"] if u["unit_id"] == str(identity)), None)
        if unit is None:
            raise DomainError("ASSISTANT_SOURCE_INVALID", "知识单元没有在这次实际供给中", 409)
        value = {
            "bid_id": str(trace["bid_id"]),
            "title": "知识参考",
            "text": unit["text"],
            "provenance": {
                "kind": "knowledge_retrieval_unit",
                "trace_id": ref["trace_id"],
                "unit": unit,
                "scope_digest": trace["scope_digest"],
            },
            "formal_scoring_input": False,
        }
    value = clean(value)
    suffix = f":page:{ref['physical_page']}" if ref.get("physical_page") else ""
    return value | {"source_key": f"{ref['kind']}:{identity}{suffix}", "version": sha(value)}


def freeze(c, selection, project_bid=None):
    bids = selection_bids(c, selection, project_bid)
    lock_scope(c, bids)
    memory_ids = (
        [UUID(str(project_bid))]
        if project_bid
        else sorted({UUID(ref["id"]) for ref in selection if ref["kind"] == "project"}, key=str)
    )
    memory = [project_memory(c, bid) for bid in memory_ids]
    sources = []
    for ref in selection:
        value = resolve(c, ref)
        if value["version"] != ref["version"]:
            raise DomainError(
                "ASSISTANT_SOURCE_CHANGED", "所选来源版本或效力已变化，请重新选择", 412
            )
        sources.append(value | {"selection": ref})
    if len({s["source_key"] for s in sources}) != len(sources):
        raise DomainError("ASSISTANT_DUPLICATE_SOURCE", "请勿重复选择同一来源", 422)
    if (
        sum(len(s["text"]) for s in sources) + len(json.dumps(memory, ensure_ascii=False))
        > MAX_INPUT_CHARACTERS
    ):
        raise DomainError(
            "ASSISTANT_SOURCE_TOO_LARGE",
            "完整所选输入超过单次范围，请选择具体原页或章节；未静默截断",
            422,
        )
    return {
        "projects": memory,
        "sources": sources,
        "target_bid_ids": [str(project_bid)]
        if project_bid
        else sorted(ref["id"] for ref in selection if ref["kind"] == "project"),
        "formal_scoring_input": False,
    }


def revalidate(c, frozen, selection, project_bid=None):
    current = freeze(c, selection, project_bid)
    if current != frozen:
        raise DomainError(
            "ASSISTANT_CONTEXT_CHANGED", "项目、事实、章节或来源依据已变更，保留候选但未应用", 409
        )
    return current


def turn_bids(c, request, project_bid=None):
    bids = selection_bids(c, request["source_selection"], project_bid)
    if request.get("section_id"):
        section = fetch(
            c, "SELECT bid_id FROM sections WHERE id=%s", (UUID(request["section_id"]),)
        )
        targets = (
            {UUID(str(project_bid))}
            if project_bid
            else {UUID(r["id"]) for r in request["source_selection"] if r["kind"] == "project"}
        )
        if section["bid_id"] not in targets:
            raise DomainError(
                "ASSISTANT_TARGET_REQUIRED", "当前章节必须属于明确选择的目标项目", 422
            )
        bids = sorted(set(bids) | {section["bid_id"]}, key=str)
    return bids


def freeze_turn(c, request, project_bid=None):
    lock_scope(c, turn_bids(c, request, project_bid))
    result = freeze(c, request["source_selection"], project_bid)
    if request.get("section_id") and not any(
        s["selection"]["kind"] == "section" and s["selection"]["id"] == request["section_id"]
        for s in result["sources"]
    ):
        ref = {"kind": "section", "id": request["section_id"], "trace_id": None}
        value = resolve(c, ref)
        ref["version"] = value["version"]
        result["sources"].append(
            value | {"selection": ref, "selection_origin": "explicit_current_section"}
        )
    if sum(len(s["text"]) for s in result["sources"]) > MAX_INPUT_CHARACTERS:
        raise DomainError("ASSISTANT_SOURCE_TOO_LARGE", "完整来源与当前章节超过单次范围", 422)
    return result


def revalidate_turn(c, frozen, request, project_bid=None):
    current = freeze_turn(c, request, project_bid)
    if current != frozen:
        raise DomainError(
            "ASSISTANT_CONTEXT_CHANGED", "项目、事实、章节或来源依据已变更，保留候选但未应用", 409
        )
    return current


def assert_generation_context(c, bid, expected):
    if project_memory(c, bid) != expected:
        raise DomainError(
            "AUTHORING_CONTEXT_CHANGED",
            "项目主体、事实依据或结构已变化，未采用生成建议",
            409,
        )


def choices(c, kind, *, bid=None, query=None, trace_id=None, physical_page=None):
    query = (query or "").strip()
    if len(query) > 200:
        raise DomainError("ASSISTANT_INVALID_INPUT", "搜索词过长", 422)
    if kind == "project":
        rows = c.execute(
            "SELECT id,title FROM bids WHERE (%s='' OR strpos(lower(title),lower(%s))>0) ORDER BY created_at DESC,id LIMIT 30",
            (query, query),
        ).fetchall()
    elif kind == "document" and bid:
        rows = c.execute(
            "SELECT id,original_name AS title FROM document_versions WHERE bid_id=%s AND (%s='' OR strpos(lower(original_name),lower(%s))>0) ORDER BY created_at DESC,id LIMIT 30",
            (bid, query, query),
        ).fetchall()
    elif kind == "section" and bid:
        rows = c.execute(
            "SELECT s.id,r.content->'attrs'->>'title' AS title FROM sections s JOIN section_revisions r ON r.section_id=s.id AND r.revision=s.head_revision JOIN bid_profiles p ON p.current_draft_id=s.draft_id AND p.bid_id=s.bid_id WHERE s.bid_id=%s ORDER BY s.id LIMIT 30",
            (bid,),
        ).fetchall()
    elif kind == "knowledge_unit" and trace_id:
        trace = trace_row(c, trace_id)
        if bid and UUID(str(bid)) != trace["bid_id"]:
            raise DomainError("ASSISTANT_SOURCE_SCOPE_INVALID", "检索记录不属于所选项目", 409)
        rows = [{"id": UUID(u["unit_id"]), "title": "知识参考"} for u in trace["selected_units"]]
    else:
        raise DomainError("ASSISTANT_SOURCE_SCOPE_REQUIRED", "请先明确项目或真实检索记录", 422)
    items = []
    refs = [
        {"kind": kind, "id": str(row["id"]), "trace_id": str(trace_id) if trace_id else None}
        for row in rows
    ]
    lock_scope(c, selection_bids(c, refs))
    for row in rows:
        ref = {
            "kind": kind,
            "id": str(row["id"]),
            "version": "0" * 64,
            "trace_id": str(trace_id) if trace_id else None,
        }
        if physical_page is not None:
            ref["physical_page"] = physical_page
        try:
            value = resolve(c, ref)
            ref["version"] = value["version"]
            items.append(
                {
                    "reference": ref,
                    "title": row["title"],
                    "available": True,
                    "reasons": [],
                    "bid_id": value["bid_id"],
                    "formal_scoring_input": False,
                }
            )
        except (DomainError, ValueError) as error:
            items.append(
                {
                    "reference": None,
                    "title": row["title"],
                    "available": False,
                    "reasons": [error.code if isinstance(error, DomainError) else "SOURCE_INVALID"],
                    "bid_id": str(bid) if bid else None,
                    "formal_scoring_input": False,
                }
            )
    return {
        "items": items,
        "coverage": "partial",
        "notice": "仅列本次可发现范围；资料及聊天不进入正式评分",
    }
