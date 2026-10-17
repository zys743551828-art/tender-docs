"""Nontrivial tree identity/ordering and strict template/plan input boundaries."""

from copy import deepcopy
from uuid import uuid4

import pytest

from packages.authoring.content import empty_content
from packages.authoring.structure_model import mutate, node, ordered, validate_tree
from packages.authoring.structure_schema import validate
from packages.domain.runtime import DomainError, sha


def sample():
    a, b, c = [str(uuid4()) for _ in range(3)]
    nodes = [node(a), node(b, parent=a), node(c, ordinal=1)]
    contents = {
        identity: empty_content(title, index)
        for index, (identity, title) in enumerate([(a, "一"), (b, "子章"), (c, "二")])
    }
    contents[b]["content"][0]["content"] = [
        {"type": "text", "text": "保留人工文字", "marks": [{"type": "bold"}]}
    ]
    return nodes, contents, (a, b, c)


def test_atomic_reparent_copy_new_ids_preserves_provenance_not_status():
    tree, contents, (a, b, c) = sample()
    ref = {
        "source_id": str(uuid4()),
        "source_revision": 1,
        "evidence_id": str(uuid4()),
        "document_version_id": str(uuid4()),
    }
    contents[b]["attrs"]["source_refs"] = [ref]
    original = deepcopy((tree, contents))
    value = mutate(
        tree,
        contents,
        [
            {"op": "move", "section_id": b, "parent": {"section_id": c}, "ordinal": 0},
            {
                "op": "copy",
                "section_id": c,
                "parent": None,
                "ordinal": 1,
                "client_key": "duplicate",
            },
        ],
    )
    new = value["created_ids"]["duplicate"]
    assert new not in (a, b, c) and len(value["copied_from"]) == 2
    new_b = next(i for i, old in value["copied_from"].items() if old == b)
    assert value["contents"][new_b]["attrs"]["source_refs"] == [ref]
    assert (
        value["contents"][new_b]["content"][0]["attrs"]["id"]
        != contents[b]["content"][0]["attrs"]["id"]
    )
    assert value["contents"][new_b]["content"][0]["content"] == contents[b]["content"][0]["content"]
    assert all(
        n["plan_version_id"] is None and n["section_revision"] == 1
        for n in value["nodes"]
        if n["section_id"] in value["copied_from"]
    )
    assert value["digest"] == sha(value["nodes"]) and (tree, contents) == original


def test_delete_tombstones_keep_historical_text_restore_is_explicit():
    tree, contents, (a, b, c) = sample()
    deleted = mutate(tree, contents, [{"op": "delete", "section_id": a}])
    assert [n["section_id"] for n in ordered(deleted["nodes"])] == [c]
    assert deleted["contents"][b]["content"] == contents[b]["content"]
    single = mutate(
        deleted["nodes"],
        deleted["contents"],
        [{"op": "restore", "section_id": a, "parent": None, "ordinal": 0}],
    )
    assert [n["section_id"] for n in ordered(single["nodes"])] == [a, c]
    restored = mutate(
        deleted["nodes"],
        deleted["contents"],
        [
            {
                "op": "restore",
                "section_id": a,
                "parent": None,
                "ordinal": 0,
                "include_descendants": True,
            }
        ],
    )
    assert [n["section_id"] for n in ordered(restored["nodes"])] == [a, b, c]
    assert restored["contents"][b]["content"] == contents[b]["content"]


@pytest.mark.parametrize("bad", ["cycle", "duplicate_ordinal", "foreign_parent", "inactive_parent"])
def test_tree_rejects_structural_counterexamples(bad):
    tree, _, (a, b, c) = sample()
    if bad == "cycle":
        tree[0]["parent_id"] = b
    elif bad == "duplicate_ordinal":
        tree[2]["ordinal"] = 0
    elif bad == "foreign_parent":
        tree[1]["parent_id"] = str(uuid4())
    else:
        tree[0]["deleted"] = True
        tree[2]["ordinal"] = 0
    with pytest.raises(DomainError):
        validate_tree(tree)


def test_same_batch_parent_alias_and_reject_move_under_descendant():
    tree, contents, (a, b, c) = sample()
    added = mutate(
        tree,
        contents,
        [
            {
                "op": "add",
                "client_key": "new",
                "parent": None,
                "ordinal": 0,
                "node_type": "chapter",
                "title": "新父章",
            },
            {"op": "move", "section_id": a, "parent": {"client_key": "new"}, "ordinal": 0},
        ],
    )
    assert (
        next(n for n in added["nodes"] if n["section_id"] == a)["parent_id"]
        == added["created_ids"]["new"]
    )
    with pytest.raises(DomainError):
        mutate(
            tree,
            contents,
            [{"op": "move", "section_id": a, "parent": {"section_id": b}, "ordinal": 0}],
        )


@pytest.mark.parametrize(
    "name,body",
    [
        (
            "AuthoringTreeChangeRequest",
            {"base_draft_revision": 1, "changes": [{"op": "execute_sql", "query": "x"}]},
        ),
        (
            "AuthoringTemplateCreate",
            {
                "name": "坏版式",
                "kind": "layout",
                "config": {"font_url": "https://example.invalid/private.ttf"},
            },
        ),
        ("AuthoringPlanSave", {"planned_pages": 0}),
        (
            "AuthoringTreeChangeRequest",
            {
                "base_draft_revision": 1,
                "changes": [{"op": "rename", "section_id": str(uuid4()), "title": "坏\x00标题"}],
            },
        ),
    ],
)
def test_closed_actual_requests_reject_unsupported_or_unsavable_payloads(name, body):
    with pytest.raises(DomainError):
        validate(name, body)


def test_outline_template_keys_are_a_real_tree_and_layout_is_closed():
    from packages.authoring.templates import DEFAULT_STYLE, checked_config

    checked_config("layout", DEFAULT_STYLE)
    with pytest.raises(DomainError):
        checked_config("layout", DEFAULT_STYLE | {"font": "https://example.invalid/font.ttf"})
    with pytest.raises(DomainError):
        checked_config(
            "outline",
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "key": "a",
                        "parent_key": "b",
                        "ordinal": 0,
                        "title": "甲",
                        "node_type": "chapter",
                    },
                    {
                        "key": "b",
                        "parent_key": "a",
                        "ordinal": 0,
                        "title": "乙",
                        "node_type": "chapter",
                    },
                ],
            },
        )
    with pytest.raises(DomainError):
        checked_config(
            "outline",
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "key": "a",
                        "parent_key": None,
                        "ordinal": 1,
                        "title": "甲",
                        "node_type": "chapter",
                    }
                ],
            },
        )
    checked_config(
        "outline",
        {
            "schema_version": 1,
            "nodes": [
                {
                    "key": "a",
                    "parent_key": None,
                    "ordinal": 0,
                    "title": "甲",
                    "node_type": "chapter",
                },
                {
                    "key": "b",
                    "parent_key": "a",
                    "ordinal": 0,
                    "title": "乙",
                    "node_type": "appendix",
                },
            ],
        },
    )
