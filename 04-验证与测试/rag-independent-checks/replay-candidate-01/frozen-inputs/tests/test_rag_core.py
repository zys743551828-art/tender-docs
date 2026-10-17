"""Mechanism checks only; fixture tokenization/vectors do not claim retrieval quality."""

from dataclasses import replace
from uuid import uuid4

import pytest

from packages.rag.core import (
    EvidenceText,
    SuppliedEvidence,
    chunk_evidence,
    fuse_rankings,
    normalized_vector,
    validate_citation,
)


class FixtureTokenizer:
    def offsets(self, text):
        return [(i, i + 1) for i, char in enumerate(text) if not char.isspace()]


def test_chunk_keeps_exact_unicode_original_page_and_overlap():
    text = "道路施工需设置围挡。" * 200
    block = EvidenceText(uuid4(), uuid4(), 3, text, ("施工组织",))
    chunks = chunk_evidence([block], FixtureTokenizer())
    assert len(chunks) > 1
    assert all(chunk.text in text and chunk.physical_page == 3 for chunk in chunks)
    assert all(chunk.evidence_ids == (block.evidence_id,) for chunk in chunks)
    assert chunks[0].text[-120:] == chunks[1].text[:120]
    assert chunks == chunk_evidence([block], FixtureTokenizer())
    assert all(chunk.token_count <= 1200 for chunk in chunks)
    with pytest.raises(ValueError):
        chunk_evidence([block, replace(block, parse_id=uuid4())], FixtureTokenizer())


@pytest.mark.parametrize(
    "vector", [[0.0] * 1024, [float("nan")] * 1024, [float("inf")] * 1024, [1.0] * 768]
)
def test_invalid_vectors_never_reach_pgvector(vector):
    with pytest.raises(ValueError):
        normalized_vector(vector)


def test_fusion_cannot_double_count_duplicate_candidates():
    first, second = uuid4(), uuid4()
    fused = fuse_rankings([[first, second], [second]])
    assert fused[0][0] == second
    with pytest.raises(ValueError):
        fuse_rankings([[first, first]])


def test_citation_requires_supplied_identity_page_and_exact_quotation():
    evidence = SuppliedEvidence("trace:1", uuid4(), 2, uuid4(), 5, uuid4(), "围挡高度不低于2米。")
    assert validate_citation("trace:1", "不低于2米", 5, [evidence]) == evidence
    for identity, quotation, page in [
        ("unknown", "不低于2米", 5),
        ("trace:1", "不低于3米", 5),
        ("trace:1", "不低于2米", 6),
    ]:
        with pytest.raises(ValueError):
            validate_citation(identity, quotation, page, [evidence])
