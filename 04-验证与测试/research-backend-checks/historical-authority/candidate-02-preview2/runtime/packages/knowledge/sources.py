"""Knowledge provenance: exact immutable original positions and shared source authority."""

from uuid import UUID

from packages.domain.runtime import DomainError, clean
from packages.rag.indexing import original_source_id, parse_source_id


def lock_graph(c):
    if c.autocommit:
        raise ValueError("Knowledge authority requires a transaction")
    isolation = c.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
    if isolation != "read committed":
        raise ValueError("Knowledge authority requires fresh read-committed authority")
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")


def authority_manifest(c, source_ids, at):
    ids = sorted(set(source_ids), key=str)
    if not ids:
        return []
    rows = c.execute(
        """SELECT a.source_id,n.id AS ancestor_id,n.kind,n.policy_class,n.known_at,n.digest,
       h.revision,h.status,h.valid_from,h.valid_until
       FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id
       LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
       WHERE a.source_id=ANY(%s) ORDER BY a.source_id,n.id""",
        (ids,),
    ).fetchall()
    if {r["source_id"] for r in rows} != set(ids) or any(
        r["status"] != "published"
        or r["known_at"] > at
        or r["valid_from"] > at
        or r["valid_until"] is not None
        and r["valid_until"] <= at
        for r in rows
    ):
        raise DomainError(
            "KNOWLEDGE_SOURCE_INVALID", "来源或祖先已撤回、未生效或过期，不能发布或新增引用", 409
        )
    return clean(rows)


def lock_heads(c, ids):
    c.execute(
        """SELECT h.source_id FROM retrieval.source_heads h WHERE h.source_id IN(
       SELECT ancestor_id FROM retrieval.source_ancestors WHERE source_id=ANY(%s))
       ORDER BY h.source_id FOR UPDATE""",
        (sorted(set(ids), key=str),),
    ).fetchall()


def verify_provenance(c, refs, at):
    lock_graph(c)
    ids = [UUID(ref["source_id"]) for ref in refs]
    lock_heads(c, ids)
    manifest = authority_manifest(c, ids, at)
    originals = []
    for ref in refs:
        if UUID(ref["source_id"]) != parse_source_id(UUID(ref["parse_id"])):
            raise DomainError("KNOWLEDGE_PROVENANCE_INVALID", "来源不是所选解析的受控子来源", 409)
        row = c.execute(
            """SELECT e.text_content,p.bid_id,v.original_name,b.digest,b.byte_size,
          profile.bidder_id,bid.title AS original_project_title,bidder.name AS original_bidder
          FROM design.evidence_blocks e JOIN design.parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id
          JOIN design.pages page ON page.parse_id=e.parse_id AND page.physical_page=e.physical_page AND page.bid_id=e.bid_id
          JOIN design.document_versions v ON v.id=p.document_version_id AND v.bid_id=p.bid_id
          JOIN design.blobs b ON b.id=v.blob_id JOIN design.bids bid ON bid.id=p.bid_id
          JOIN design.bid_profiles profile ON profile.bid_id=p.bid_id JOIN design.bidders bidder ON bidder.id=profile.bidder_id
          JOIN retrieval.source_nodes n ON n.id=%s AND n.kind='document' AND n.document_version_id=v.id AND n.digest=b.digest
          JOIN retrieval.source_heads h ON h.source_id=n.id AND h.revision=%s
          WHERE e.id=%s AND p.id=%s AND p.document_version_id=%s AND e.physical_page=%s
            AND p.coverage<>'failed' AND page.quality<>'failed'
            AND EXISTS(SELECT 1 FROM retrieval.lineage_edges edge
              JOIN retrieval.source_nodes root ON root.id=edge.parent_id
              WHERE edge.child_id=n.id AND root.id=%s AND root.document_version_id=v.id AND root.digest=b.digest)""",
            (
                UUID(ref["source_id"]),
                ref["source_revision"],
                UUID(ref["evidence_id"]),
                UUID(ref["parse_id"]),
                UUID(ref["document_version_id"]),
                ref["physical_page"],
                original_source_id(UUID(ref["document_version_id"])),
            ),
        ).fetchone()
        if not row or ref["excerpt"] not in row["text_content"]:
            raise DomainError(
                "KNOWLEDGE_PROVENANCE_INVALID", "原件、解析、页、证据与摘录不能互相核验", 409
            )
        identity = dict(row)
        identity.pop("text_content")
        originals.append(clean({"source_ref": ref, **identity}))
    policy = (
        "prebid_content"
        if all(r["policy_class"] == "prebid_content" for r in manifest)
        else "derived_mixed"
    )
    return originals, manifest, policy
