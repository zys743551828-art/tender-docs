"""Pure safety/selection checks; no inference quality or network claim."""

from copy import deepcopy
from uuid import uuid4

import pytest

from packages.assistant.generation import (
    citation_position,
    node_text,
    selected_range,
    splice_text,
    validate_answer,
    validate_generation,
)
from packages.assistant.protocol import normalized_turn
from packages.authoring.content import empty_content
from packages.domain.runtime import DomainError, sha


def answer():
    return {
        "answer": "只读参考",
        "citations": [],
        "issue_suggestions": [],
        "tool_proposals": [],
        "gaps": [],
    }


@pytest.mark.parametrize("text", ["含\x00字符", "\ud800", "\x01"])
def test_unpersistable_input_is_rejected_before_queue(text):
    with pytest.raises(DomainError):
        normalized_turn({"client_message_id": str(uuid4()), "text": text, "source_selection": []})


def test_only_structured_whitelist_can_propose_and_none_can_execute_from_plain_yes():
    manifest = {"context": {"sources": [], "target_bid_ids": []}}
    assert validate_answer(manifest, answer())["tool_proposals"] == []
    bad = answer() | {
        "tool_proposals": [
            {"tool": "execute_sql", "target_bid_id": str(uuid4()), "arguments": {"sql": "DROP"}}
        ]
    }
    with pytest.raises(DomainError):
        validate_answer(manifest, bad)
    bad = answer() | {
        "tool_proposals": [
            {"tool": "review.request", "target_bid_id": str(uuid4()), "arguments": {}}
        ]
    }
    with pytest.raises(DomainError):
        validate_answer(manifest, bad)


def test_original_quote_must_be_in_real_evidence_not_server_locator_wrapper():
    text = "[physical_page=1 evidence_id=not-body]\n实际原文\n"
    source = {
        "source_key": "document:synthetic",
        "bid_id": str(uuid4()),
        "version": "1" * 64,
        "text": text,
        "provenance": {
            "kind": "original_document",
            "document_version_id": str(uuid4()),
            "parse_id": str(uuid4()),
        },
        "quote_ranges": [
            {
                "start": text.index("实际原文"),
                "end": text.index("实际原文") + 4,
                "physical_page": 1,
                "evidence_id": str(uuid4()),
            }
        ],
    }
    with pytest.raises(DomainError):
        citation_position(source, "physical_page=1")
    position = citation_position(source, "实际原文")
    assert position["physical_pages"] == [1] and position["verbatim_original"]
    with pytest.raises(DomainError):
        validate_answer(
            {"context": {"sources": [source], "target_bid_ids": []}},
            answer() | {"citations": [{"source_key": source["source_key"], "quote": "not-body"}]},
        )


@pytest.mark.parametrize(
    "start,end,replacement", [(0, 0, "前"), (1, 3, "改"), (2, 5, ""), (6, 6, "后"), (0, 6, "全新")]
)
def test_splice_preserves_outside_text_and_unselected_marks(start, end, replacement):
    node = {
        "type": "paragraph",
        "attrs": {"id": "stable-node"},
        "content": [
            {"type": "text", "text": "abc", "marks": [{"type": "bold"}]},
            {"type": "text", "text": "def", "marks": [{"type": "italic"}]},
        ],
    }
    result = splice_text(node, start, end, replacement)
    assert (
        result["attrs"] == node["attrs"]
        and node_text(result) == "abcdef"[:start] + replacement + "abcdef"[end:]
    )
    if start >= 3:
        assert result["content"][0] == node["content"][0]
    if end <= 3:
        assert result["content"][-1] == node["content"][-1]


def test_selected_generation_constructs_server_node_and_rejects_outside_tampering():
    original = empty_content("施工措施", 0)
    original["content"][0]["attrs"]["id"] = "stable-node"
    original["content"][0]["content"] = [
        {"type": "text", "text": "前文待改后文", "marks": [{"type": "bold"}]}
    ]
    plan = {"original_content": original, "source_blocks": []}
    selection = selected_range(
        plan, {"node_id": "stable-node", "selection_start": 2, "selection_end": 4}
    )
    manifest = {"selection": selection, "plan": plan}
    value = validate_generation(manifest, {"replacement_text": "已改", "evidence_ids": []})
    assert node_text(value["replacement"]) == "前文已改后文"
    assert validate_generation(manifest, value, canonical=True) == value
    changed = deepcopy(value)
    changed["replacement"]["content"][0]["text"] = "破坏前文"
    with pytest.raises(DomainError):
        validate_generation(manifest, changed, canonical=True)
    with pytest.raises(DomainError):
        validate_generation(manifest, {"replacement_text": "改", "evidence_ids": [str(uuid4())]})
    assert value["before_digest"] == sha(original["content"][0])
