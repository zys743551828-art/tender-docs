"""Explicit synthetic clock fixtures, not real future or historical model quality."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from packages.model_governance.common import GovernanceError
from packages.model_governance.research_timing import RetrospectiveTimingRequest, freeze, partition


def timing():
    return freeze(
        {
            "mode": "retrospective_submission_time_holdout",
            "submission_split_at": "2019-06-01T00:00:00Z",
            "label_available_as_of": "2026-10-01T00:00:00Z",
        },
        training_frozen_at="2026-10-17T00:00:00Z",
    )


def member(identity, submitted, group=None):
    return {
        "id": identity,
        "historical_subject_id": "subject-" + identity,
        "group_id": group or identity,
        "project_id": group or identity,
        "body_digest": identity,
        "template_key": None,
        "near_duplicate_key": None,
        "submitted_at": submitted,
        "historical_as_of": submitted,
        "label_known_at": "2026-09-01T00:00:00Z",
    }


def test_2026_labels_and_training_do_not_become_2019_prediction_or_future_accuracy():
    value = timing()
    rows = [member("old", "2019-01-01T00:00:00Z"), member("later", "2019-07-01T00:00:00Z")]
    output = partition(value, rows)
    assert output["assignments"] == {"old": "fit_side", "later": "final"}
    assert rows[0]["label_known_at"].startswith("2026") and value["training_frozen_at"].startswith(
        "2026"
    )
    assert value["research_only"] and value["formal_eligibility"] == "unavailable"


def test_related_group_spanning_submission_boundary_is_wholly_excluded():
    rows = [
        member("old", "2019-01-01T00:00:00Z", "same"),
        member("later", "2019-07-01T00:00:00Z", "same"),
    ]
    output = partition(timing(), rows)
    assert set(output["assignments"].values()) == {"excluded"}
    assert set(output["exclusions"].values()) == {"group_spans_submission_split"}


def test_actual_known_after_cutoff_is_excluded_not_backdated():
    row = member("old", "2019-01-01T00:00:00Z")
    row["label_known_at"] = "2026-10-02T00:00:00Z"
    before = deepcopy(row)
    assert partition(timing(), [row])["exclusions"] == {"old": "label_after_available_cutoff"}
    assert row == before


@pytest.mark.parametrize(
    "attack", ["clock_override", "strict_mode", "naive", "future_cutoff", "future_boundary"]
)
def test_client_cannot_rewrite_server_freeze_or_silently_change_strict(attack):
    value = {
        "mode": "retrospective_submission_time_holdout",
        "submission_split_at": "2019-06-01T00:00:00Z",
        "label_available_as_of": "2026-10-01T00:00:00Z",
    }
    if attack == "clock_override":
        value["training_frozen_at"] = "2019-01-01T00:00:00Z"
    elif attack == "strict_mode":
        value["mode"] = "strict_future_submission_holdout"
    elif attack == "naive":
        value["label_available_as_of"] = "2026-10-01T00:00:00"
    elif attack == "future_cutoff":
        value["label_available_as_of"] = "2027-01-01T00:00:00Z"
    else:
        value["submission_split_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises((ValidationError, GovernanceError)):
        freeze(value, training_frozen_at="2026-10-17T00:00:00Z")


@pytest.mark.parametrize(
    "attack",
    [
        "historical_boundary",
        "label_before_submission",
        "bridging",
        "duplicate",
        "missing_subject",
        "timing_digest",
    ],
)
def test_typed_source_time_and_trusted_group_identity_fail_closed(attack):
    value = timing()
    rows = [member("old", "2019-01-01T00:00:00Z"), member("later", "2019-07-01T00:00:00Z")]
    if attack == "historical_boundary":
        rows[0]["historical_as_of"] = "2026-10-17T00:00:00Z"
    elif attack == "label_before_submission":
        rows[0]["label_known_at"] = "2018-01-01T00:00:00Z"
    elif attack == "bridging":
        rows[1]["template_key"] = "same"
        rows[0]["template_key"] = "same"
    elif attack == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif attack == "missing_subject":
        rows[0]["historical_subject_id"] = None
    else:
        value["label_available_as_of"] = "2018-01-01T00:00:00Z"
    with pytest.raises(GovernanceError):
        partition(value, rows)


def test_closed_timing_request_has_no_client_origin_or_labels():
    assert set(RetrospectiveTimingRequest.model_fields) == {
        "mode",
        "submission_split_at",
        "label_available_as_of",
    }
