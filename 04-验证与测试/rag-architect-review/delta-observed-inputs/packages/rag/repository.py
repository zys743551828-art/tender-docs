"""Nonowner PostgreSQL indexing and exact hybrid retrieval with source-head rechecks."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from psycopg import sql
from psycopg.types.json import Jsonb

from packages.rag.core import (
    Chunk,
    Embedder,
    SuppliedEvidence,
    fuse_rankings,
    normalized_vector,
)


class Lexical(Protocol):
    def lexemes(self, text: str) -> str: ...


class Reranker(Protocol):
    def rank(self, query: str, texts: list[str]) -> list[float]: ...


@dataclass(frozen=True)
class RetrievalScope:
    bid_id: UUID
    purpose: str
    source_ids: tuple[UUID, ...]
    as_of: datetime
    excluded_groups: tuple[UUID, ...] = ()

    def __post_init__(self):
        if self.purpose not in ("assist", "authoring", "compliance", "history"):
            raise ValueError("Formal scoring uses complete chapter assembly and a separate role")
        if self.as_of.tzinfo is None:
            raise ValueError("Source evaluation time must be timezone aware")


def source_manifest(connection, scope: RetrievalScope) -> list[dict]:
    rows = connection.execute(
        """SELECT a.source_id,n.id,n.kind,n.policy_class,n.known_at,n.digest,
           h.revision,h.status,h.valid_from,h.valid_until
           FROM retrieval.source_ancestors a
           JOIN retrieval.source_nodes n ON n.id=a.ancestor_id
           LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
           WHERE a.source_id=ANY(%s) ORDER BY a.source_id,n.id""",
        (list(scope.source_ids),),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_project_scope(
    connection, bid_id: UUID, context: str, as_of: datetime
) -> RetrievalScope:
    """Trusted domain entry: public clients never supply source allowlists or formal purposes."""
    if context not in ("assistant", "authoring", "body_evidence", "history"):
        raise ValueError("Unknown public retrieval context")
    if context == "history":
        versions = connection.execute(
            "SELECT id FROM design.document_versions WHERE bid_id=%s",
            (bid_id,),
        ).fetchall()
    else:
        versions = connection.execute(
            """SELECT a.document_version_id AS id FROM design.bid_profiles p
               JOIN design.material_assignments a
                 ON a.snapshot_id=p.current_material_id AND a.bid_id=p.bid_id
               WHERE p.bid_id=%s AND a.role=ANY(%s)
               UNION SELECT current_body_id FROM design.bid_profiles
               WHERE bid_id=%s AND current_body_id IS NOT NULL""",
            (
                bid_id,
                ["body", "tender", "supplement"]
                if context == "body_evidence"
                else ["body", "tender", "supplement", "drawing", "boq", "other"],
                bid_id,
            ),
        ).fetchall()
    ids = [row["id"] for row in versions]
    sources = connection.execute(
        """SELECT DISTINCT n.id FROM retrieval.source_nodes n
           JOIN retrieval.chunks c ON c.source_id=n.id
           JOIN design.parse_runs p ON p.id=c.parse_id
           WHERE n.document_version_id=ANY(%s) AND n.kind='document'
           AND p.id=(SELECT newest.id FROM design.parse_runs newest
             WHERE newest.document_version_id=n.document_version_id
             ORDER BY newest.created_at DESC,newest.id DESC LIMIT 1) ORDER BY n.id""",
        (ids,),
    ).fetchall()
    purpose = {
        "assistant": "assist",
        "authoring": "authoring",
        "body_evidence": "compliance",
        "history": "history",
    }[context]
    return RetrievalScope(bid_id, purpose, tuple(row["id"] for row in sources), as_of)


def insert_chunks(connection, bid_id: UUID, source_id: UUID, chunks: list[Chunk], lexical: Lexical):
    """Verify every chunk's exact original block, source document, parse, page and project."""
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    for expected_ordinal, chunk in enumerate(chunks):
        if (
            chunk.ordinal != expected_ordinal
            or not chunk.evidence_ids
            or not 0 < chunk.token_count <= 1200
        ):
            raise ValueError("Noncontiguous chunk manifest or missing evidence")
        row = connection.execute(
            """SELECT p.document_version_id,n.document_version_id AS source_version
               FROM design.parse_runs p JOIN retrieval.source_nodes n ON n.id=%s
               WHERE p.id=%s AND p.bid_id=%s""",
            (source_id, chunk.parse_id, bid_id),
        ).fetchone()
        if not row or row["document_version_id"] != row["source_version"]:
            raise ValueError("Chunk source does not own the parse original")
        evidence = connection.execute(
            """SELECT id,text_content FROM design.evidence_blocks
               WHERE id=ANY(%s) AND bid_id=%s AND parse_id=%s AND physical_page=%s
               ORDER BY ordinal""",
            (list(chunk.evidence_ids), bid_id, chunk.parse_id, chunk.physical_page),
        ).fetchall()
        if len(evidence) != len(chunk.evidence_ids) or not any(
            chunk.text in block["text_content"] for block in evidence
        ):
            raise ValueError("Chunk citation is not an exact original block fragment")
        if hashlib.sha256(chunk.text.encode()).hexdigest() != chunk.digest:
            raise ValueError("Chunk digest mismatch")
        connection.execute(
            """INSERT INTO retrieval.chunks
               (id,source_id,parse_id,physical_page,ordinal,chapter_path,evidence_ids,
                text_content,lexemes,token_count,digest)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                chunk.id,
                source_id,
                chunk.parse_id,
                chunk.physical_page,
                chunk.ordinal,
                list(chunk.chapter_path),
                list(chunk.evidence_ids),
                chunk.text,
                lexical.lexemes(chunk.text),
                chunk.token_count,
                chunk.digest,
            ),
        )


def assert_embedding_profile(connection, profile_id: UUID, embedder: Embedder) -> dict:
    profile = connection.execute(
        "SELECT * FROM retrieval.embedding_profiles WHERE id=%s",
        (profile_id,),
    ).fetchone()
    if (
        not profile
        or profile["dimensions"] != 1024
        or profile["distance"] != "cosine"
        or not profile["normalized"]
    ):
        raise ValueError("Missing or incompatible embedding profile")
    if any(profile[key] != value for key, value in embedder.identity.items()) or set(
        embedder.identity
    ) != {"model_name", "model_revision", "weights_digest", "preprocessing_digest"}:
        raise ValueError("Embedding runtime does not match the frozen profile")
    return dict(profile)


def insert_embeddings(connection, profile_id: UUID, chunks: list[Chunk], embedder: Embedder):
    assert_embedding_profile(connection, profile_id, embedder)
    vectors = embedder.embed([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError("Embedding response does not cover the chunk manifest")
    for chunk, vector in zip(chunks, vectors, strict=True):
        encoded = "[" + ",".join(map(str, normalized_vector(vector))) + "]"
        connection.execute(
            "INSERT INTO retrieval.embeddings(chunk_id,profile_id,embedding) VALUES(%s,%s,%s::vector)",
            (chunk.id, profile_id, encoded),
        )


def retrieve(
    connection,
    scope: RetrievalScope,
    query: str,
    embedder: Embedder,
    profile_id: UUID,
    policy_digest: str,
    lexical: Lexical,
    reranker: Reranker | None = None,
) -> dict:
    if not query.strip() or len(query) > 10000:
        raise ValueError("Query is empty or too long")
    assert_embedding_profile(connection, profile_id, embedder)
    foreign = connection.execute(
        """SELECT n.id FROM retrieval.source_nodes n
           LEFT JOIN design.document_versions v ON v.id=n.document_version_id
           WHERE n.id=ANY(%s) AND (v.bid_id IS DISTINCT FROM %s OR n.kind<>'document')""",
        (list(scope.source_ids), scope.bid_id),
    ).fetchall()
    if foreign:
        raise ValueError("Retrieval source belongs to another project or unsupported scope")
    query_vectors = embedder.embed([query])
    if len(query_vectors) != 1:
        raise ValueError("Query embedding response incomplete")
    vector = "[" + ",".join(map(str, normalized_vector(query_vectors[0]))) + "]"
    before = source_manifest(connection, scope)
    params = (list(scope.source_ids), scope.as_of, list(scope.excluded_groups), False)
    allowed = """WITH allowed AS MATERIALIZED (
        SELECT * FROM retrieval.eligible_chunks(%s,%s,%s,%s)) """
    dense = connection.execute(
        sql.SQL(allowed)
        + sql.SQL("""SELECT c.id,e.embedding <=> %s::vector AS distance FROM allowed c
            JOIN retrieval.embeddings e ON e.chunk_id=c.id
            WHERE e.profile_id=%s ORDER BY distance,c.id LIMIT 50"""),
        (*params, vector, profile_id),
    ).fetchall()
    lex = connection.execute(
        sql.SQL(allowed)
        + sql.SQL("""SELECT c.id,ts_rank_cd(c.lex_tsv,q.value) AS score FROM allowed c,
            plainto_tsquery('simple',%s) q(value)
            WHERE c.lex_tsv @@ q.value ORDER BY score DESC,c.id LIMIT 50"""),
        (*params, lexical.lexemes(query)),
    ).fetchall()
    fused = fuse_rankings([[row["id"] for row in dense], [row["id"] for row in lex]])
    ids = [item[0] for item in fused]
    rows = connection.execute(
        sql.SQL(allowed)
        + sql.SQL("""SELECT c.*,h.revision AS source_revision FROM allowed c
            JOIN retrieval.source_heads h ON h.source_id=c.source_id WHERE c.id=ANY(%s)"""),
        (*params, ids),
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    ordered = [by_id[identity] for identity in ids if identity in by_id]
    route = "exact_vector+lexical+rrf"
    if reranker and ordered:
        try:
            scores = reranker.rank(query, [row["text_content"] for row in ordered])
            if len(scores) != len(ordered) or any(not math.isfinite(score) for score in scores):
                raise ValueError("Reranker produced invalid values")
            ordered = [
                row
                for _, row in sorted(
                    zip(scores, ordered, strict=True),
                    key=lambda pair: (-pair[0], str(pair[1]["id"])),
                )
            ]
            route += "+reranker"
        except Exception:
            # RRF is an explicit degraded route; never fabricate reranker success.
            route += "+reranker_unavailable"
    after = source_manifest(connection, scope)
    if before != after:
        raise ValueError("Sources changed during retrieval; discard this result")
    selected = ordered[:8]
    trace_id = uuid4()
    manifest_digest = hashlib.sha256(
        json.dumps(before, default=str, sort_keys=True).encode()
    ).hexdigest()
    connection.execute(
        """INSERT INTO retrieval.retrieval_traces
           (id,bid_id,purpose,query_digest,allowed_manifest_digest,profile_id,policy_digest,
            selected_chunk_ids,excluded_reasons) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            trace_id,
            scope.bid_id,
            scope.purpose,
            hashlib.sha256(query.encode()).hexdigest(),
            manifest_digest,
            profile_id,
            policy_digest,
            [row["id"] for row in selected],
            Jsonb({"route": route, "source_heads_rechecked": True}),
        ),
    )
    evidence = [
        SuppliedEvidence(
            str(uuid5(trace_id, str(row["id"]))),
            row["source_id"],
            row["source_revision"],
            row["parse_id"],
            row["physical_page"],
            row["id"],
            row["text_content"],
        )
        for index, row in enumerate(selected)
    ]
    return {
        "trace_id": trace_id,
        "route": route,
        "manifest_digest": manifest_digest,
        "scope": scope,
        "source_manifest": before,
        "evidence": evidence,
    }


def assert_current_sources(connection, result: dict) -> None:
    """Hold this transaction's graph lock until the caller commits its application effect."""
    if connection.autocommit:
        raise ValueError("Source application requires a transaction")
    isolation = connection.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
    if isolation != "read committed":
        raise ValueError("Source application requires fresh read-committed authority")
    connection.execute("SELECT pg_advisory_xact_lock(90127,1)")
    if source_manifest(connection, result["scope"]) != result["source_manifest"]:
        raise ValueError("Retrieved sources are no longer current")
    scope = result["scope"]
    selected = {item.chunk_id for item in result["evidence"]}
    current = connection.execute(
        """SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,false) WHERE id=ANY(%s)""",
        (list(scope.source_ids), scope.as_of, list(scope.excluded_groups), list(selected)),
    ).fetchall()
    if {row["id"] for row in current} != selected:
        raise ValueError("Retrieved evidence is no longer eligible")
