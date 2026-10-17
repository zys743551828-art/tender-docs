"""FR011 exact denominators and monitoring; absence is never numeric zero."""

from collections import Counter
from decimal import Decimal, localcontext

STAGES = (
    "pending",
    "technical_shortlisted",
    "not_shortlisted",
    "rejected",
    "candidate",
    "awarded",
    "not_awarded",
    "cancelled",
    "failed_tender",
)
VERIFICATIONS = ("pending", "verified", "conflict", "rejected")
SHORTLIST_NUMERATOR = {"technical_shortlisted", "candidate", "awarded", "not_awarded"}
SHORTLIST_DENOMINATOR = SHORTLIST_NUMERATOR | {"not_shortlisted", "rejected"}
WIN_DENOMINATOR = {"awarded", "not_awarded", "not_shortlisted", "rejected"}


def decimal_text(value, *, signed=False):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if signed and value >= 0:
        return "+" + text
    return text


def ratio(numerator, denominator):
    with localcontext() as context:
        context.prec = 28
        value = decimal_text(Decimal(numerator) / Decimal(denominator)) if denominator else None
    return {"numerator": numerator, "denominator": denominator, "value": value}


def ledger(rows, *, verification_scope="ledger"):
    counts = Counter({stage: 0 for stage in STAGES if stage != "pending"})
    excluded: Counter[str] = Counter()
    verification = Counter({key: 0 for key in VERIFICATIONS})
    known = unknown = conflict = unusable = 0
    for row in rows:
        result = row.get("result")
        if result is None:
            unknown += 1
            continue
        status = result["verification"]
        verification[status] += 1
        if status in {"conflict", "rejected"}:
            conflict += 1
            excluded["verification_" + status] += 1
        elif not result["usable"]:
            unusable += 1
            excluded.update(result["unusable_reasons"])
        elif verification_scope == "verified_only" and status != "verified":
            unusable += 1
            excluded["not_verified_in_selected_scope"] += 1
        elif result["stage"] == "pending":
            unknown += 1
        else:
            known += 1
            counts[result["stage"]] += 1
    return {
        "effective_submissions": len(rows),
        "known_outcomes": known,
        "unknown_outcomes": unknown,
        "conflicted_outcomes": conflict,
        "unusable_outcomes": unusable,
        "stage_counts": dict(counts),
        "verification_counts": dict(verification),
        "excluded_result_counts": dict(excluded),
        "shortlist_rate": ratio(
            sum(counts[s] for s in SHORTLIST_NUMERATOR),
            sum(counts[s] for s in SHORTLIST_DENOMINATOR),
        ),
        "win_rate": ratio(counts["awarded"], sum(counts[s] for s in WIN_DENOMINATOR)),
    }


def online_errors(pairs, excluded=None):
    errors = []
    for pair in pairs:
        predicted, maximum = (
            Decimal(str(pair["predicted"])),
            Decimal(str(pair["prediction_maximum"])),
        )
        official, official_maximum = (
            Decimal(str(pair["official"])),
            Decimal(str(pair["official_maximum"])),
        )
        if (
            not all(value.is_finite() for value in (predicted, maximum, official, official_maximum))
            or maximum <= 0
            or official_maximum <= 0
        ):
            raise ValueError("invalid eligible error pair")
        errors.append(
            Decimal(100) * predicted / maximum - Decimal(100) * official / official_maximum
        )
    count = len(errors)
    return {
        "prediction_pairs": count,
        "needed_pairs": 20,
        "online_error_state": "no_pairs"
        if count == 0
        else "insufficient"
        if count < 20
        else "sufficient",
        "mae": decimal_text(sum(map(abs, errors)) / Decimal(count)) if count >= 20 else None,
        "bias": decimal_text(sum(errors) / Decimal(count), signed=True) if count >= 20 else None,
        "excluded_pair_counts": dict(excluded or {}),
        "scope_note": "仅当前可见、服务器可证事前固定预测与已核实官方技术总分；20对仅为展示门槛，不授模型资格",
    }


def snapshot_totals(snapshots):
    known = [
        item
        for item in snapshots
        if item and item.get("basis") != "unknown" and item.get("open") is not None
    ]
    return {
        "open_at_submission": sum(item["open"] for item in known) if known else None,
        "known_snapshot_submissions": len(known),
        "unknown_snapshot_submissions": len(snapshots) - len(known),
        "scope_note": "仅汇总已知冻结快照；未记录或当时依据未知的实交不补0",
    }
