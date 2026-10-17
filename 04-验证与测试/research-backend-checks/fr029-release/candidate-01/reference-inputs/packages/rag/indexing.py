"""Prepare and compute outside graph locks, then publish a verified immutable index."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid5

from packages.rag.core import Chunk, Embedder, EvidenceText, Tokenizer, chunk_evidence
from packages.rag.repository import Lexical, assert_embedding_profile, insert_chunks

INDEX_NAMESPACE = UUID("8d6cdd5c-1a28-45c6-ac26-e293b1d16f42")


def parse_source_id(parse_id: UUID) -> UUID:
    return uuid5(INDEX_NAMESPACE, f"document-parse:{UUID(str(parse_id))}")


def original_source_id(document_version_id: UUID) -> UUID:
    return uuid5(INDEX_NAMESPACE, f"document-original:{UUID(str(document_version_id))}")


@dataclass(frozen=True)
class IndexInput:
    bid_id: UUID
    parse_id: UUID
    document_version_id: UUID
    original_digest: str
    blocks: tuple[EvidenceText, ...]


@dataclass(frozen=True)
class PreparedIndex:
    bid_id: UUID
    parse_id: UUID
    document_version_id: UUID
    original_digest: str
    chunks: tuple[Chunk, ...]
    lexical_texts: tuple[str, ...]


@dataclass(frozen=True)
class ComputedIndex:
    prepared: PreparedIndex
    embedding_identity_json: str
    vectors: tuple[tuple[float, ...], ...]


def read_parse(connection, bid_id, parse_id):
    row = connection.execute(
        """SELECT p.*,v.obtained_at,b.digest AS original_digest FROM design.parse_runs p
           JOIN design.document_versions v ON v.id=p.document_version_id
           JOIN design.blobs b ON b.id=v.blob_id WHERE p.bid_id=%s AND p.id=%s""",
        (bid_id, parse_id),
    ).fetchone()
    if not row or row["coverage"] == "failed":
        raise ValueError("No usable original parse for this project")
    return row


def read_index_input(connection, bid_id: UUID, parse_id: UUID) -> IndexInput:
    """Copy immutable parse inputs only. No tokenizer, lexical or embedding RPC here."""
    parse = read_parse(connection, bid_id, parse_id)
    rows = connection.execute(
        """SELECT * FROM design.evidence_blocks WHERE bid_id=%s AND parse_id=%s
           AND kind='text' ORDER BY physical_page,ordinal""",
        (bid_id, parse_id),
    ).fetchall()
    blocks = tuple(
        EvidenceText(row["id"], parse_id, row["physical_page"], row["text_content"])
        for row in rows
        if row["text_content"].strip()
    )
    return IndexInput(
        bid_id, parse_id, parse["document_version_id"], parse["original_digest"], blocks
    )


def prepare_index(raw: IndexInput, tokenizer: Tokenizer, lexical: Lexical) -> PreparedIndex:
    """Tokenize and segment after the input-read connection has been closed."""
    chunks = chunk_evidence(list(raw.blocks), tokenizer)
    if not chunks:
        raise ValueError("No readable text chunks; OCR or layout processing is still needed")
    return PreparedIndex(
        raw.bid_id,
        raw.parse_id,
        raw.document_version_id,
        raw.original_digest,
        tuple(chunks),
        tuple(lexical.lexemes(chunk.text) for chunk in chunks),
    )


def compute_index(prepared: PreparedIndex, embedder: Embedder) -> ComputedIndex:
    """CPU/HTTP model work has no database connection, transaction or source-graph lock."""
    from packages.rag.core import normalized_vector

    vectors = embedder.embed([chunk.text for chunk in prepared.chunks])
    if len(vectors) != len(prepared.chunks):
        raise ValueError("Embedding response does not cover the chunk manifest")
    return ComputedIndex(
        prepared,
        json.dumps(embedder.identity, sort_keys=True),
        tuple(tuple(normalized_vector(vector)) for vector in vectors),
    )


def ensure_profile(connection, identity: dict[str, str]) -> UUID:
    profile_id = uuid5(INDEX_NAMESPACE, json.dumps(identity, sort_keys=True))
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


def ensure_parse_sources(connection, bid_id: UUID, parse_id: UUID, *, publish_readable=False):
    """Register stable original ancestry; readable publication is separate from vector readiness."""
    if connection.autocommit:
        raise ValueError("Source registration requires a transaction")
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    parse = read_parse(connection, bid_id, parse_id)
    source_id = parse_source_id(parse_id)
    root_id = original_source_id(parse["document_version_id"])
    official = connection.execute(
        """SELECT EXISTS(SELECT 1 FROM design.material_assignments
           WHERE bid_id=%s AND document_version_id=%s AND role='official_result') AS value""",
        (bid_id, parse["document_version_id"]),
    ).fetchone()["value"]
    policy = "official_label" if official else "prebid_content"
    for identity in (root_id, source_id):
        connection.execute(
            """INSERT INTO retrieval.source_nodes
               (id,document_version_id,kind,policy_class,known_at,digest)
               VALUES(%s,%s,'document',%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
            (
                identity,
                parse["document_version_id"],
                policy,
                parse["obtained_at"],
                parse["original_digest"],
            ),
        )
        node = connection.execute(
            "SELECT * FROM retrieval.source_nodes WHERE id=%s", (identity,)
        ).fetchone()
        if (
            node["document_version_id"] != parse["document_version_id"]
            or node["policy_class"] != policy
            or node["digest"] != parse["original_digest"]
        ):
            raise ValueError("Existing source has a different immutable identity")
        connection.execute(
            """INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from)
               VALUES(%s,1,'candidate',%s) ON CONFLICT(source_id) DO NOTHING""",
            (identity, parse["obtained_at"]),
        )
        head = connection.execute(
            "SELECT * FROM retrieval.source_heads WHERE source_id=%s FOR UPDATE",
            (identity,),
        ).fetchone()
        if head["status"] == "withdrawn":
            raise ValueError("Original or parse source was withdrawn; indexing cannot republish it")
    connection.execute(
        "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (source_id, root_id),
    )
    if publish_readable:
        for node_id in (root_id, source_id):
            connection.execute(
                """UPDATE retrieval.source_heads SET status='published',revision=revision+1
                   WHERE source_id=%s AND status='candidate'""",
                (node_id,),
            )
    return root_id, source_id


def publish_index(connection, computed: ComputedIndex) -> dict:
    """Short Worker transaction; no model calls. Revalidate every identity before publication."""
    from packages.rag.core import normalized_vector

    if connection.autocommit:
        raise ValueError("Index publication requires an explicit transaction")
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    prepared = computed.prepared
    if len(prepared.lexical_texts) != len(prepared.chunks):
        raise ValueError("Incomplete lexical manifest")
    lexical_by_text = {
        chunk.text: text
        for chunk, text in zip(prepared.chunks, prepared.lexical_texts, strict=True)
    }

    class PreparedLexical:
        def lexemes(self, text):
            return lexical_by_text[text]

    bid_id, parse_id = prepared.bid_id, prepared.parse_id
    parse = read_parse(connection, bid_id, parse_id)
    if (
        parse["document_version_id"] != prepared.document_version_id
        or parse["original_digest"] != prepared.original_digest
    ):
        raise ValueError("Original changed after index preparation")
    if len(computed.vectors) != len(prepared.chunks):
        raise ValueError("Incomplete computed vector manifest")
    root_id, source_id = ensure_parse_sources(connection, bid_id, parse_id)
    chunks = list(prepared.chunks)
    existing = connection.execute(
        "SELECT * FROM retrieval.chunks WHERE parse_id=%s ORDER BY ordinal",
        (parse_id,),
    ).fetchall()
    if not existing:
        insert_chunks(connection, bid_id, source_id, chunks, PreparedLexical())
    elif len(existing) != len(chunks) or any(
        row["id"] != chunk.id
        or row["source_id"] != source_id
        or row["digest"] != chunk.digest
        or row["token_count"] != chunk.token_count
        or row["lexemes"] != lexeme
        or tuple(row["evidence_ids"]) != chunk.evidence_ids
        for row, chunk, lexeme in zip(existing, chunks, prepared.lexical_texts, strict=True)
    ):
        raise ValueError("Parse has another chunk method; do not overwrite")
    embedding_identity = json.loads(computed.embedding_identity_json)
    profile_id = ensure_profile(connection, embedding_identity)

    # Validate the complete identity without invoking an external model port.
    class IdentityOnly:
        def __init__(self):
            self.identity = embedding_identity

        def embed(self, texts):
            raise RuntimeError("No inference is permitted in the publication transaction")

    assert_embedding_profile(connection, profile_id, IdentityOnly())
    for chunk, vector in zip(chunks, computed.vectors, strict=True):
        encoded = "[" + ",".join(map(str, normalized_vector(list(vector)))) + "]"
        connection.execute(
            """INSERT INTO retrieval.embeddings(chunk_id,profile_id,embedding)
               VALUES(%s,%s,%s::vector) ON CONFLICT(chunk_id,profile_id) DO NOTHING""",
            (chunk.id, profile_id, encoded),
        )
    for identity in (root_id, source_id):
        connection.execute(
            """UPDATE retrieval.source_heads SET status='published',revision=revision+1
               WHERE source_id=%s AND status='candidate'""",
            (identity,),
        )
    return {
        "source_id": source_id,
        "original_source_id": root_id,
        "profile_id": profile_id,
        "chunk_count": len(chunks),
        "embedded_count": len(chunks),
        "manifest_digest": hashlib.sha256(
            json.dumps([chunk.digest for chunk in chunks]).encode()
        ).hexdigest(),
        "parse_coverage": parse["coverage"],
        "parse_gaps": parse["gaps"],
        "indexed_scope": "readable_text_blocks_only",
    }


def withdraw_source(connection, bid_id: UUID, source_id: UUID, expected_root_revision: int) -> None:
    if connection.autocommit:
        raise ValueError("Withdrawal requires a transaction")
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    source = connection.execute(
        """SELECT n.document_version_id FROM retrieval.source_nodes n
           JOIN design.document_versions v ON v.id=n.document_version_id
           WHERE n.id=%s AND v.bid_id=%s""",
        (source_id, bid_id),
    ).fetchone()
    if not source:
        raise ValueError("Source does not belong to this project")
    root_id = original_source_id(source["document_version_id"])
    row = connection.execute(
        """UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1
           WHERE source_id=%s AND revision=%s RETURNING revision""",
        (root_id, expected_root_revision),
    ).fetchone()
    if not row:
        raise ValueError("Original source revision changed")
