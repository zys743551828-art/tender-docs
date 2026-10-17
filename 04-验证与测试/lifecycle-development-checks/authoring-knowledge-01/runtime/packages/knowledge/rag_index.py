"""Lock-free knowledge envelope indexing computation.

API/Worker adapters freeze and revalidate authority separately. The computation
receives immutable strings and approved excerpts, never a live DB connection or
an original evidence paragraph that could expand the approved envelope.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from uuid import UUID

from packages.domain.runtime import sha
from packages.knowledge.rag_types import UnitFragment, assert_unit_envelope, make_units
from packages.rag.core import Embedder, Tokenizer, normalized_vector
from packages.rag.repository import Lexical

CHUNK_LIMITS = {"target": 800, "hard_limit": 1200, "overlap": 120, "max_units": 1000}
CHUNK_POLICY = {"name": "knowledge_envelopes/1", **CHUNK_LIMITS}


def canonical_vector(values):
    """Match pgvector's finite float32 storage and make replay digests stable."""
    normalized = normalized_vector(list(values))
    return tuple(struct.unpack("!1024f", struct.pack("!1024f", *normalized)))


def vector_digest(vector):
    return hashlib.sha256(struct.pack("!1024f", *vector)).hexdigest()


@dataclass(frozen=True)
class PreparedKnowledgeIndex:
    index_id: UUID
    input_digest: str
    content: str
    approved_excerpts: tuple[str, ...]
    embedding_identity_json: str
    lexical_dictionary_digest: str


@dataclass(frozen=True)
class ComputedKnowledgeIndex:
    prepared: PreparedKnowledgeIndex
    units: tuple[UnitFragment, ...]
    lexical_texts: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]
    manifest_digest: str


def unit_manifest(units, lexical_texts, vectors):
    if not units or not len(units) == len(lexical_texts) == len(vectors):
        raise ValueError("KNOWLEDGE_INDEX_INCOMPLETE")
    return [
        {
            "unit_id": str(unit.id),
            "index_id": str(unit.index_id),
            "ordinal": unit.ordinal,
            "unit_kind": unit.unit_kind,
            "ref_ordinal": unit.ref_ordinal,
            "span_start": unit.span_start,
            "span_end": unit.span_end,
            "text_digest": unit.digest,
            "token_count": unit.token_count,
            "lexical_digest": sha(lexical),
            "vector_digest": vector_digest(vector),
        }
        for unit, lexical, vector in zip(units, lexical_texts, vectors, strict=True)
    ]


def compute_index(
    prepared: PreparedKnowledgeIndex,
    tokenizer: Tokenizer,
    embedder: Embedder,
    lexical: Lexical,
    *,
    lexical_dictionary_digest: str,
) -> ComputedKnowledgeIndex:
    """Every tokenizer/lexical/embedding call happens here, with no DB argument."""
    if (
        json.dumps(embedder.identity, sort_keys=True) != prepared.embedding_identity_json
        or lexical_dictionary_digest != prepared.lexical_dictionary_digest
    ):
        raise ValueError("KNOWLEDGE_INDEX_PROFILE_CHANGED")
    units = make_units(
        prepared.index_id,
        prepared.content,
        prepared.approved_excerpts,
        tokenizer,
        **CHUNK_LIMITS,
    )
    lexical_texts = tuple(lexical.lexemes(unit.text) for unit in units)
    if any(not isinstance(value, str) for value in lexical_texts):
        raise ValueError("KNOWLEDGE_LEXICAL_INVALID")
    raw_vectors = embedder.embed([unit.text for unit in units])
    if len(raw_vectors) != len(units):
        raise ValueError("KNOWLEDGE_INDEX_INCOMPLETE")
    vectors = tuple(canonical_vector(values) for values in raw_vectors)
    for unit in units:
        assert_unit_envelope(unit, prepared.content, prepared.approved_excerpts)
    manifest = unit_manifest(units, lexical_texts, vectors)
    return ComputedKnowledgeIndex(prepared, units, lexical_texts, vectors, sha(manifest))
