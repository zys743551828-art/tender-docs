"""Positive canonical official-source authority; database only, no provider calls."""

from datetime import datetime
from uuid import UUID

from packages.domain.runtime import clean, sha
from packages.rag.indexing import original_source_id, parse_source_id


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def inspect(c, bid, source, *, as_of, cutoff=None, refs=(), lock=False):
    """Both observation time and business known-at must qualify; empty graphs fail closed."""
    if source is None:
        return None, ["official_source_missing"]
    document = c.execute(
        "SELECT v.id,b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id "
        "WHERE v.bid_id=%s AND v.id=%s AND v.artifact_kind='source_original' AND "
        "(EXISTS(SELECT 1 FROM material_assignments a WHERE a.bid_id=v.bid_id AND a.document_version_id=v.id AND a.role='official_result') "
        "OR EXISTS(SELECT 1 FROM document_file_versions fv JOIN document_files f ON f.id=fv.file_id WHERE fv.version_id=v.id AND f.bid_id=v.bid_id AND f.role='official_result'))",
        (bid, source),
    ).fetchone()
    if not document:
        return None, ["source_identity_invalid"]
    root = original_source_id(UUID(str(source)))
    identities = {root}
    evidence = []
    for ref in refs:
        row = c.execute(
            "SELECT e.*,p.document_version_id,p.coverage,pg.quality FROM evidence_blocks e "
            "JOIN parse_runs p ON p.bid_id=e.bid_id AND p.id=e.parse_id "
            "JOIN pages pg ON pg.bid_id=e.bid_id AND pg.parse_id=e.parse_id AND pg.physical_page=e.physical_page "
            "WHERE e.bid_id=%s AND e.id=%s",
            (bid, ref["evidence_id"]),
        ).fetchone()
        if (
            row is None
            or str(row["document_version_id"]) != str(source)
            or ref["document_version_id"] != str(source)
            or row["physical_page"] != ref["physical_page"]
            or row["coverage"] == "failed"
            or row["quality"] != "usable"
            or row["kind"] != "text"
            or sha(row["text_content"].encode()) != row["digest"]
            or not ref["quote"]
            or ref["quote"] not in row["text_content"]
        ):
            return None, ["source_evidence_invalid"]
        parsed = parse_source_id(row["parse_id"])
        if not c.execute(
            "SELECT 1 FROM retrieval.lineage_edges WHERE child_id=%s AND parent_id=%s",
            (parsed, root),
        ).fetchone():
            return None, ["source_parse_unregistered"]
        identities.add(parsed)
        evidence.append(
            {
                "id": str(row["id"]),
                "parse_id": str(row["parse_id"]),
                "digest": row["digest"],
                "physical_page": row["physical_page"],
                "quote_digest": sha(ref["quote"].encode()),
            }
        )
    ids = sorted(identities, key=str)
    if lock:
        # Caller holds bid then graph advisory lock. RC sees the latest committed heads.
        c.execute(
            "SELECT source_id FROM retrieval.source_heads WHERE source_id IN "
            "(SELECT ancestor_id FROM retrieval.source_ancestors WHERE source_id=ANY(%s::uuid[])) ORDER BY source_id FOR UPDATE",
            (ids,),
        ).fetchall()
    rows = c.execute(
        "SELECT a.source_id,n.id,n.document_version_id,n.kind,n.policy_class,n.known_at,n.digest,h.revision,h.status,h.valid_from,h.valid_until "
        "FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id "
        "LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE a.source_id=ANY(%s::uuid[]) ORDER BY a.source_id,n.id",
        (ids,),
    ).fetchall()
    if {r["source_id"] for r in rows} != identities:
        return None, ["source_unregistered"]
    present = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    at, boundary = instant(as_of), instant(cutoff or as_of)
    known_cutoff = min(present, at, boundary)
    effective_from, effective_until = min(present, at), max(present, at)
    for row in rows:
        if (
            row["kind"] != "document"
            or row["policy_class"] != "official_label"
            or row["document_version_id"] is None
        ):
            return None, ["source_ancestor_identity_invalid"]
        ancestor = c.execute(
            "SELECT b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s AND v.bid_id=%s AND v.artifact_kind='source_original' AND "
            "(EXISTS(SELECT 1 FROM material_assignments a WHERE a.bid_id=v.bid_id AND a.document_version_id=v.id AND a.role='official_result') "
            "OR EXISTS(SELECT 1 FROM document_file_versions fv JOIN document_files f ON f.id=fv.file_id WHERE fv.version_id=v.id AND f.bid_id=v.bid_id AND f.role='official_result'))",
            (row["document_version_id"], bid),
        ).fetchone()
        canonical = {original_source_id(row["document_version_id"])} | {
            parse_source_id(p["id"])
            for p in c.execute(
                "SELECT id FROM parse_runs WHERE bid_id=%s AND document_version_id=%s",
                (bid, row["document_version_id"]),
            ).fetchall()
        }
        if not ancestor or ancestor["digest"] != row["digest"] or row["id"] not in canonical:
            return None, ["source_ancestor_identity_invalid"]
        original = original_source_id(row["document_version_id"])
        if (
            row["id"] != original
            and not c.execute(
                "SELECT 1 FROM retrieval.lineage_edges WHERE child_id=%s AND parent_id=%s",
                (row["id"], original),
            ).fetchone()
        ):
            return None, ["source_parse_unregistered"]
        if row["id"] in identities and (
            row["document_version_id"] != document["id"] or row["digest"] != document["digest"]
        ):
            return None, ["source_identity_invalid"]
        if (
            row["status"] != "published"
            or row["revision"] is None
            or row["known_at"] > known_cutoff
            or row["valid_from"] is None
            or row["valid_from"] > effective_from
            or row["valid_until"] is not None
            and row["valid_until"] <= effective_until
        ):
            return None, ["source_no_longer_effective"]
    edges = c.execute(
        "SELECT child_id,parent_id FROM retrieval.lineage_edges WHERE child_id IN "
        "(SELECT ancestor_id FROM retrieval.source_ancestors WHERE source_id=ANY(%s::uuid[])) ORDER BY child_id,parent_id",
        (ids,),
    ).fetchall()
    authority = clean(
        {
            "schema": "official-source-authority/v2",
            "root_id": root,
            "heads": rows,
            "edges": edges,
            "evidence": sorted(evidence, key=lambda r: r["id"]),
        }
    )
    return {"digest": sha(authority), "snapshot": authority}, []


def outcome_reasons(c, outcome, as_of):
    """Technical outcome projections use the same authority, including decision evidence IDs."""
    decision = c.execute(
        "SELECT d.evidence_ids FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.object_kind='outcome' AND h.object_id=%s",
        (outcome["id"],),
    ).fetchone()
    if not decision or not decision["evidence_ids"]:
        return ["source_evidence_missing"]
    refs = []
    for identity in decision["evidence_ids"]:
        row = c.execute(
            "SELECT e.*,p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id WHERE e.bid_id=%s AND e.id=%s",
            (outcome["bid_id"], identity),
        ).fetchone()
        if not row:
            return ["source_evidence_invalid"]
        refs.append(
            clean(
                {
                    "evidence_id": row["id"],
                    "document_version_id": row["document_version_id"],
                    "physical_page": row["physical_page"],
                    "quote": row["text_content"],
                }
            )
        )
    return inspect(
        c,
        outcome["bid_id"],
        outcome["source_version_id"],
        as_of=as_of,
        cutoff=outcome["known_at"],
        refs=refs,
    )[1]
