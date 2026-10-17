"""Closed knowledge protocol and no implicit all-scope or formal-score permission."""

import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from packages.domain.runtime import DomainError, now
from packages.knowledge.content import validate_version
from packages.knowledge.contracts import validate_actual


def version():
    return {
        "title": "有据的工法参考",
        "content": "仅作为参考正文，不成为项目事实",
        "categories": ["method"],
        "scope": "具体适用条件",
        "applicability": {
            "bidder_ids": [str(uuid4())],
            "bid_ids": [],
            "regions": ["浙江"],
            "engineering_types": ["市政道路"],
            "conditions": [],
            "purposes": ["authoring"],
            "validity_kind": "bounded",
            "valid_from": now().isoformat(),
            "valid_until": (now() + timedelta(days=1)).isoformat(),
            "validity_basis": "核查原件的时间依据",
            "specificity_note": "原工期及企业事实须另核",
        },
        "source_refs": [
            {
                "source_id": str(uuid4()),
                "source_revision": 2,
                "document_version_id": str(uuid4()),
                "parse_id": str(uuid4()),
                "physical_page": 1,
                "evidence_id": str(uuid4()),
                "excerpt": "必须与原件逐字相符",
            }
        ],
    }


def test_closed_actual_schema_and_semantic_validation():
    body = version()
    validate_actual("KnowledgeVersionRequest", body)
    assert validate_version(body) == body


@pytest.mark.parametrize(
    "field,value",
    [
        ("bidder_ids", []),
        ("regions", []),
        ("engineering_types", []),
        ("purposes", ["formal_score"]),
        ("valid_from", "2026-01-01T00:00:00"),
        ("valid_until", None),
    ],
)
def test_unknown_or_broad_scope_cannot_become_implicit_all(field, value):
    body = copy.deepcopy(version())
    body["applicability"][field] = value
    with pytest.raises(DomainError):
        validate_version(body)


def test_schema_rejects_extra_scope_and_source_url_instead_of_identity():
    body = version()
    body["source_refs"][0]["download_url"] = "https://example.invalid/temporary"
    with pytest.raises(DomainError):
        validate_actual("KnowledgeVersionRequest", body)
