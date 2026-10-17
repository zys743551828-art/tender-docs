"""Tokenizer-based chunks, validated vectors, deterministic fusion and citations."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid5

CHUNK_NAMESPACE = UUID("7d67fbf0-3569-41c1-b82e-650ddcfb526a")


class Tokenizer(Protocol):
    def offsets(self, text: str) -> list[tuple[int, int]]: ...


class Embedder(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EvidenceText:
    evidence_id: UUID
    parse_id: UUID
    physical_page: int
    text: str
    chapter_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class Chunk:
    id: UUID
    parse_id: UUID
    physical_page: int
    ordinal: int
    evidence_ids: tuple[UUID, ...]
    chapter_path: tuple[str, ...]
    text: str
    token_count: int
    digest: str


def chunk_evidence(
    blocks: list[EvidenceText],
    tokenizer: Tokenizer,
    target: int = 800,
    hard_limit: int = 1200,
    overlap: int = 120,
) -> list[Chunk]:
    if not 0 <= overlap < target <= hard_limit:
        raise ValueError("Invalid chunk token limits")
    if len({block.parse_id for block in blocks}) > 1:
        raise ValueError("A chunk manifest must belong to one parse")
    chunks: list[Chunk] = []
    for block in blocks:
        if block.physical_page < 1:
            raise ValueError("Invalid physical page")
        offsets = tokenizer.offsets(block.text)
        if any(start < 0 or end > len(block.text) or start >= end for start, end in offsets):
            raise ValueError("Tokenizer returned invalid source offsets")
        if any(offsets[i][0] < offsets[i - 1][0] for i in range(1, len(offsets))):
            raise ValueError("Tokenizer offsets are not in source order")
        start = 0
        while start < len(offsets):
            end = min(start + target, len(offsets))
            # Re-tokenization can change boundary tokens. Verify the actual fragment.
            text = block.text[offsets[start][0] : offsets[end - 1][1]]
            count = len(tokenizer.offsets(text))
            while count > hard_limit and end > start + 1:
                end -= 1
                text = block.text[offsets[start][0] : offsets[end - 1][1]]
                count = len(tokenizer.offsets(text))
            if count < 1 or count > hard_limit:
                raise ValueError("Fragment cannot meet the tokenizer limit")
            digest = hashlib.sha256(text.encode()).hexdigest()
            identity = f"{block.parse_id}:{block.evidence_id}:{start}:{end}:{digest}"
            chunks.append(
                Chunk(
                    uuid5(CHUNK_NAMESPACE, identity),
                    block.parse_id,
                    block.physical_page,
                    len(chunks),
                    (block.evidence_id,),
                    block.chapter_path,
                    text,
                    count,
                    digest,
                )
            )
            if end == len(offsets):
                break
            start = max(start + 1, end - overlap)
    return chunks


def normalized_vector(values: list[float], dimensions: int = 1024) -> list[float]:
    if len(values) != dimensions or any(not math.isfinite(value) for value in values):
        raise ValueError("Embedding dimensions or finite values invalid")
    magnitude = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(magnitude) or magnitude <= 0:
        raise ValueError("Embedding norm invalid")
    return [value / magnitude for value in values]


def fuse_rankings(rankings: list[list[UUID]], limit: int = 20) -> list[tuple[UUID, float]]:
    if limit < 1:
        raise ValueError("Invalid result limit")
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        if len(set(ranking)) != len(ranking):
            raise ValueError("A ranking contains duplicate chunk identities")
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk] = scores.get(chunk, 0.0) + 1.0 / (60 + rank)
    return sorted(scores.items(), key=lambda row: (-row[1], str(row[0])))[:limit]


@dataclass(frozen=True)
class SuppliedEvidence:
    citation_id: str
    source_id: UUID
    source_revision: int
    parse_id: UUID
    physical_page: int
    chunk_id: UUID
    text: str


def validate_citation(
    citation_id: str,
    quotation: str,
    physical_page: int,
    supplied: list[SuppliedEvidence],
) -> SuppliedEvidence:
    matches = [item for item in supplied if item.citation_id == citation_id]
    if len(matches) != 1 or not quotation.strip():
        raise ValueError("Citation was not uniquely supplied or has no quotation")
    evidence = matches[0]
    if physical_page != evidence.physical_page or quotation not in evidence.text:
        raise ValueError("Citation page or verbatim quotation invalid")
    return evidence
