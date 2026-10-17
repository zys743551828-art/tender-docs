"""One shared read projection for API, cards and future reports; current heads never guessed."""

from collections import Counter, defaultdict

from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.metrics import records, results, snapshots
from packages.metrics.core import ledger, online_errors, snapshot_totals
from packages.metrics.predictions import eligible_pairs
from packages.research.identities import submission_identity
from packages.research.outcomes import outcome_view


def _scope_time(value, name):
    if value is not None and value.tzinfo is None:
        raise DomainError("METRICS_TIME_INVALID", "统计时间需要明确时区", 422, {"field": name})


def metrics(
    c, *, period_from=None, period_to=None, as_of=None, bid_ids=None, verification_scope="ledger"
):
    at = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    for name, value in (("period_from", period_from), ("period_to", period_to), ("as_of", as_of)):
        _scope_time(value, name)
    if (
        verification_scope not in {"ledger", "verified_only"}
        or as_of
        and as_of > at
        or period_from
        and period_to
        and period_from >= period_to
    ):
        raise DomainError("METRICS_SCOPE_INVALID", "统计口径或时间范围不合法", 422)
    at = as_of or at
    rows = c.execute(
        "SELECT s.*,b.settings,h.revision AS submission_head_revision FROM submission_heads h JOIN submission_records s ON s.id=h.submission_id JOIN bids b ON b.id=s.bid_id WHERE (%s::uuid[] IS NULL OR s.bid_id=ANY(%s::uuid[])) ORDER BY s.bid_id LIMIT 5001",
        (bid_ids, bid_ids),
    ).fetchall()
    if len(rows) > 5000:
        raise DomainError(
            "METRICS_SCOPE_TOO_LARGE", "请缩小项目范围后查询；未截取部分记录冒充完整统计", 413
        )
    excluded: Counter[str] = Counter()
    groups = defaultdict(list)
    selected = []
    for row in rows:
        identity = submission_identity(c, row)
        if identity["status"] != "confirmed":
            excluded[identity["reason"].lower()] += 1
            continue
        value = identity["snapshot"]
        unit = (value["engineering_project_id"], value["round_id"], value["bidder_id"])
        groups[unit].append(row)
    conflicts = []
    for unit, items in groups.items():
        if len(items) != 1:
            conflicts.append({"unit": list(unit), "submission_ids": [str(r["id"]) for r in items]})
            excluded["identity_submission_conflict"] += len(items)
            continue
        row = items[0]
        # submitted_at defines business period; created_at is only an observation availability guard.
        if row["submitted_at"] > at or row["created_at"] > at:
            excluded["not_observed_at_as_of"] += 1
        elif (
            period_from
            and row["submitted_at"] < period_from
            or period_to
            and row["submitted_at"] >= period_to
        ):
            excluded["outside_submitted_at_period"] += 1
        else:
            selected.append(row)
    ledger_rows = []
    digest_heads = []
    frozen = []
    qualification = []
    for row in selected:
        head = records.result_head(c, row["bid_id"], row["id"], as_of=at)
        current = results.get(c, head["result_id"], as_of=at) if head["result_id"] else None
        ledger_rows.append({"result": current})
        frozen.append(snapshots.for_submission(c, row, at))
        qualification.append(snapshots.current_risks(c, row["bid_id"]))
        digest_heads.append(
            {
                "bid_id": str(row["bid_id"]),
                "submission_id": str(row["id"]),
                "submission_revision": row["submission_head_revision"],
                "result_head": head,
                "result_usable": current["usable"] if current else None,
                "result_exclusions": current["unusable_reasons"] if current else [],
            }
        )
    business = ledger(ledger_rows, verification_scope=verification_scope)
    pairs, pair_excluded = eligible_pairs(c, selected, as_of=at)
    known_qual = [q for q in qualification if q["available"]]
    risk = {
        key: sum(q[key] for q in known_qual) if known_qual else None
        for key in ("identified", "resolved", "dismissed", "open")
    }
    risk.update(snapshot_totals(frozen))
    risk.update(
        official_rejections=business["stage_counts"]["rejected"],
        known_current_review_submissions=len(known_qual),
        unknown_current_review_submissions=len(qualification) - len(known_qual),
        official_rejection_causality="not_inferred",
    )
    return clean(
        {
            "scope": {
                "period_from": period_from,
                "period_to": period_to,
                "as_of": at,
                "bid_ids": list(map(str, bid_ids)) if bid_ids else None,
                "statistical_unit": "engineering_lot_round_bidder_current_actual_submission",
                "verification_scope": verification_scope,
                "definition": "fr011-ledger/v1",
                "observed_current_heads": len(rows),
                "effective_head_digest": sha(digest_heads),
                "head_authorities": digest_heads,
                "excluded_submission_counts": dict(excluded),
                "identity_submission_conflicts": conflicts,
                "scope_note": "当前实交头的只读事务观察；时间窗按实交时点，历史as_of遇到没有可证头的记录排除，不猜历史头",
            },
            **business,
            "qualification": risk,
            "online_error": online_errors(pairs, pair_excluded),
            "model_qualification_granted": False,
        }
    )


def _prediction(c, review, visible):
    if not visible:
        return {"mode": "hidden", "reasons": ["project_prediction_hidden"]}
    rows = c.execute(
        "SELECT * FROM predictions WHERE review_id=%s ORDER BY created_at,id", (review["id"],)
    ).fetchall()
    if len(rows) != 1:
        return {"mode": "unavailable", "reasons": ["no_single_fixed_prediction_for_review"]}
    p = rows[0]
    if p["mode"] in {"partial", "unavailable"}:
        return {"mode": p["mode"], "reasons": p["reasons"]}
    return clean(
        {
            "id": p["id"],
            "mode": p["mode"],
            "value": str(p["value"]) if p["value"] is not None else None,
            "maximum": str(p["maximum"]) if p["maximum"] is not None else None,
            "lower_bound": str(p["lower_bound"]) if p["lower_bound"] is not None else None,
            "upper_bound": str(p["upper_bound"]) if p["upper_bound"] is not None else None,
            "reasons": p["reasons"],
        }
    )


def round_trends(c, bid, *, limit=30, after=None):
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (bid,))["settings"]
    reviews = c.execute(
        "SELECT r.*,i.body_version_id,i.manifest,i.input_digest,i.method_digest,i.manifest_digest,o.state AS review_state,rr.core_coverage FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id LEFT JOIN LATERAL(SELECT core_coverage FROM review_results WHERE review_id=r.id ORDER BY created_at DESC,id DESC LIMIT 1)rr ON true WHERE r.bid_id=%s ORDER BY r.created_at,r.id",
        (bid,),
    ).fetchall()
    start = 0
    if after:
        ids = [str(r["id"]) for r in reviews]
        if str(after) not in ids:
            raise DomainError("TREND_CURSOR_INVALID", "走势游标不属于本项目，请重新查询", 422)
        start = ids.index(str(after)) + 1
    selected = reviews[start : start + limit]
    points = []
    for index, review in enumerate(selected, start):
        previous = reviews[index - 1] if index else None
        manifest = review["manifest"]
        reasons = []
        if review["review_state"] != "succeeded" or review["core_coverage"] != "complete":
            reasons.append("review_not_completed_or_scope_incomplete")
        if previous and (
            previous["review_state"] != "succeeded" or previous["core_coverage"] != "complete"
        ):
            reasons.append("previous_review_not_completed_or_scope_incomplete")
        required = ("body_version_id", "rule_set_id", "material_snapshot_id", "content_checks")
        if any(key not in manifest for key in required):
            reasons.append("input_identity_incomplete")
        if previous:
            old = previous["manifest"]
            if any(key not in old for key in required):
                reasons.append("previous_input_identity_incomplete")
            if review["method_digest"] != previous["method_digest"]:
                reasons.append("method_changed")
            if manifest.get("rule_set_id") != old.get("rule_set_id"):
                reasons.append("rules_changed")
            if manifest.get("content_checks") != old.get("content_checks") or manifest.get(
                "material_snapshot_id"
            ) != old.get("material_snapshot_id"):
                reasons.append("scope_changed")
            if manifest.get("fact_set_id") != old.get("fact_set_id"):
                reasons.append("facts_changed")
        findings = c.execute(
            "SELECT id FROM findings WHERE review_id=%s", (review["id"],)
        ).fetchall()
        counts: Counter[str] = Counter({key: 0 for key in ("critical", "major", "minor", "info")})
        roots = set()
        from packages.domain.finding_lifecycle import origin

        for finding in findings:
            value = fetch(c, "SELECT * FROM findings WHERE id=%s", (finding["id"],))
            root = origin(c, value)
            if root["id"] not in roots:
                counts[value["severity"]] += 1
                roots.add(root["id"])
        official = c.execute(
            "SELECT o.*,s.id AS actual_submission_id FROM submission_heads h JOIN submission_records s ON s.id=h.submission_id JOIN outcome_heads oh ON oh.submission_id=s.id JOIN outcomes o ON o.id=oh.outcome_id WHERE s.bid_id=%s AND s.review_id=%s AND s.body_version_id=%s AND o.score_kind='technical_total'",
            (bid, review["id"], review["body_version_id"]),
        ).fetchone()
        official_view = outcome_view(c, official) if official else None
        if official_view:
            submission = c.execute(
                "SELECT * FROM submission_records WHERE id=%s", (official["actual_submission_id"],)
            ).fetchone()
            at = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
            if (
                official_view["verification"] != "verified"
                or results.source_reasons(c, bid, official["source_version_id"], at)
                or submission_identity(c, submission)["status"] != "confirmed"
            ):
                official_view = None
        points.append(
            clean(
                {
                    "review_id": review["id"],
                    "body_version_id": review["body_version_id"],
                    "rule_set_id": manifest.get("rule_set_id"),
                    "fact_set_id": manifest.get("fact_set_id"),
                    "input_digest": review["input_digest"],
                    "manifest_digest": review["manifest_digest"],
                    "method_digest": review["method_digest"],
                    "created_at": review["created_at"],
                    "body_changed": bool(
                        previous and review["body_version_id"] != previous["body_version_id"]
                    ),
                    "review_state": review["review_state"],
                    "core_coverage": review["core_coverage"],
                    "risk_counts": dict(counts)
                    if review["review_state"] == "succeeded"
                    and review["core_coverage"] in {"complete", "partial"}
                    else None,
                    "prediction": _prediction(c, review, settings.get("show_prediction", False)),
                    "official_technical_score": official_view,
                    "comparable_to_previous": bool(previous and not reasons),
                    "break_reasons": sorted(set(reasons)),
                }
            )
        )
    return {
        "bid_id": str(bid),
        "items": points,
        "next_after": str(selected[-1]["id"])
        if selected and start + len(selected) < len(reviews)
        else None,
        "total_reviews": len(reviews),
        "prediction_hidden": not settings.get("show_prediction", False),
        "scope_note": "每轮固定输入；不可比处断开；不计算改稿因果收益，官方分只关联真实当前实交稿",
    }
