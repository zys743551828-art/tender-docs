"""Connected source closure and typed time gates before any statistical fitting."""

from packages.model_governance.common import GovernanceError, digest, instant

PURPOSES = {"fit", "development", "calibration", "final", "excluded"}


def check_members(rows, *, test_start):
    boundary = instant(test_start)
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise GovernanceError("MEMBERSHIP_INVALID", "Unique frozen members required")
    parent = {r["id"]: r["id"] for r in rows}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["purpose"] not in PURPOSES:
            raise GovernanceError("PURPOSE_INVALID", "Unsupported statistical purpose")
        for field in (
            "project_id",
            "body_digest",
            "template_key",
            "near_duplicate_key",
            "group_id",
        ):
            value = row.get(field)
            if value:
                previous = seen.setdefault((field, value), row["id"])
                a, b = root(previous), root(row["id"])
                parent[max(a, b)] = min(a, b)
    assignments: dict[str, str] = {}
    for row in rows:
        side = row["purpose"]
        if side == "excluded":
            continue
        group = root(row["id"])
        if group in assignments and assignments[group] != side:
            raise GovernanceError(
                "RELATED_GROUP_LEAK", "Connected source closure crosses a purpose boundary"
            )
        assignments[group] = side
        known, submitted = instant(row["known_at"]), instant(row["submitted_at"])
        if known < submitted:
            raise GovernanceError("TIME_INVALID", "Official label predates submission")
        if side != "final" and (known >= boundary or submitted >= boundary):
            raise GovernanceError(
                "FUTURE_LABEL_LEAK", "Fit, development and calibration must precede test start"
            )
        if side == "final" and submitted < boundary:
            raise GovernanceError(
                "FINAL_TIME_INVALID", "Final projects must follow the frozen boundary"
            )
    if not {"fit", "development", "final"} <= {r["purpose"] for r in rows}:
        raise GovernanceError(
            "INSUFFICIENT_SPLIT", "Fit, development and final populations required"
        )
    declared: dict[str, set[str]] = {}
    for row in rows:
        declared.setdefault(root(row["id"]), set()).add(row["group_id"])
    if any(len(values) != 1 for values in declared.values()):
        raise GovernanceError(
            "GROUP_IDENTITY_CHANGED",
            "Connected sources require one server-frozen group before folds",
        )
    return {key: root(key) for key in parent}


def group_folds(rows, *, count, seed):
    groups = sorted(
        {row["group_id"] for row in rows}, key=lambda group: digest({"seed": seed, "group": group})
    )
    if type(count) is not int or count < 2 or count > len(groups):
        raise GovernanceError(
            "INSUFFICIENT_GROUP_FOLDS", "Explicit fold count needs independent fitting groups"
        )
    assignment = {group: index % count for index, group in enumerate(groups)}
    return {row["id"]: assignment[row["group_id"]] for row in rows}
