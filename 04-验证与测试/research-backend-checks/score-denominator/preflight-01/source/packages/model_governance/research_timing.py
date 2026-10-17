"""Explicit retrospective timing and group partition; never modifies strict StageA.

Server authority must supply frozen rows, historical Subject grouping and current
label acquisition instants. This module does not authenticate any client row.
"""

from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from packages.model_governance.common import GovernanceError, digest, instant

MODE = "retrospective_submission_time_holdout"


class RetrospectiveTimingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["retrospective_submission_time_holdout"]
    submission_split_at: datetime
    label_available_as_of: datetime

    @field_validator("submission_split_at", "label_available_as_of")
    @classmethod
    def aware(cls, value):
        return instant(value)


def freeze(request, *, training_frozen_at):
    value = RetrospectiveTimingRequest.model_validate(request)
    frozen = instant(training_frozen_at)
    if value.label_available_as_of > frozen:
        raise GovernanceError(
            "RETROSPECTIVE_FUTURE_LABEL_CUTOFF", "Actual label cutoff cannot exceed server freeze"
        )
    if value.submission_split_at > frozen:
        raise GovernanceError(
            "RETROSPECTIVE_FUTURE_SPLIT", "Retrospective submission boundary cannot be in future"
        )
    result = value.model_dump(mode="json") | {
        "schema": "historical-research-timing/1",
        "training_frozen_at": frozen.isoformat(),
        "group_spanning_policy": "exclude",
        "prediction_timing": "retrospective",
        "research_only": True,
        "formal_eligibility": "unavailable",
    }
    return result | {"digest": digest(result)}


def partition(timing, members):
    if timing.get("mode") != MODE or timing.get("digest") != digest(
        {k: v for k, v in timing.items() if k != "digest"}
    ):
        raise GovernanceError(
            "RETROSPECTIVE_TIMING_IDENTITY", "Exact separately frozen retrospective timing required"
        )
    boundary, label_cutoff, frozen = (
        instant(timing[k])
        for k in ("submission_split_at", "label_available_as_of", "training_frozen_at")
    )
    if label_cutoff > frozen or timing.get("group_spanning_policy") != "exclude":
        raise GovernanceError("RETROSPECTIVE_TIMING_INVALID", "Timing policy changed after freeze")
    if not members or len({m["id"] for m in members}) != len(members):
        raise GovernanceError(
            "RETROSPECTIVE_MEMBERSHIP", "Unique nonempty frozen membership required"
        )
    # Server-frozen connected groups must already include all subject project/body/
    # template/near-duplicate relationships; detect any undeclared bridging here too.
    by_key: dict[tuple[str, str], str] = {}
    for row in members:
        if not row.get("historical_subject_id") or not row.get("group_id"):
            raise GovernanceError(
                "RETROSPECTIVE_SUBJECT_REQUIRED", "Historical Subject and trusted group required"
            )
        for key in ("project_id", "body_digest", "template_key", "near_duplicate_key"):
            if row.get(key):
                other = by_key.setdefault((key, row[key]), row["group_id"])
                if other != row["group_id"]:
                    raise GovernanceError(
                        "RETROSPECTIVE_GROUP_BRIDGE",
                        "Connected historical subjects must share a group",
                    )
    groups = defaultdict(list)
    for row in members:
        groups[row["group_id"]].append(row)
    assignments, excluded = {}, {}
    for rows in groups.values():
        sides = {"fit_side" if instant(row["submitted_at"]) < boundary else "final" for row in rows}
        group_reason = "group_spans_submission_split" if len(sides) > 1 else None
        for row in rows:
            submitted, as_of, known = (
                instant(row[k]) for k in ("submitted_at", "historical_as_of", "label_known_at")
            )
            if as_of != submitted or known < submitted:
                raise GovernanceError(
                    "RETROSPECTIVE_TIME_INVALID",
                    "Historical content boundary and actual label acquisition must match events",
                )
            reason = group_reason or (
                "label_after_available_cutoff" if known > label_cutoff else None
            )
            if reason:
                assignments[row["id"]] = "excluded"
                excluded[row["id"]] = reason
            else:
                assignments[row["id"]] = next(iter(sides))
    result = {
        "schema": "historical-research-split/1",
        "timing_digest": timing["digest"],
        "assignments": assignments,
        "exclusions": excluded,
        "members_digest": digest(members),
    }
    return result | {"digest": digest(result)}
