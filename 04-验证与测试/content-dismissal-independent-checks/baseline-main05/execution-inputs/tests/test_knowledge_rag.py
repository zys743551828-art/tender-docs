"""Explicit character tokenizer fixture proves envelope safety, not BGE quality."""

from dataclasses import replace
from uuid import uuid4

import pytest

from packages.knowledge.rag_types import assert_unit_envelope, make_units


class CharacterTokenizer:
    def __init__(self):
        self.inputs = []

    def offsets(self, text):
        self.inputs.append(text)
        return [(i, i + 1) for i in range(len(text))]


def test_knowledge_and_quote_remain_separate_and_never_read_unapproved_paragraph_tail():
    index = uuid4()
    summary = "派生参考：按本工程事实核查，不沿用历史工期。"
    approved = "仅批准这句道路工法。"
    unapproved_tail = "同段未批准尾句：历史工期120天不得外传或当本项目事实。"
    tokenizer = CharacterTokenizer()
    units = make_units(index, summary, (approved,), tokenizer, target=8, hard_limit=12, overlap=2)
    assert [u.ordinal for u in units] == list(range(len(units)))
    assert {u.unit_kind for u in units} == {"knowledge_text", "original_quote"}
    assert all(unapproved_tail not in supplied for supplied in tokenizer.inputs)
    for unit in units:
        assert_unit_envelope(unit, summary, (approved,))
        assert unit.text in (summary if unit.unit_kind == "knowledge_text" else approved)
        assert unit.offset_basis == (
            "knowledge_content" if unit.unit_kind == "knowledge_text" else "approved_excerpt"
        )
    quote = next(u for u in units if u.unit_kind == "original_quote")
    with pytest.raises(ValueError):
        assert_unit_envelope(replace(quote, text=approved + unapproved_tail), summary, (approved,))


def test_equal_quote_text_has_distinct_identity_for_each_approved_reference():
    index = uuid4()
    units = make_units(index, "派生参考", ("相同批准引文", "相同批准引文"), CharacterTokenizer())
    quotes = [u for u in units if u.unit_kind == "original_quote"]
    assert quotes[0].text == quotes[1].text and quotes[0].id != quotes[1].id
    assert quotes[0].ref_ordinal != quotes[1].ref_ordinal
    assert units == make_units(
        index, "派生参考", ("相同批准引文", "相同批准引文"), CharacterTokenizer()
    )
    with pytest.raises(ValueError):
        assert_unit_envelope(
            replace(quotes[0], ref_ordinal=2), "派生参考", ("相同批准引文", "相同批准引文")
        )


def test_index_limit_fails_instead_of_publishing_a_partial_ready_manifest():
    with pytest.raises(ValueError, match="KNOWLEDGE_INDEX_TOO_LARGE"):
        make_units(
            uuid4(),
            "这是长度足够的派生文本",
            ("批准引文",),
            CharacterTokenizer(),
            target=3,
            hard_limit=4,
            overlap=1,
            max_units=2,
        )


@pytest.mark.parametrize("offsets", [[], [(0, 0)], [(0, 99)], [(1, 2), (0, 1)], [(False, 1)]])
def test_invalid_token_offsets_never_become_original_positions(offsets):
    from packages.knowledge.rag_types import split_envelope

    class BrokenTokenizer:
        def offsets(self, text):
            return offsets

    with pytest.raises(ValueError):
        split_envelope("批准引文", BrokenTokenizer())


def test_index_compute_embeds_and_lexicalizes_only_approved_envelopes_with_immutable_profile():
    import json

    from packages.knowledge.rag_index import PreparedKnowledgeIndex, compute_index

    class ExplicitMechanismEmbedder:
        identity = {
            "model_name": "EXPLICIT_TEST_FIXTURE",
            "model_revision": "mechanism-only",
            "weights_digest": "0" * 64,
            "preprocessing_digest": "1" * 64,
        }

        def __init__(self):
            self.supplied = []

        def embed(self, texts):
            self.supplied.extend(texts)
            return [[1.0] + [0.0] * 1023 for _ in texts]

    class ExplicitMechanismLexical:
        def __init__(self):
            self.supplied = []

        def lexemes(self, text):
            self.supplied.append(text)
            return " ".join(text)

    embedder = ExplicitMechanismEmbedder()
    lexical = ExplicitMechanismLexical()
    prepared = PreparedKnowledgeIndex(
        uuid4(),
        "2" * 64,
        "人工维护的派生参考，不是原文",
        ("只允许这句原件引文",),
        json.dumps(embedder.identity, sort_keys=True),
        "3" * 64,
    )
    outcome = compute_index(
        prepared, CharacterTokenizer(), embedder, lexical, lexical_dictionary_digest="3" * 64
    )
    assert embedder.supplied == lexical.supplied == [unit.text for unit in outcome.units]
    assert set(embedder.supplied) == {prepared.content, *prepared.approved_excerpts}
    assert "未批准同段尾句" not in "".join(embedder.supplied)
    assert len(outcome.vectors) == len(outcome.units) and all(
        len(v) == 1024 for v in outcome.vectors
    )
    assert outcome == compute_index(
        prepared, CharacterTokenizer(), embedder, lexical, lexical_dictionary_digest="3" * 64
    )
    with pytest.raises(ValueError, match="PROFILE_CHANGED"):
        compute_index(
            prepared, CharacterTokenizer(), embedder, lexical, lexical_dictionary_digest="4" * 64
        )


@pytest.mark.parametrize("text", ["含\x00空字节", "控制\x01字符", "\ud800"])
def test_query_rejects_database_invalid_text_before_any_runtime_or_transaction(text):
    from packages.domain.runtime import DomainError
    from packages.knowledge.rag_contracts import validate_actual

    with pytest.raises(DomainError) as error:
        validate_actual(
            "KnowledgeRetrievalRequest", {"query": text, "purpose": "authoring", "limit": 2}
        )
    assert error.value.status == 422


@pytest.mark.parametrize("failure", ["unavailable", "partial", "identity_missing"])
def test_reranker_failure_preserves_real_fused_order_and_declares_fallback(failure):
    from packages.knowledge.rag_search import rerank_candidates
    from packages.rag.http_port import RuntimeUnavailable

    candidates = [{"unit_id": str(uuid4()), "text": text} for text in ["一", "二"]]

    class BrokenReranker:
        def rank(self, query, texts):
            if failure == "unavailable":
                raise RuntimeUnavailable("explicit_fixture_failure")
            return [1] if failure == "partial" else [2, 1]

    selected, route, identity = rerank_candidates("参考", candidates, BrokenReranker())
    assert selected == candidates and route[-1] == "reranker_unavailable" and identity is None
