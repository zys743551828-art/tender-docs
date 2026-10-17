"""Real pg_dump reparse case: one exact equivalent pair, strict negative controls."""

from copy import deepcopy

import pytest

from deploy.application_recovery import definition_equivalence as d


def definitions(expression):
    return {"constraints": [[*d.IDENTITY, expression]], "acl": [["controlled", "SELECT"]]}


def test_exact_reviewed_nested_and_flat_pair_without_mutating_archive():
    original = definitions(d.NESTED)
    before = deepcopy(original)
    assert d.equivalent(original, definitions(d.FLAT))
    assert d.equivalent(definitions(d.FLAT), original)
    assert original == before


@pytest.mark.parametrize(
    "expression",
    [
        d.FLAT.replace("2097152", "2097153"),
        d.FLAT.replace("octet_length", "length"),
        d.FLAT.replace("::jsonb", "::json"),
        d.FLAT.replace(" AND ", " OR ", 1),
        d.FLAT.replace("'object'", "'array'"),
        d.FLAT.replace(">= 1", ">= 0"),
        d.FLAT.replace("'object'::text", "'object'"),
    ],
)
def test_unrecognized_sql_change_rejected(expression):
    assert not d.equivalent(definitions(d.NESTED), definitions(expression))


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_same_spelling_pair_for_other_identity_rejected(index):
    source, target = definitions(d.NESTED), definitions(d.FLAT)
    source["constraints"][0][index] += "_different"
    target["constraints"][0][index] += "_different"
    assert not d.equivalent(source, target)


def test_acl_and_duplicated_constraint_and_owner_changes_rejected():
    original = definitions(d.NESTED)
    target = definitions(d.FLAT)
    target["acl"][0][1] = "UPDATE"
    assert not d.equivalent(original, target)
    target = definitions(d.FLAT)
    target["constraints"].append(target["constraints"][0])
    assert not d.equivalent(original, target)
    target = definitions(d.FLAT)
    target["owners"] = ["another_owner"]
    assert not d.equivalent(original, target)


def test_unrelated_nested_boolean_constraint_is_not_normalized():
    source, target = (
        definitions("CHECK (((a AND b) AND c))"),
        definitions("CHECK ((a AND b AND c))"),
    )
    assert not d.equivalent(source, target)
