"""Idempotent indexing of actual immutable parse evidence with explicit publication."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from packages.rag.core import Embedder, EvidenceText, Tokenizer, chunk_evidence
from packages.rag.repository import Lexical, insert_chunks, insert_embeddings

INDEX_NAMESPACE = UUID("8d6cdd5c-1a28-45c6-ac26-e293b1d16f42")


def ensure_profile(connection, embedder: Embedder) -> UUID:
    encoded = json.dumps(embedder.identity, sort_keys=True)
    profile_id = uuid5(INDEX_NAMESPACE, encoded)
    identity = embedder.identity
    connection.execute(
        """INSERT INTO retrieval.embedding_profiles
           (id,model_name,model_revision,weights_digest,dimensions,distance,normalized,
            preprocessing_digest) VALUES(%s,%s,%s,%s,1024,'cosine',true,%s)
           ON CONFLICT(id) DO NOTHING""",
        (
            profile_id,
            identity["model_name"],
            identity["model_revision"],
            identity["weights_digest"],
            identity["preprocessing_digest"],
        ),
    )
    return profile_id


def index_parse(
    connection,
    bid_id: UUID,
    parse_id: UUID,
    embedder: Embedder,
    tokenizer: Tokenizer,
    lexical: Lexical,
) -> dict:
    """Caller commits atomically; use a restricted Worker connection, never a database owner."""
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    parse = connection.execute(
        """SELECT p.*,v.obtained_at,b.digest AS original_digest FROM design.parse_runs p
           JOIN design.document_versions v ON v.id=p.document_version_id
           JOIN design.blobs b ON b.id=v.blob_id WHERE p.bid_id=%s AND p.id=%s""",
        (bid_id, parse_id),
    ).fetchone()
    if not parse or parse["coverage"] == "failed":
        raise ValueError("No usable original parse for this project")
    blocks = connection.execute(
        """SELECT * FROM design.evidence_blocks WHERE bid_id=%s AND parse_id=%s
           AND kind='text' ORDER BY physical_page,ordinal""",
        (bid_id, parse_id),
    ).fetchall()
    originals = [
        EvidenceText(row["id"], parse_id, row["physical_page"], row["text_content"])
        for row in blocks
        if row["text_content"].strip()
    ]
    chunks = chunk_evidence(originals, tokenizer)
    if not chunks:
        raise ValueError("No readable text chunks; OCR or layout processing is still needed")
    source_id = uuid5(INDEX_NAMESPACE, f"document-parse:{parse_id}")
    official = connection.execute(
        """SELECT EXISTS(SELECT 1 FROM design.material_assignments
           WHERE bid_id=%s AND document_version_id=%s AND role='official_result') AS value""",
        (bid_id, parse["document_version_id"]),
    ).fetchone()["value"]
    policy_class = "official_label" if official else "prebid_content"
    connection.execute(
        """INSERT INTO retrieval.source_nodes
           (id,document_version_id,kind,policy_class,known_at,digest)
           VALUES(%s,%s,'document',%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
        (
            source_id,
            parse["document_version_id"],
            policy_class,
            parse["obtained_at"],
            parse["original_digest"],
        ),
    )
    node = connection.execute(
        "SELECT * FROM retrieval.source_nodes WHERE id=%s",
        (source_id,),
    ).fetchone()
    if (
        node["policy_class"] != policy_class
        or node["document_version_id"] != parse["document_version_id"]
        or node["digest"] != parse["original_digest"]
    ):
        raise ValueError("Existing source has a different immutable identity")
    connection.execute(
        """INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from)
           VALUES(%s,1,'candidate',%s) ON CONFLICT(source_id) DO NOTHING""",
        (source_id, parse["obtained_at"]),
    )
    head = connection.execute(
        "SELECT * FROM retrieval.source_heads WHERE source_id=%s FOR UPDATE",
        (source_id,),
    ).fetchone()
    if head["status"] == "withdrawn":
        raise ValueError("Source was withdrawn; indexing cannot republish it")
    existing = connection.execute(
        "SELECT * FROM retrieval.chunks WHERE parse_id=%s ORDER BY ordinal",
        (parse_id,),
    ).fetchall()
    if not existing:
        insert_chunks(connection, bid_id, source_id, chunks, lexical)
    else:
        if len(existing) != len(chunks) or any(
            row["id"] != chunk.id
            or row["source_id"] != source_id
            or row["digest"] != chunk.digest
            or row["token_count"] != chunk.token_count
            or row["lexemes"] != lexical.lexemes(chunk.text)
            or tuple(row["evidence_ids"]) != chunk.evidence_ids
            for row, chunk in zip(existing, chunks, strict=True)
        ):
            raise ValueError("Parse already has a different chunk method; do not overwrite")
    profile_id = ensure_profile(connection, embedder)
    ready = connection.execute(
        """SELECT e.chunk_id FROM retrieval.embeddings e
           JOIN retrieval.chunks c ON c.id=e.chunk_id WHERE c.source_id=%s AND e.profile_id=%s""",
        (source_id, profile_id),
    ).fetchall()
    ready_ids = {row["chunk_id"] for row in ready}
    missing = [chunk for chunk in chunks if chunk.id not in ready_ids]
    if missing:
        insert_embeddings(connection, profile_id, missing, embedder)
    count = connection.execute(
        """SELECT count(*) AS count FROM retrieval.embeddings e
           JOIN retrieval.chunks c ON c.id=e.chunk_id WHERE c.source_id=%s AND e.profile_id=%s""",
        (source_id, profile_id),
    ).fetchone()["count"]
    if count != len(chunks):
        raise ValueError("Index does not cover its entire expected chunk manifest")
    if head["status"] == "candidate":
        connection.execute(
            "UPDATE retrieval.source_heads SET status='published',revision=revision+1 WHERE source_id=%s",
            (source_id,),
        )
    digest = hashlib.sha256(json.dumps([chunk.digest for chunk in chunks]).encode()).hexdigest()
    return {
        "source_id": source_id,
        "profile_id": profile_id,
        "chunk_count": len(chunks),
        "embedded_count": count,
        "manifest_digest": digest,
        "parse_coverage": parse["coverage"],
        "parse_gaps": parse["gaps"],
        "indexed_scope": "readable_text_blocks_only",
    }


def withdraw_source(connection, bid_id: UUID, source_id: UUID, expected_revision: int) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    source = connection.execute(
        """SELECT n.id FROM retrieval.source_nodes n JOIN design.document_versions v
           ON v.id=n.document_version_id WHERE n.id=%s AND v.bid_id=%s""",
        (source_id, bid_id),
    ).fetchone()
    if not source:
        raise ValueError("Source does not belong to this project")
    row = connection.execute(
        """UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1
           WHERE source_id=%s AND revision=%s RETURNING revision""",
        (source_id, expected_revision),
    ).fetchone()
    if not row:
        raise ValueError("Source revision changed")
