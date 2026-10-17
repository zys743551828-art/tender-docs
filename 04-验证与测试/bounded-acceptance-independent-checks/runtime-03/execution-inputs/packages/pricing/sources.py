"""本项目报价公式来源与祖先效力；调用者在短事务持bid→graph锁。"""

from uuid import UUID

from packages.domain.runtime import DomainError, clean, etag, fetch, now, sha
from packages.rag.indexing import original_source_id, parse_source_id


def enabled_bid(c, bid, *, lock=False):
    row = fetch(c, "SELECT b.*,p.current_material_id,p.round_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s" + (" FOR UPDATE OF b" if lock else ""), (bid,))
    if row["settings"].get("pricing", True) is False:
        raise DomainError("PRICING_DISABLED", "项目报价模块已关闭，可在项目设置中开启；历史资料已保留", 403, {"settings_url": f"/bids/{bid}/settings"})
    return row


def pricing_scope(c, bid):
    row = enabled_bid(c, bid)
    material = row["current_material_id"]
    documents = c.execute("SELECT a.role,a.document_version_id,b.digest FROM material_assignments a JOIN document_versions d ON d.id=a.document_version_id JOIN blobs b ON b.id=d.blob_id WHERE a.bid_id=%s AND a.snapshot_id=%s AND a.role IN ('tender','supplement') ORDER BY a.role,a.document_version_id", (bid, material)).fetchall() if material else []
    return {"bid_id": str(bid), "round_id": str(row["round_id"]), "effective_documents": clean(documents)}


def context(c, bid):
    row = enabled_bid(c, bid)
    scope = pricing_scope(c, bid)
    versions = [UUID(item["document_version_id"]) for item in scope["effective_documents"]]
    parses = c.execute("SELECT DISTINCT ON(document_version_id) id,document_version_id FROM parse_runs WHERE bid_id=%s AND document_version_id=ANY(%s::uuid[]) ORDER BY document_version_id,created_at DESC,id DESC", (bid, versions)).fetchall()
    ids = [identity for parse in parses for identity in (parse_source_id(parse["id"]), original_source_id(parse["document_version_id"]))]
    heads = c.execute("SELECT n.id,n.digest,h.revision,h.status,h.valid_from,h.valid_until FROM retrieval.source_nodes n LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE n.id=ANY(%s::uuid[]) ORDER BY n.id", (ids,)).fetchall()
    value = {"bid_id": str(bid), "material_snapshot_id": str(row["current_material_id"]) if row["current_material_id"] else None, "material_revision": row["material_revision"], "scope": scope, "parses": clean(parses), "source_heads": clean(heads)}
    return value, etag("pricing-context", bid, sha(value))


def parent_heads(c, source_ids):
    return clean(c.execute("SELECT a.source_id,n.id,n.document_version_id,n.kind,n.policy_class,n.known_at,n.digest,h.revision,h.status,h.valid_from,h.valid_until,d.bid_id FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN document_versions d ON d.id=n.document_version_id WHERE a.source_id=ANY(%s::uuid[]) ORDER BY a.source_id,n.id", (source_ids,)).fetchall())


def exact_sources(c, *, bid, quotes):
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    scope = pricing_scope(c, bid)
    allowed = {item["document_version_id"] for item in scope["effective_documents"]}
    ids = []
    seen = set()
    for quote in quotes:
        if quote["evidence_id"] in seen:
            raise DomainError("PRICING_DUPLICATE_EVIDENCE", "相同原页证据请合并支持字段后选择一次", 422)
        seen.add(quote["evidence_id"])
        if str(quote["document_version_id"]) not in allowed or UUID(str(quote["source_id"])) != parse_source_id(UUID(str(quote["parse_id"]))):
            raise DomainError("PRICING_SOURCE_INVALID", "报价依据不属于本项目生效招标补遗或精确解析版本", 409)
        row = c.execute("SELECT e.text_content,p.document_version_id,n.digest,h.revision,b.digest AS original_digest FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN document_versions d ON d.id=p.document_version_id JOIN blobs b ON b.id=d.blob_id JOIN retrieval.source_nodes n ON n.id=%s JOIN retrieval.source_heads h ON h.source_id=n.id WHERE e.bid_id=%s AND e.id=%s AND e.parse_id=%s AND e.physical_page=%s AND p.document_version_id=%s AND n.kind='document' AND n.document_version_id=d.id AND n.digest=b.digest AND h.revision=%s AND p.id=(SELECT newest.id FROM parse_runs newest WHERE newest.document_version_id=p.document_version_id ORDER BY newest.created_at DESC,newest.id DESC LIMIT 1) AND EXISTS(SELECT 1 FROM pages page WHERE page.bid_id=e.bid_id AND page.parse_id=e.parse_id AND page.physical_page=e.physical_page AND page.quality<>'failed') AND EXISTS(SELECT 1 FROM retrieval.lineage_edges edge JOIN retrieval.source_nodes ancestor ON ancestor.id=edge.parent_id WHERE edge.child_id=n.id AND ancestor.id=%s AND ancestor.kind='document' AND ancestor.document_version_id=d.id AND ancestor.digest=b.digest)", (quote["source_id"], bid, quote["evidence_id"], quote["parse_id"], quote["physical_page"], quote["document_version_id"], quote["source_revision"], original_source_id(UUID(str(quote["document_version_id"]))))).fetchone()
        if row is None:
            raise DomainError("PRICING_SOURCE_INVALID", "原文、页码、解析、来源版本或当前效力不匹配，请重新选择依据", 409)
        if not quote["quote"].strip() or quote["quote"].strip() != row["text_content"].strip():
            raise DomainError("PRICING_QUOTE_INVALID", "请引用完整可读原页证据，不能省掉同段的条件或例外", 422)
        ids.append(UUID(str(quote["source_id"])))
    heads = parent_heads(c, sorted(set(ids)))
    instant = now()
    for head in heads:
        from datetime import datetime

        def parsed(value):
            return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))

        if head["bid_id"] != str(bid) or head["document_version_id"] not in allowed or head["kind"] != "document" or head["policy_class"] != "prebid_content" or head["status"] != "published" or parsed(head["known_at"]) > instant or parsed(head["valid_from"]) is None or parsed(head["valid_from"]) > instant or (parsed(head["valid_until"]) is not None and parsed(head["valid_until"]) <= instant):
            raise DomainError("PRICING_SOURCE_STALE", "报价依据或祖先已撤回、过期、用途/项目范围不符，请核对原页", 409)
    if not heads:
        raise DomainError("PRICING_SOURCE_STALE", "报价依据来源图尚未就绪", 409)
    return {"scope": scope, "ancestor_heads": heads}


def source_choices(c, bid, *, limit=50, cursor=None):
    enabled_bid(c, bid)
    scope = pricing_scope(c, bid)
    versions = [UUID(item["document_version_id"]) for item in scope["effective_documents"]]
    rows = c.execute("SELECT e.*,p.document_version_id,d.original_name FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN document_versions d ON d.id=p.document_version_id WHERE e.bid_id=%s AND p.document_version_id=ANY(%s::uuid[]) AND p.id=(SELECT newest.id FROM parse_runs newest WHERE newest.document_version_id=p.document_version_id ORDER BY newest.created_at DESC,newest.id DESC LIMIT 1) AND (%s::uuid IS NULL OR e.id>%s) ORDER BY e.id LIMIT %s", (bid, versions, cursor, cursor, limit + 1)).fetchall()
    choices = []
    for row in rows[:limit]:
        source = c.execute("SELECT revision FROM retrieval.source_heads WHERE source_id=%s AND status='published'", (parse_source_id(row["parse_id"]),)).fetchone()
        choices.append({"source_id": str(parse_source_id(row["parse_id"])), "source_revision": source["revision"] if source else None, "document_version_id": str(row["document_version_id"]), "parse_id": str(row["parse_id"]), "physical_page": row["physical_page"], "evidence_id": str(row["id"]), "filename": row["original_name"], "quote": row["text_content"], "source_registered": source is not None})
    return {"items": choices, "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}
