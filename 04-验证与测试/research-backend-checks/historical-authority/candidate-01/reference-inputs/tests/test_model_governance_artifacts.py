"""Explicit synthetic artifact loader and shadow math. Never business qualification."""

import copy
import hashlib
import json
from uuid import uuid4

import pytest

from packages.model_governance import artifacts, evaluation, statistics
from packages.model_governance.common import GovernanceError, digest
from packages.model_governance.features import FEATURES, SCHEMA


def document():
    rows = [
        {
            "id": "s1",
            "project_id": "p1",
            "group_id": "g1",
            "method": "a" * 64,
            "features": [60, 1, 0, 1],
            "target": 50,
        }
    ]
    model = statistics.fit(rows, "mean")
    return {
        "schema": "registered-score-artifact/1",
        "model_id": str(uuid4()),
        "training_job_id": str(uuid4()),
        "training_result_digest": "b" * 64,
        "cohort_digest": "c" * 64,
        "protocol_digest": "d" * 64,
        "judgment_method_digest": "a" * 64,
        "feature_schema": SCHEMA,
        "origin": "synthetic",
        "research_only": True,
        "formal_eligibility": "unavailable",
        "scope": {
            "judgment_method_digest": "a" * 64,
            "feature_schema": SCHEMA,
            "engineering_types": ["synthetic building"],
            "maximums": [60],
        },
        "model": model,
        "model_digest": digest(model),
        "intervals": evaluation.conformal(model, [], levels=[0.8, 0.9]),
        "baselines": {
            family: statistics.fit(
                rows, family, **({"shrinkage": 1} if family == "method_shrinkage" else {})
            )
            for family in ("mean", "median", "method_shrinkage", "raw_band")
        },
    }


def features():
    return {
        "schema": SCHEMA,
        "status": "complete",
        "feature_names": list(FEATURES),
        "values": [60, 1, 0, 1],
        "technical_maximum": "60",
        "reasons": [],
    }


def infer(doc, **kwargs):
    options = {
        "method_digest": "a" * 64,
        "engineering_type": "synthetic building",
        "technical_maximum": "60",
        "current": True,
    }
    options.update(kwargs)
    return artifacts.infer(doc, features(), **options)


def test_exact_bytes_registry_and_original_scale_hand_calculation():
    doc = document()
    raw = artifacts.encode(doc)
    loaded = artifacts.load(
        raw,
        expected_digest=hashlib.sha256(raw).hexdigest(),
        expected_size=len(raw),
        expected_document=doc,
    )
    result = infer(loaded)
    assert float(result["value"]) == 30  # synthetic mean 50/100 × original maximum 60
    assert result["intervals"] == {"0.8": None, "0.9": None}
    assert result["mode"] == "research_candidate" and result["formal_eligibility"] == "unavailable"


@pytest.mark.parametrize("mutation", ["bytes", "size", "digest", "document"])
def test_actual_bytes_and_registry_document_are_separate_checks(mutation):
    doc = document()
    raw = artifacts.encode(doc)
    options = {
        "expected_digest": hashlib.sha256(raw).hexdigest(),
        "expected_size": len(raw),
        "expected_document": copy.deepcopy(doc),
    }
    if mutation == "bytes":
        raw += b" "
    elif mutation == "size":
        options["expected_size"] += 1
    elif mutation == "digest":
        options["expected_digest"] = "e" * 64
    else:
        options["expected_document"]["model_id"] = str(uuid4())
    with pytest.raises(GovernanceError):
        artifacts.load(raw, **options)


@pytest.mark.parametrize(
    "raw", [b"\x80\x04cos\nsystem\n.", b'{"schema":"x","schema":"y"}', b'{"model":NaN}', b"\xff"]
)
def test_no_pickle_duplicate_json_keys_nonfinite_or_non_utf8(raw):
    with pytest.raises(GovernanceError):
        artifacts.load(
            raw,
            expected_digest=hashlib.sha256(raw).hexdigest(),
            expected_size=len(raw),
            expected_document=document(),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("script", "execute"),
        ("research_only", False),
        ("formal_eligibility", "accepted"),
        ("origin", "uncertain"),
        ("model_digest", "e" * 64),
    ],
)
def test_untrusted_capabilities_or_altered_model_do_not_load(field, value):
    doc = document()
    doc[field] = value
    with pytest.raises(GovernanceError):
        artifacts.encode(doc)


@pytest.mark.parametrize(
    "options",
    [
        {"current": False},
        {"method_digest": "f" * 64},
        {"engineering_type": "unseen"},
        {"technical_maximum": "100"},
    ],
)
def test_stale_or_scope_mismatched_candidate_is_not_newly_used(options):
    with pytest.raises(GovernanceError):
        infer(document(), **options)


def test_unknown_features_remain_null_but_real_zero_is_a_valid_research_estimate():
    doc = document()
    value = features() | {"status": "unavailable", "values": None, "reasons": ["missing_rule"]}
    result = artifacts.infer(
        doc,
        value,
        method_digest="a" * 64,
        engineering_type="synthetic building",
        technical_maximum="60",
        current=True,
    )
    assert result["value"] is None and result["mode"] == "unavailable"
    doc["model"]["parameters"]["constant"] = 0
    doc["model_digest"] = digest(doc["model"])
    assert float(infer(doc)["value"]) == 0


def test_shadow_missing_business_baseline_not_invented_and_hidden_values_not_serialized():
    candidate = infer(document())
    result = artifacts.compare(candidate, None, show_prediction=True)
    assert result["comparison_status"] == "unavailable" and result["difference"] is None
    baseline = candidate | {"value": "28"}
    assert artifacts.compare(candidate, baseline, show_prediction=True)["difference"] == "2.0"
    hidden = artifacts.compare(candidate, baseline, show_prediction=False)
    assert hidden == {
        "prediction_hidden": True,
        "comparison_status": "hidden",
        "formal_eligibility": "unavailable",
    }
    assert all(
        key not in json.dumps(hidden)
        for key in ("intervals", "candidate", "baseline", "difference", "value")
    )
    with pytest.raises(GovernanceError, match="Same frozen scale"):
        artifacts.compare(candidate, baseline | {"maximum": "100"}, show_prediction=True)


def test_unknown_engineering_type_scope_stays_unknown_not_universal():
    doc = document()
    doc["scope"]["engineering_types"] = [None]
    assert infer(doc, engineering_type=None)["mode"] == "research_candidate"
    with pytest.raises(GovernanceError):
        infer(doc, engineering_type="unseen real type")
