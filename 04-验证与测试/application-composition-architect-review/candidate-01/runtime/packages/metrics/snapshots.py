"""Server observations and immutable submission risk snapshots, never GET-time backfills."""

from packages.domain.finding_lifecycle import decision, lifecycle, origin, rule_key
from packages.domain.review_data import effective_at, source_closure
from packages.domain.reviews import method_profile
from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.metrics import records
from packages.metrics.results import timestamp

OBSERVATION = records.OBSERVATION


def current_risks(c, bid, review_id=None):
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    chosen = review_id or profile["current_review_id"]
    unavailable: dict = {
        "available": False,
        "review_id": str(chosen) if chosen else None,
        "identified": None,
        "resolved": None,
        "dismissed": None,
        "open": None,
        "scope_issue_ids": [],
        "roots": [],
        "scope_note": "没有当前适用且完成的评审，风险总数未知",
    }
    if not chosen or str(chosen) != str(profile["current_review_id"]):
        return unavailable
    review = c.execute(
        "SELECT r.id,r.created_at,i.body_version_id,i.material_snapshot_id,i.manifest,i.input_digest,i.method_digest,o.state,o.finished_at,rr.core_coverage FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id LEFT JOIN LATERAL(SELECT core_coverage FROM review_results WHERE review_id=r.id ORDER BY created_at DESC,id DESC LIMIT 1)rr ON true WHERE r.bid_id=%s AND r.id=%s",
        (bid, chosen),
    ).fetchone()
    if (
        not review
        or review["state"] != "succeeded"
        or str(review["body_version_id"]) != str(profile["current_body_id"])
        or str(review["material_snapshot_id"]) != str(profile["current_material_id"])
        or str(review["manifest"].get("rule_set_id")) != str(profile["current_rule_set_id"])
    ):
        return unavailable
    fact_head = c.execute("SELECT fact_set_id FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
    current_fact = str(fact_head["fact_set_id"]) if fact_head and fact_head["fact_set_id"] else None
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (bid,))["settings"]
    checks = {name: bool(settings.get(name, False)) for name in ("consistency", "scope_residue")}
    if (
        review["manifest"].get("fact_set_id") != current_fact
        or review["manifest"].get("content_checks") != checks
        or review["method_digest"] != sha(method_profile())
    ):
        return unavailable
    try:
        closure = source_closure(c, review["manifest"]["source_refs"])
        effective_at(closure, c.execute("SELECT clock_timestamp() AS t").fetchone()["t"])
    except (DomainError, KeyError, ValueError):
        return unavailable
    rules = c.execute(
        "SELECT id FROM rule_items WHERE bid_id=%s AND rule_set_id=%s AND kind IN('disqualification','mandatory')",
        (bid, profile["current_rule_set_id"]),
    ).fetchall()
    keys = {rule_key(c, r["id"]) for r in rules}
    findings = c.execute(
        "SELECT f.*,r.created_at AS review_created_at,ri.kind AS rule_kind FROM findings f JOIN review_runs r ON r.id=f.review_id LEFT JOIN rule_items ri ON ri.id=f.rule_id WHERE f.bid_id=%s AND r.created_at<=%s ORDER BY r.created_at,f.id",
        (bid, review["created_at"]),
    ).fetchall()
    grouped = {}
    for finding in findings:
        root = origin(c, finding)
        if root["verdict"] in {"satisfied", "not_applicable"}:
            continue
        if (
            finding["rule_kind"] in {"disqualification", "mandatory"}
            and rule_key(c, finding["rule_id"]) in keys
            or finding["rule_id"] is None
            and finding["category"] == "disqualification"
        ):
            grouped[str(root["id"])] = finding
    roots = []
    for root_id, finding in sorted(grouped.items()):
        manual, _ = decision(c, finding)
        state = lifecycle(c, finding)
        roots.append(
            {
                "root_id": root_id,
                "finding_id": str(finding["id"]),
                "state": state,
                "decision_id": str(manual["id"]) if manual else None,
                "evidence_ids": list(map(str, finding["evidence_ids"])),
            }
        )
    checks = c.execute(
        "SELECT m.rule_id,m.verdict,m.state,m.review_id,m.revision,o.finished_at FROM matrix_checks m JOIN review_runs r ON r.id=m.review_id JOIN operations o ON o.id=r.operation_id WHERE m.bid_id=%s ORDER BY m.review_id,m.rule_id",
        (bid,),
    ).fetchall()
    snapshot = {
        "available": True,
        "review_id": str(chosen),
        "body_version_id": str(review["body_version_id"]),
        "manifest_digest": sha(review["manifest"]),
        "input_digest": review["input_digest"],
        "method_digest": review["method_digest"],
        "review_finished_at": review["finished_at"],
        "core_coverage": review["core_coverage"],
        "identified": len(roots),
        "resolved": sum(r["state"] == "resolved_by_recheck" for r in roots),
        "dismissed": sum(r["state"] == "dismissed" for r in roots),
        "open": sum(r["state"] not in {"resolved_by_recheck", "dismissed"} for r in roots),
        "scope_issue_ids": [r["root_id"] for r in roots],
        "roots": roots,
        "check_authorities": clean(checks),
        "current_source_closure": closure,
        "scope_note": "最近适用评审的否决/强制义务去根问题；confirm/defer不是解决",
    }
    snapshot = clean(snapshot)
    return snapshot | {"context_fingerprint": sha(snapshot)}


def observe_review_risks(c, *, bid, actor, command, review_id=None):
    records.lock_bid(c, bid)
    snapshot = current_risks(c, bid, review_id)
    row = records.append(
        c, bid=bid, actor=actor, command=command, event=OBSERVATION, payload={"snapshot": snapshot}
    )
    return {
        "authority_id": str(row["id"]),
        "observed_at": row["created_at"].isoformat(),
        "snapshot": snapshot,
    }


def freeze_submission_risks(c, *, submission, actor, command):
    bid = submission["bid_id"]
    records.lock_bid(c, bid)
    old = [
        r
        for r in records.rows(c, bid, (records.RISK,))
        if r["payload"]["submission_id"] == str(submission["id"])
    ]
    if old:
        return old[0]["payload"]["snapshot"]
    current = current_risks(c, bid, submission.get("review_id"))
    observations = [
        r
        for r in records.rows(c, bid, (OBSERVATION,))
        if r["created_at"] <= timestamp(submission["submitted_at"])
        and r["payload"]["snapshot"].get("body_version_id") == str(submission["body_version_id"])
        and r["payload"]["snapshot"].get("context_fingerprint")
        == current.get("context_fingerprint")
    ]
    at = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    known = bool(
        observations and current.get("available") and current.get("core_coverage") == "complete"
    )
    observation = observations[-1] if known else None
    snapshot = clean(
        {
            "submission_id": submission["id"],
            "review_id": current.get("review_id"),
            "manifest_digest": current.get("manifest_digest"),
            "body_version_id": submission["body_version_id"],
            "scope_issue_ids": current["scope_issue_ids"] if known else [],
            "roots": current["roots"] if known else [],
            "identified": current["identified"] if known else None,
            "open": current["open"] if known else None,
            "resolved": current["resolved"] if known else None,
            "dismissed": current["dismissed"] if known else None,
            "assessment_as_of": submission["submitted_at"],
            "frozen_at": at,
            "basis": "reconstructed_from_audit" if known else "unknown",
            "scope_definition": "disqualification_and_mandatory_root_findings",
            "not_all_submission_blockers": True,
            "observation_authority_id": observation["id"] if observation else None,
            "scope_note": "事前观察及当前完整上下文一致，保守重建否决/强制义务登记快照；不代表全部提交阻断项"
            if known
            else "当时否决/强制义务风险未知；无足够事前依据，不用今日状态补0；不代表全部提交阻断项",
        }
    )
    records.append(
        c,
        bid=bid,
        actor=actor,
        command=command,
        event=records.RISK,
        payload={"submission_id": str(submission["id"]), "snapshot": snapshot},
    )
    return snapshot


def for_submission(c, submission, as_of=None):
    rows = [
        r
        for r in records.rows(c, submission["bid_id"], (records.RISK,), as_of=as_of)
        if r["payload"]["submission_id"] == str(submission["id"])
    ]
    return rows[0]["payload"]["snapshot"] if rows else None
