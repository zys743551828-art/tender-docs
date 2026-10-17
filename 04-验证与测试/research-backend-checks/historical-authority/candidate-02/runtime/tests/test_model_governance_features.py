"""Synthetic complete-chapter feature arithmetic and fail-closed counterexamples."""

import hashlib
from copy import deepcopy

import pytest

from packages.model_governance.common import digest
from packages.model_governance.features import build


def inputs():
    package = {
        "purpose": "formal_score",
        "rule": {
            "id": "rule-a",
            "statement": "Synthetic only",
            "conditions": [],
            "exceptions": [],
            "bands": [
                {"id": "low", "title": "low", "description": "synthetic", "points": "0"},
                {"id": "high", "title": "high", "description": "synthetic", "points": "30"},
            ],
            "maximum": "30",
        },
        "body_version_id": "body",
        "parse_id": "parse",
        "sections": [
            {
                "id": "full",
                "pages": [1],
                "blocks": [
                    {
                        "evidence_id": "evidence",
                        "physical_page": 1,
                        "text": "Complete synthetic chapter",
                        "digest": hashlib.sha256(b"Complete synthetic chapter").hexdigest(),
                    }
                ],
            }
        ],
        "method_digest": "synthetic-method",
    }
    package["digest"] = digest(package)
    result = {
        "package_digest": package["digest"],
        "valid_replicates": 3,
        "judgments": [
            {
                "replicate": i,
                "call_id": str(i),
                "payload": {
                    "band_id": "high" if i < 3 else "low",
                    "reason": "Synthetic reason",
                    "evidence": [{"evidence_id": "evidence", "quote": "synthetic"}],
                    "coverage": "complete",
                    "status": "valid",
                },
            }
            for i in range(1, 4)
        ],
    }
    return package, result


def test_feature_arithmetic_uses_exact_actual_bands_and_no_invented_dimensions():
    package, result = inputs()
    output = build([package], [result], technical_maximum="30", maximum_evidence_verified=True)
    assert output["status"] == "complete"
    assert output["values"] == pytest.approx([200 / 3, 2 / 3, 100, 1])
    assert output["feature_names"] == [
        "raw_percent",
        "modal_share",
        "spread_percent",
        "evidence_share",
    ]
    assert output["rule_details"][0]["raw_points"] == "20"


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "reuse",
        "invalid",
        "null_band",
        "citation",
        "unread",
        "wrong_max",
        "unknown_max",
        "duplicate_rule",
        "label_injection",
        "corrupt_text",
    ],
)
def test_incomplete_or_dirty_features_return_unavailable_not_zero(attack):
    package, result = inputs()
    kwargs = {"technical_maximum": "30", "maximum_evidence_verified": True}
    packages, results = [package], [result]
    if attack == "missing":
        result["judgments"].pop()
    elif attack == "reuse":
        result["judgments"][1]["call_id"] = result["judgments"][0]["call_id"]
    elif attack == "invalid":
        result["judgments"][0]["payload"]["status"] = "invalid"
    elif attack == "null_band":
        result["judgments"][0]["payload"]["band_id"] = None
    elif attack == "citation":
        result["judgments"][0]["payload"]["evidence"][0]["quote"] = "not in input"
    elif attack == "unread":
        result["judgments"][0]["payload"]["coverage"] = "partial"
    elif attack == "wrong_max":
        kwargs["technical_maximum"] = "100"
    elif attack == "unknown_max":
        kwargs["maximum_evidence_verified"] = False
    elif attack == "duplicate_rule":
        packages.append(deepcopy(package))
        results.append(deepcopy(result))
    elif attack == "label_injection":
        package["official_score"] = "25"
        package["digest"] = digest({k: v for k, v in package.items() if k != "digest"})
        result["package_digest"] = package["digest"]
    elif attack == "corrupt_text":
        package["sections"][0]["blocks"][0]["text"] += "unverified"
        package["digest"] = digest({k: v for k, v in package.items() if k != "digest"})
        result["package_digest"] = package["digest"]
    output = build(packages, results, **kwargs)
    assert output["status"] == "unavailable" and output["values"] is None and output["reasons"]
