"""Knowledge candidates, immutable versions, manual publication and impact journals."""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, etag, fetch, match, now, sha
from packages.knowledge.content import ids, instant, text, validate_version
from packages.knowledge.sources import authority_manifest, lock_graph, verify_provenance


def version_row(c, knowledge_id, version_id=None):
    return fetch(
        c,
        """SELECT v.*,i.title AS item_title,i.revision AS item_revision,i.published_version_id,
      n.known_at AS created_at,h.status,h.revision AS source_revision,h.valid_from,h.valid_until
      FROM retrieval.knowledge_items i JOIN retrieval.knowledge_versions v ON v.item_id=i.id
      JOIN retrieval.source_nodes n ON n.id=v.source_id JOIN retrieval.source_heads h ON h.source_id=n.id
      WHERE i.id=%s AND (%s::uuid IS NULL OR v.id=%s::uuid) ORDER BY v.version_no DESC LIMIT 1""",
        (knowledge_id, version_id, version_id),
    )


def lock_item(c, knowledge_id):
    initial = version_row(c, knowledge_id)
    origin = UUID(initial["applicability"]["origin_bid_id"])
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (origin,))
    lock_graph(c)
    row = fetch(
        c, "SELECT * FROM retrieval.knowledge_items WHERE id=%s FOR NO KEY UPDATE", (knowledge_id,)
    )
    return row, origin


def append_decision(c, row, actor, decision, reason, evidence_ids):
    origin = UUID(row["applicability"]["origin_bid_id"])
    identity = uuid4()
    c.execute(
        """INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id)
       VALUES(%s,%s,'knowledge_version',%s,%s,%s,%s,%s,%s)""",
        (identity, origin, row["id"], row["version_no"], decision, reason, evidence_ids, actor),
    )
    c.execute(
        """INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id)
       VALUES(%s,'knowledge_version',%s,1,%s) ON CONFLICT(bid_id,object_kind,object_id)
       DO UPDATE SET revision=decision_heads.revision+1,decision_id=excluded.decision_id""",
        (origin, row["id"], identity),
    )
    return identity


def last_decision(c, row):
    return c.execute(
        """SELECT d.* FROM decision_heads h JOIN decisions d ON d.id=h.decision_id
       WHERE h.bid_id=%s AND h.object_kind='knowledge_version' AND h.object_id=%s""",
        (UUID(row["applicability"]["origin_bid_id"]), row["id"]),
    ).fetchone()


def lifecycle(c, row, at):
    decision = last_decision(c, row)
    if row["status"] == "withdrawn":
        return "superseded" if decision and decision["decision"] == "superseded" else "withdrawn"
    if row["applicability"].get("readiness") != "complete":
        return "candidate"
    if row["valid_until"] is not None and row["valid_until"] <= at:
        return "expired"
    try:
        parents = authority_manifest(
            c,
            [UUID(ref["source_id"]) for ref in row["applicability"]["source_refs"]],
            at,
        )
        if parents != row["applicability"]["publication_basis"]:
            return "source_invalid"
    except DomainError:
        return "source_invalid"
    if row["status"] == "candidate":
        return (
            "pending_confirmation" if decision and decision["decision"] == "submit" else "candidate"
        )
    try:
        authority_manifest(c, [row["source_id"]], at)
    except DomainError:
        if row["valid_from"] > at:
            return "not_yet_effective"
        return "source_invalid"
    return "published"


def view(c, knowledge_id, version_id=None, at=None):
    at = at or now()
    row = version_row(c, knowledge_id, version_id)
    meta = row["applicability"]
    workflow = lifecycle(c, row, at)
    published = c.execute(
        """SELECT actor_id,created_at,reason FROM decisions WHERE object_kind='knowledge_version'
      AND object_id=%s AND decision='publish' ORDER BY created_at DESC,id DESC LIMIT 1""",
        (row["id"],),
    ).fetchone()
    published_workflow = (
        lifecycle(c, version_row(c, knowledge_id, row["published_version_id"]), at)
        if row["published_version_id"]
        else None
    )
    return clean(
        {
            "id": knowledge_id,
            "version_id": row["id"],
            "revision": row["item_revision"],
            "title": meta.get("title", row["item_title"]),
            "state": "candidate"
            if workflow in ["candidate", "pending_confirmation"]
            else "published"
            if workflow in ["published", "not_yet_effective"]
            else "withdrawn",
            "content": row["content"],
            "categories": row["categories"],
            "source_ids": list(dict.fromkeys(r["source_id"] for r in meta.get("source_refs", []))),
            "source_impact": workflow in ["withdrawn", "superseded", "expired", "source_invalid"],
            "workflow": workflow,
            "version_no": row["version_no"],
            "knowledge_source_id": row["source_id"],
            "source_revision": row["source_revision"],
            "published_version_id": row["published_version_id"],
            "published_workflow": published_workflow,
            "scope": meta["scope"],
            "applicability": meta.get("rules"),
            "source_refs": meta.get("source_refs", []),
            "provenance": meta.get("originals", []),
            "declared_source_version_ids": meta.get("declared_source_version_ids", []),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "confirmed_by": published["actor_id"] if published else None,
            "confirmed_at": published["created_at"] if published else None,
            "reference_notice": "仅作为有出处的历史参考；本项目事实须另行核验确认，不自动带入。",
        }
    )


def insert_version(
    c, item_id, version_no, actor, content, categories, metadata, parents, policy, at
):
    version, source = uuid4(), uuid4()
    rules = metadata.get("rules")
    start = instant(rules["valid_from"]) if rules else at
    until = instant(rules["valid_until"]) if rules and rules["valid_until"] is not None else None
    c.execute(
        "INSERT INTO retrieval.source_nodes(id,kind,policy_class,known_at,digest) VALUES(%s,'knowledge',%s,%s,%s)",
        (
            source,
            policy,
            at,
            sha({"content": content, "categories": categories, "metadata": metadata}),
        ),
    )
    c.execute(
        "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from,valid_until) VALUES(%s,1,'candidate',%s,%s)",
        (source, start, until),
    )
    for parent in sorted(set(parents), key=str):
        c.execute(
            "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
            (source, parent),
        )
    c.execute(
        """INSERT INTO retrieval.knowledge_versions(id,item_id,version_no,source_id,content,categories,applicability,created_by)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
        (version, item_id, version_no, source, content, categories, Jsonb(metadata), actor),
    )
    return version


def create_candidate(c, actor, body):
    text(body["title"], 200)
    text(body["content"])
    text(body["scope"], 2000)
    source_versions = ids(body["source_version_ids"], 1, 100)
    rows = c.execute(
        "SELECT id,bid_id FROM document_versions WHERE id=ANY(%s) ORDER BY id", (source_versions,)
    ).fetchall()
    if len(rows) != len(source_versions):
        raise DomainError("KNOWLEDGE_ORIGINAL_MISSING", "候选来源原件不存在，不能编造来源", 404)
    origin = rows[0]["bid_id"]
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (origin,))
    lock_graph(c)
    item = uuid4()
    c.execute(
        "INSERT INTO retrieval.knowledge_items(id,title,revision) VALUES(%s,%s,1)",
        (item, body["title"]),
    )
    metadata = {
        "readiness": "needs_applicability",
        "title": body["title"],
        "scope": body["scope"],
        "origin_bid_id": str(origin),
        "declared_source_version_ids": [str(v) for v in source_versions],
        "source_refs": [],
        "originals": [],
    }
    version = insert_version(
        c, item, 1, actor, body["content"], [], metadata, [], "derived_mixed", now()
    )
    audit(
        c,
        actor,
        origin,
        "knowledge.candidate_created",
        {"knowledge_id": item, "version_id": version},
    )
    return view(c, item)


def add_version(c, knowledge_id, actor, body, condition):
    validate_version(body)
    item, origin = lock_item(c, knowledge_id)
    match_item(c, knowledge_id, condition)
    originals, manifest, policy = verify_provenance(c, body["source_refs"], now())
    rules = body["applicability"]
    bidder_ids = ids(rules["bidder_ids"], 1)
    bidders = c.execute("SELECT id FROM bidders WHERE id=ANY(%s)", (bidder_ids,)).fetchall()
    if len(bidders) != len(bidder_ids):
        raise DomainError("KNOWLEDGE_SUBJECT_UNKNOWN", "适用主体尚未建立，请先核对主体身份", 409)
    project_ids = ids(rules["bid_ids"])
    allowed = c.execute(
        "SELECT bid_id FROM bid_profiles WHERE bid_id=ANY(%s) AND bidder_id=ANY(%s)",
        (project_ids, bidder_ids),
    ).fetchall()
    if len(allowed) != len(project_ids):
        raise DomainError("KNOWLEDGE_SCOPE_CONFLICT", "指定项目不属于所声明主体", 409)
    if "company_fact" in body["categories"] and (
        len(bidder_ids) != 1
        or policy != "prebid_content"
        or {o["bidder_id"] for o in originals} != set(map(str, bidder_ids))
    ):
        raise DomainError(
            "KNOWLEDGE_SUBJECT_CONFLICT", "其他企业或事后标签不能发布为本公司可用事实", 409
        )
    previous = version_row(c, knowledge_id)
    metadata = {
        "readiness": "complete",
        "title": body["title"],
        "scope": body["scope"],
        "origin_bid_id": str(origin),
        "rules": rules,
        "source_refs": body["source_refs"],
        "originals": originals,
        "declared_source_version_ids": list(
            dict.fromkeys(r["document_version_id"] for r in body["source_refs"])
        ),
        "publication_basis": manifest,
    }
    version = insert_version(
        c,
        knowledge_id,
        previous["version_no"] + 1,
        actor,
        body["content"],
        body["categories"],
        metadata,
        [UUID(r["source_id"]) for r in body["source_refs"]],
        policy,
        now(),
    )
    c.execute(
        "UPDATE retrieval.knowledge_items SET title=%s,revision=revision+1 WHERE id=%s",
        (body["title"], knowledge_id),
    )
    audit(
        c,
        actor,
        origin,
        "knowledge.version_created",
        {
            "knowledge_id": knowledge_id,
            "version_id": version,
            "prior_published_version_id": item["published_version_id"],
        },
    )
    return view(c, knowledge_id)


def transition(c, knowledge_id, actor, body, condition, action):
    item, origin = lock_item(c, knowledge_id)
    match_item(c, knowledge_id, condition)
    row = version_row(c, knowledge_id)
    if action == "withdraw" and item["published_version_id"]:
        row = version_row(c, knowledge_id, item["published_version_id"])
    text(body["reason"], 4000)
    if body["decision"] != action:
        raise DomainError("KNOWLEDGE_DECISION_INVALID", "决定与当前知识动作不一致", 422)
    evidence = ids(body["evidence_ids"])
    permitted = {UUID(r["evidence_id"]) for r in row["applicability"].get("source_refs", [])}
    if not set(evidence) <= permitted:
        raise DomainError("KNOWLEDGE_PROVENANCE_INVALID", "确认依据不属于这个知识版本", 409)
    state = lifecycle(c, row, now())
    if action in ["submit", "publish"]:
        if row["applicability"].get("readiness") != "complete":
            raise DomainError(
                "KNOWLEDGE_NOT_READY", "请先补齐主体、适用条件、期限依据及精确出处；候选已保留", 409
            )
        if state not in ["candidate", "pending_confirmation"]:
            raise DomainError(
                "KNOWLEDGE_VERSION_NOT_REPUBLISHABLE",
                "停用、失效或已发布版本不能直接重新发布，请建立新版本",
                409,
            )
        if row["applicability"]["rules"]["validity_kind"] == "unknown":
            raise DomainError("KNOWLEDGE_VALIDITY_UNKNOWN", "有效性尚未核实，不能发布或送确认", 409)
        _, manifest, _ = verify_provenance(c, row["applicability"]["source_refs"], now())
        if manifest != row["applicability"]["publication_basis"]:
            raise DomainError(
                "KNOWLEDGE_SOURCE_CHANGED", "确认期间来源或祖先版本变化，请建立经复核的新版本", 409
            )
    if action == "publish":
        if state != "pending_confirmation":
            raise DomainError(
                "KNOWLEDGE_CONFIRMATION_REQUIRED", "请先核对版本并送确认，成员明确确认后发布", 409
            )
        if item["published_version_id"] and item["published_version_id"] != row["id"]:
            old = version_row(c, knowledge_id, item["published_version_id"])
            c.execute(
                "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
                (old["source_id"],),
            )
            append_decision(c, old, actor, "superseded", "由明确确认的新知识版本替代", [])
        c.execute(
            "UPDATE retrieval.source_heads SET status='published',revision=revision+1 WHERE source_id=%s",
            (row["source_id"],),
        )
        c.execute(
            "UPDATE retrieval.knowledge_items SET published_version_id=%s WHERE id=%s",
            (row["id"], knowledge_id),
        )
    elif action == "withdraw":
        if state in ["withdrawn", "superseded"]:
            raise DomainError(
                "KNOWLEDGE_ALREADY_WITHDRAWN", "该知识版本已停用，旧引用保持历史身份", 409
            )
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (row["source_id"],),
        )
    elif action != "submit":
        raise DomainError("KNOWLEDGE_DECISION_INVALID", "知识动作不支持", 422)
    decision = append_decision(c, row, actor, action, body["reason"], evidence)
    c.execute(
        "UPDATE retrieval.knowledge_items SET revision=revision+1 WHERE id=%s", (knowledge_id,)
    )
    audit(
        c,
        actor,
        origin,
        "knowledge." + action,
        {"knowledge_id": knowledge_id, "version_id": row["id"], "decision_id": decision},
    )
    return view(c, knowledge_id)


def knowledge_etag(representation):
    return etag("knowledge", representation["id"], sha(representation))


def match_item(c, knowledge_id, condition):
    representation = view(c, knowledge_id)
    match(condition, "knowledge", knowledge_id, sha(representation))


def search(c, query, after, limit, bid=None, purpose="assist", state="published"):
    from packages.knowledge.content import PURPOSES

    if purpose not in PURPOSES:
        raise DomainError("KNOWLEDGE_PURPOSE_FORBIDDEN", "知识目录不能作为正式评分来源入口", 422)
    if state not in ["published", "all"]:
        raise DomainError("INVALID_REQUEST", "请选择已发布知识或维护目录", 422)
    if query is not None:
        text(query, 200)
    selected = []
    if bid is not None:
        from packages.knowledge.bridge import eligible_knowledge_sources

        scope = eligible_knowledge_sources(c, bid, purpose, now())
        selected = [UUID(r["version_id"]) for r in scope["eligible"]]
    else:
        items = c.execute(
            "SELECT id,published_version_id FROM retrieval.knowledge_items ORDER BY id"
        ).fetchall()
        for item in items:
            if state == "all":
                selected.append(version_row(c, item["id"])["id"])
            elif item["published_version_id"]:
                row = version_row(c, item["id"], item["published_version_id"])
                if lifecycle(c, row, now()) == "published":
                    selected.append(row["id"])
    rows = c.execute(
        """SELECT v.item_id,v.id FROM retrieval.knowledge_versions v
       WHERE v.id=ANY(%s) AND (%s::uuid IS NULL OR v.item_id>%s::uuid)
         AND (%s::text IS NULL OR strpos(lower(v.content||' '||coalesce(v.applicability->>'title','')),lower(%s))>0)
       ORDER BY v.item_id LIMIT %s""",
        (selected, after, after, query, query, limit + 1),
    ).fetchall()
    return {
        "items": [view(c, r["item_id"], r["id"]) for r in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["item_id"]) if len(rows) > limit else None,
        "has_more": len(rows) > limit,
    }
