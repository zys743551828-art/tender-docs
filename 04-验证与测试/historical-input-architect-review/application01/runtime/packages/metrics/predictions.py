"""Prospective server authority only; historical predictions are never promoted by timestamp."""

from collections import Counter

from packages.domain.review_data import effective_at, source_closure
from packages.domain.runtime import DomainError, fetch
from packages.metrics import records
from packages.metrics.results import timestamp
from packages.metrics.sources import outcome_reasons
from packages.research.identities import submission_identity
from packages.research.outcomes import outcome_view

PRODUCER_EVENT = "formal_prediction.completed"
PROOF_SCHEMA = "formal-score-completion/v1"


def freeze_prediction(c, *, bid, actor, command, prediction_id, proof_event_id):
    records.lock_bid(c, bid)
    prediction = fetch(
        c,
        "SELECT p.*,r.operation_id,i.body_version_id,i.input_digest,i.manifest_digest,i.manifest,o.state AS operation_state,o.finished_at FROM predictions p JOIN review_runs r ON r.id=p.review_id JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE p.bid_id=%s AND p.id=%s",
        (bid, prediction_id),
    )
    proof = c.execute(
        "SELECT * FROM audit_entries WHERE id=%s AND bid_id=%s AND event_type=%s",
        (proof_event_id, bid, PRODUCER_EVENT),
    ).fetchone()
    expected = {
        "schema": PROOF_SCHEMA,
        "prediction_id": str(prediction_id),
        "review_id": str(prediction["review_id"]),
        "input_digest": prediction["input_digest"],
        "manifest_digest": prediction["manifest_digest"],
        "method_digest": prediction["method_digest"],
        "clean_input_verified": True,
        "complete_chapters_verified": True,
        "unseen_labels_verified": True,
    }
    if (
        not proof
        or any(proof["payload"].get(k) != v for k, v in expected.items())
        or prediction["operation_state"] != "succeeded"
        or prediction["value"] is None
        or prediction["mode"] not in {"raw_uncalibrated", "accepted"}
    ):
        raise DomainError(
            "PROSPECTIVE_PREDICTION_NOT_AVAILABLE",
            "没有服务器可证的干净输入、完整章节及事前评分完成依据",
            409,
        )
    packages = c.execute(
        "SELECT p.id,p.rule_id,p.required_ranges,p.read_ranges,p.unread_ranges,count(j.*) FILTER(WHERE j.status='valid' AND mc.state='succeeded') AS valid FROM chapter_packages p LEFT JOIN item_judgments j ON j.chapter_package_id=p.id LEFT JOIN model_calls mc ON mc.id=j.model_call_id WHERE p.review_id=%s GROUP BY p.id,p.rule_id,p.required_ranges,p.read_ranges,p.unread_ranges",
        (prediction["review_id"],),
    ).fetchall()
    scoring = c.execute(
        "SELECT id FROM rule_items WHERE rule_set_id=%s AND kind='scoring'",
        (prediction["manifest"].get("rule_set_id"),),
    ).fetchall()
    scoring_ids = {r["id"] for r in scoring}
    if (
        not scoring_ids
        or {p["rule_id"] for p in packages} != scoring_ids
        or any(
            not p["required_ranges"]
            or not p["read_ranges"]
            or p["unread_ranges"]
            or p["valid"] != 3
            for p in packages
        )
    ):
        raise DomainError(
            "PROSPECTIVE_PREDICTION_NOT_AVAILABLE", "完整章节及三次有效判断尚未满足", 409
        )
    if (
        proof["created_at"] < prediction["created_at"]
        or proof["created_at"] < prediction["finished_at"]
    ):
        raise DomainError(
            "PROSPECTIVE_PREDICTION_NOT_AVAILABLE", "预测完成依据早于实际完成动作", 409
        )
    try:
        effective_at(source_closure(c, prediction["manifest"]["source_refs"]), proof["created_at"])
    except (DomainError, KeyError, ValueError):
        raise DomainError(
            "PROSPECTIVE_PREDICTION_NOT_AVAILABLE", "输入来源不满足事前范围", 409
        ) from None
    # Any already-known label prevents a later freeze from masquerading as a prelabel prediction.
    known = c.execute("SELECT 1 FROM outcomes WHERE bid_id=%s LIMIT 1", (bid,)).fetchone()
    if known:
        raise DomainError(
            "PREDICTION_LABEL_ALREADY_KNOWN", "项目已有正式成绩记录，不能再授事前预测资格", 409
        )
    old = [
        r
        for r in records.rows(c, bid, (records.PREDICTION,))
        if r["payload"]["review_id"] == str(prediction["review_id"])
    ]
    if old:
        if old[0]["payload"]["prediction_id"] != str(prediction_id):
            raise DomainError(
                "PREDICTION_ALREADY_FIXED", "此评审已固定主预测，不能事后择优替换", 409
            )
        return old[0]
    return records.append(
        c,
        bid=bid,
        actor=actor,
        command=command,
        event=records.PREDICTION,
        payload={
            **expected,
            "proof_event_id": str(proof_event_id),
            "body_version_id": str(prediction["body_version_id"]),
            "value": str(prediction["value"]),
            "maximum": str(prediction["maximum"]),
        },
    )


def bind_submission_prediction(c, *, submission, actor, command):
    bid = submission["bid_id"]
    records.lock_bid(c, bid)
    old = [
        r
        for r in records.rows(c, bid, (records.PAIR,))
        if r["payload"]["submission_id"] == str(submission["id"])
    ]
    if old:
        return old[0]["payload"]
    candidates = [
        r
        for r in records.rows(c, bid, (records.PREDICTION,))
        if r["created_at"] < timestamp(submission["submitted_at"])
        and r["payload"]["body_version_id"] == str(submission["body_version_id"])
        and r["payload"]["review_id"] == str(submission.get("review_id"))
    ]
    if len(candidates) != 1:
        return None
    chosen = candidates[0]
    row = records.append(
        c,
        bid=bid,
        actor=actor,
        command=command,
        event=records.PAIR,
        payload={
            "submission_id": str(submission["id"]),
            "prediction_freeze_id": str(chosen["id"]),
            "prediction_id": chosen["payload"]["prediction_id"],
            "review_id": chosen["payload"]["review_id"],
            "body_version_id": chosen["payload"]["body_version_id"],
        },
    )
    return row["payload"]


def eligible_pairs(c, submissions, *, as_of):
    pairs = []
    excluded: Counter[str] = Counter()
    for submission in submissions:
        bid = submission["bid_id"]
        if not submission["settings"].get("show_prediction", False):
            excluded["prediction_hidden"] += 1
            continue
        bindings = [
            r
            for r in records.rows(c, bid, (records.PAIR,), as_of=as_of)
            if r["payload"]["submission_id"] == str(submission["id"])
        ]
        if len(bindings) != 1:
            excluded["no_server_fixed_pre_submission_pair"] += 1
            continue
        binding = bindings[0]["payload"]
        frozen = c.execute(
            "SELECT * FROM audit_entries WHERE id=%s AND event_type=%s",
            (binding["prediction_freeze_id"], records.PREDICTION),
        ).fetchone()
        if not frozen or frozen["created_at"] >= submission["submitted_at"]:
            excluded["prediction_not_pre_submission"] += 1
            continue
        authority = frozen["payload"]
        proof = c.execute(
            "SELECT payload,created_at FROM audit_entries WHERE id=%s AND bid_id=%s AND event_type=%s",
            (authority["proof_event_id"], bid, PRODUCER_EVENT),
        ).fetchone()
        if (
            not proof
            or proof["created_at"] > frozen["created_at"]
            or any(
                proof["payload"].get(k) != authority[k]
                for k in (
                    "schema",
                    "prediction_id",
                    "review_id",
                    "input_digest",
                    "manifest_digest",
                    "method_digest",
                    "clean_input_verified",
                    "complete_chapters_verified",
                    "unseen_labels_verified",
                )
            )
        ):
            excluded["prediction_authority_unavailable"] += 1
            continue
        prediction = c.execute(
            "SELECT p.*,i.body_version_id,i.input_digest,i.manifest_digest FROM predictions p JOIN review_runs r ON r.id=p.review_id JOIN review_inputs i ON i.id=r.input_id WHERE p.bid_id=%s AND p.id=%s",
            (bid, binding["prediction_id"]),
        ).fetchone()
        if (
            not prediction
            or prediction["created_at"] >= submission["submitted_at"]
            or str(prediction["review_id"]) != str(submission["review_id"])
            or str(prediction["body_version_id"]) != str(submission["body_version_id"])
            or prediction["input_digest"] != authority["input_digest"]
            or prediction["manifest_digest"] != authority["manifest_digest"]
            or str(prediction["value"]) != authority["value"]
            or str(prediction["maximum"]) != authority["maximum"]
            or prediction["method_digest"] != authority["method_digest"]
            or prediction["mode"] not in {"raw_uncalibrated", "accepted"}
        ):
            excluded["prediction_submission_input_mismatch"] += 1
            continue
        outcome = c.execute(
            "SELECT o.* FROM outcome_heads h JOIN outcomes o ON o.id=h.outcome_id WHERE h.submission_id=%s",
            (submission["id"],),
        ).fetchone()
        if not outcome:
            excluded["official_outcome_missing"] += 1
            continue
        if outcome["known_at"] > as_of:
            excluded["official_label_after_as_of"] += 1
            continue
        verification_time = c.execute(
            "SELECT d.created_at FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.object_kind='outcome' AND h.object_id=%s",
            (outcome["id"],),
        ).fetchone()
        if not verification_time or verification_time["created_at"] > as_of:
            excluded["official_verification_not_observed_at_as_of"] += 1
            continue
        view = outcome_view(c, outcome)
        if view["verification"] != "verified":
            excluded["official_outcome_" + view["verification"]] += 1
            continue
        if (
            outcome["score_kind"] != "technical_total"
            or outcome["value"] is None
            or outcome["maximum"] is None
        ):
            excluded["not_official_technical_total"] += 1
            continue
        if outcome_reasons(c, outcome, as_of):
            excluded["official_outcome_source_unusable"] += 1
            continue
        if outcome["known_at"] <= frozen["created_at"]:
            excluded["label_known_before_prediction_freeze"] += 1
            continue
        if submission_identity(c, submission)["status"] != "confirmed":
            excluded["submission_identity_unavailable"] += 1
            continue
        pairs.append(
            {
                "predicted": prediction["value"],
                "prediction_maximum": prediction["maximum"],
                "official": outcome["value"],
                "official_maximum": outcome["maximum"],
            }
        )
    return pairs, dict(excluded)
