"""Knowledge units have their own identity and stay inside approved envelopes.

This module does not read a database, original block, model credentials or chunks.
Offsets describe a published knowledge text or its approved excerpt, never an
invented location in an unprovided original paragraph.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid5

from packages.rag.core import Tokenizer

UNIT_NAMESPACE = UUID("28723442-38da-477d-a973-44f3fd781035")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UnitFragment:
    id: UUID
    index_id: UUID
    ordinal: int
    unit_kind: str
    ref_ordinal: int | None
    span_start: int
    span_end: int
    text: str
    token_count: int
    digest: str

    @property
    def offset_basis(self) -> str:
        return "knowledge_content" if self.unit_kind == "knowledge_text" else "approved_excerpt"


def split_envelope(
    text: str,
    tokenizer: Tokenizer,
    *,
    target: int = 800,
    hard_limit: int = 1200,
    overlap: int = 120,
) -> list[tuple[int, int, str, int]]:
    """Tokenize only supplied text. Every emitted span is checked against it."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("KNOWLEDGE_EMPTY_ENVELOPE")
    if (
        any(type(v) is not int for v in (overlap, target, hard_limit))
        or not 0 <= overlap < target <= hard_limit <= 1200
    ):
        raise ValueError("KNOWLEDGE_CHUNK_POLICY_INVALID")
    offsets = tokenizer.offsets(text)
    if not offsets or any(
        not isinstance(pair, (tuple, list))
        or len(pair) != 2
        or any(type(v) is not int for v in pair)
        or not 0 <= pair[0] < pair[1] <= len(text)
        for pair in offsets
    ):
        raise ValueError("KNOWLEDGE_TOKEN_OFFSETS_INVALID")
    if any(offsets[i][0] < offsets[i - 1][0] for i in range(1, len(offsets))):
        raise ValueError("KNOWLEDGE_TOKEN_OFFSETS_UNORDERED")
    fragments = []
    first = 0
    while first < len(offsets):
        end = min(first + target, len(offsets))
        while True:
            span_start, span_end = offsets[first][0], offsets[end - 1][1]
            fragment = text[span_start:span_end]
            count = len(tokenizer.offsets(fragment))
            if 0 < count <= hard_limit:
                break
            if end <= first + 1:
                raise ValueError("KNOWLEDGE_FRAGMENT_TOKEN_LIMIT")
            end -= 1
        fragments.append((span_start, span_end, fragment, count))
        if end == len(offsets):
            break
        first = max(first + 1, end - overlap)
    return fragments


def make_units(
    index_id: UUID,
    content: str,
    approved_excerpts: tuple[str, ...],
    tokenizer: Tokenizer,
    *,
    target: int = 800,
    hard_limit: int = 1200,
    overlap: int = 120,
    max_units: int = 1000,
) -> tuple[UnitFragment, ...]:
    """Caller passes immutable approved envelopes, not whole evidence blocks."""
    if type(max_units) is not int or not 1 <= max_units <= 1000:
        raise ValueError("KNOWLEDGE_UNIT_LIMIT_INVALID")
    envelopes = [("knowledge_text", None, content)] + [
        ("original_quote", ordinal, excerpt) for ordinal, excerpt in enumerate(approved_excerpts)
    ]
    result: list[UnitFragment] = []
    for kind, ref_ordinal, envelope in envelopes:
        for start, end, fragment, count in split_envelope(
            envelope, tokenizer, target=target, hard_limit=hard_limit, overlap=overlap
        ):
            if len(result) >= max_units:
                raise ValueError("KNOWLEDGE_INDEX_TOO_LARGE")
            digest = text_digest(fragment)
            ident = uuid5(UNIT_NAMESPACE, f"{index_id}:{kind}:{ref_ordinal}:{start}:{end}:{digest}")
            result.append(
                UnitFragment(
                    ident,
                    index_id,
                    len(result),
                    kind,
                    ref_ordinal,
                    start,
                    end,
                    fragment,
                    count,
                    digest,
                )
            )
    return tuple(result)


def assert_unit_envelope(unit: UnitFragment, content: str, approved_excerpts: tuple[str, ...]):
    """Final publication/use must verify the frozen text, not trust computed output."""
    if unit.unit_kind == "knowledge_text" and unit.ref_ordinal is None:
        envelope = content
    elif (
        unit.unit_kind == "original_quote"
        and type(unit.ref_ordinal) is int
        and 0 <= unit.ref_ordinal < len(approved_excerpts)
    ):
        envelope = approved_excerpts[unit.ref_ordinal]
    else:
        raise ValueError("KNOWLEDGE_UNIT_PROVENANCE_INVALID")
    if (
        type(unit.ordinal) is not int
        or unit.ordinal < 0
        or any(type(v) is not int for v in (unit.span_start, unit.span_end, unit.token_count))
        or not 0 <= unit.span_start < unit.span_end <= len(envelope)
        or unit.text != envelope[unit.span_start : unit.span_end]
        or unit.digest != text_digest(unit.text)
        or not 0 < unit.token_count <= 1200
        or unit.id
        != uuid5(
            UNIT_NAMESPACE,
            f"{unit.index_id}:{unit.unit_kind}:{unit.ref_ordinal}:{unit.span_start}:{unit.span_end}:{unit.digest}",
        )
    ):
        raise ValueError("KNOWLEDGE_UNIT_ENVELOPE_CHANGED")
