"""完整页输入、核验字节与来源闭包；模型调用和对象存储均在业务锁事务外。"""

import json
from datetime import datetime

from packages.domain import storage
from packages.domain.runtime import DomainError, clean, connection, fetch, sha, uid
from packages.rag.indexing import ensure_parse_sources

REVIEW_ROLES = ("body", "tender", "supplement", "drawing", "boq")


def read_bundle(c, bid, material):
    rows = c.execute(
        "SELECT a.document_version_id,a.role,a.ordinal,v.blob_id,v.obtained_at,b.digest,b.object_key,b.byte_size,p.id AS parse_id,p.parser_digest,p.config_digest,p.page_count,p.coverage,p.gaps FROM material_assignments a JOIN document_versions v ON v.bid_id=a.bid_id AND v.id=a.document_version_id JOIN blobs b ON b.id=v.blob_id LEFT JOIN LATERAL(SELECT * FROM parse_runs WHERE document_version_id=v.id ORDER BY created_at DESC,id DESC LIMIT 1)p ON true WHERE a.bid_id=%s AND a.snapshot_id=%s AND a.role=ANY(%s) ORDER BY a.role,a.ordinal",
        (bid, material, list(REVIEW_ROLES)),
    ).fetchall()
    if not rows or not any(r["role"] == "tender" for r in rows):
        raise DomainError("TENDER_SOURCE_REQUIRED", "请先入库本项目招标资料", 409)
    documents = []
    for row in rows:
        if not row["parse_id"]:
            raise DomainError(
                "PARSE_NOT_READY", "资料原页仍在准备，请稍后继续", 409, retryable=True
            )
        if row["coverage"] == "failed":
            raise DomainError("PARSE_FAILED", "必要资料解析失败，请查看原页缺口", 409)
        pages = c.execute(
            "SELECT physical_page,quality,gaps FROM pages WHERE bid_id=%s AND parse_id=%s ORDER BY physical_page",
            (bid, row["parse_id"]),
        ).fetchall()
        blocks = c.execute(
            "SELECT id,physical_page,ordinal,kind,text_content,bbox,structure,digest FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s ORDER BY physical_page,ordinal",
            (bid, row["parse_id"]),
        ).fetchall()
        if len(pages) != row["page_count"] or not pages:
            raise DomainError("PARSE_RANGE_INCOMPLETE", "资料原页范围记录不完整", 409)
        if any(sha(block["text_content"].encode()) != block["digest"] for block in blocks):
            raise DomainError("EVIDENCE_DIGEST_MISMATCH", "页证据内容摘要不符", 409)
        documents.append(dict(row) | {"pages": pages, "blocks": blocks})
    return documents


def verify_bytes(documents):
    for doc in documents:
        raw = storage.get(doc["object_key"])
        if len(raw) != doc["byte_size"] or sha(raw) != doc["digest"]:
            raise DomainError("ORIGINAL_BYTES_CORRUPTED", "本轮原件实际字节核验失败", 409)


def restore_documents(c, frozen):
    documents = []
    for doc in frozen:
        original = fetch(c, "SELECT * FROM blobs WHERE id=%s", (doc["blob_id"],))
        if original["digest"] != doc["digest"] or original["byte_size"] != doc["byte_size"]:
            raise DomainError("REVIEW_ORIGINAL_MISMATCH", "冻结原件身份不一致", 409)
        documents.append(doc | {"object_key": original["object_key"]})
    return documents


def publish_sources(c, bid, documents):
    refs = []
    for doc in documents:
        try:
            _, source = ensure_parse_sources(c, bid, doc["parse_id"], publish_readable=True)
        except ValueError:
            raise DomainError(
                "SOURCE_NOT_EFFECTIVE", "来源已撤回或身份不一致，请核对生效资料", 409
            ) from None
        refs.append(
            {
                "id": str(source),
                "document_version_id": str(doc["document_version_id"]),
                "kind": "body"
                if doc["role"] == "body"
                else "supplement"
                if doc["role"] == "supplement"
                else "tender",
                "digest": doc["digest"],
            }
        )
    return refs


def source_closure(c, sources, *, lock=False):
    ids = [r["id"] for r in sources]
    if lock:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "SELECT h.source_id FROM retrieval.source_heads h WHERE h.source_id IN(SELECT ancestor_id FROM retrieval.source_ancestors WHERE source_id=ANY(%s::uuid[])) ORDER BY h.source_id FOR UPDATE",
            (ids,),
        ).fetchall()
    rows = c.execute(
        "SELECT a.source_id,n.id,n.document_version_id,n.kind,n.policy_class,n.known_at,n.digest,h.revision,h.status,h.valid_from,h.valid_until FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE a.source_id=ANY(%s::uuid[]) ORDER BY a.source_id,n.id",
        (ids,),
    ).fetchall()
    if not rows or {str(row["source_id"]) for row in rows} != set(ids):
        raise DomainError("SOURCE_CLOSURE_INCOMPLETE", "来源祖先范围不完整", 409)
    return clean(rows)


def effective_at(closure, as_of):
    at = (
        as_of
        if isinstance(as_of, datetime)
        else datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    )
    for row in closure:
        if (
            row["status"] != "published"
            or row["policy_class"] != "prebid_content"
            or datetime.fromisoformat(row["known_at"]) > at
            or datetime.fromisoformat(row["valid_from"]) > at
            or row["valid_until"]
            and datetime.fromisoformat(row["valid_until"]) <= at
        ):
            raise DomainError(
                "SOURCE_NOT_EFFECTIVE", "本轮存在尚未生效、已撤回或不适用于事前评审的来源", 409
            )


def check_frozen_sources(c, manifest, evidence, *, lock=False):
    current = source_closure(c, manifest["source_refs"], lock=lock)
    if sha(current) != sha(evidence["source_closure"]):
        raise DomainError(
            "REVIEW_SOURCE_STALE", "冻结来源效力已变更，本轮不能发布为当前有效结果", 409
        )
    effective_at(current, manifest["as_of"])


def save_json(value, *, worker=False):
    raw = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    digest, blob_id = sha(raw), uid()
    key = "blobs/" + digest
    storage.put(key, raw, "application/json")
    with connection(worker) as c:
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/json') ON CONFLICT(digest) DO NOTHING",
            (blob_id, digest, key, len(raw)),
        )
        stored = fetch(c, "SELECT * FROM blobs WHERE digest=%s", (digest,))
    return stored


def read_json_blob(blob):
    raw = storage.get(blob["object_key"])
    if len(raw) != blob["byte_size"] or sha(raw) != blob["digest"]:
        raise DomainError("FROZEN_EVIDENCE_CORRUPTED", "冻结证据清单实际字节核验失败", 409)
    return json.loads(raw)


def frozen_documents(documents):
    # object_key、显示名称和临时URL不是语义身份。原件blob与所有解析证据仍完整可核验。
    return clean([{k: v for k, v in doc.items() if k != "object_key"} for doc in documents])
