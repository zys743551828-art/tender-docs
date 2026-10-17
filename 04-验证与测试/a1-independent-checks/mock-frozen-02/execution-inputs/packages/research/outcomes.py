"""正式结果追加链、人工核验和严格样本准入（API限定LOGIN）。"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, etag, fetch, match, now, uid
from packages.research.core import connected_groups, validate_score


def outcome_view(c, outcome):
    state = c.execute(
        "SELECT h.revision,d.decision,d.reason,d.evidence_ids FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.bid_id=%s AND h.object_kind='outcome' AND h.object_id=%s",
        (outcome["bid_id"], outcome["id"]),
    ).fetchone()
    value = {
        key: outcome[key]
        for key in (
            "id",
            "bid_id",
            "submission_id",
            "score_kind",
            "value",
            "maximum",
            "source_version_id",
            "known_at",
        )
    }
    value.update(
        verification=state["decision"] if state else "pending",
        verification_revision=state["revision"] if state else 0,
        supersedes_id=outcome["supersedes_id"],
    )
    value["verification_details"] = json.loads(state["reason"]) if state else None
    return clean(value)


def list_outcomes(c, bid):
    fetch(c, "SELECT id FROM bids WHERE id=%s", (bid,))
    rows = c.execute(
        "SELECT * FROM outcomes WHERE bid_id=%s ORDER BY known_at,id", (bid,)
    ).fetchall()
    heads = c.execute("SELECT * FROM outcome_heads WHERE bid_id=%s", (bid,)).fetchall()
    return {"items": [outcome_view(c, row) for row in rows], "heads": clean(heads)}


def context(c, bid, submission_id):
    submission = fetch(
        c,
        "SELECT s.*,d.original_name FROM submission_records s JOIN document_versions d ON d.id=s.body_version_id WHERE s.bid_id=%s AND s.id=%s",
        (bid, submission_id),
    )
    profile = fetch(
        c,
        "SELECT p.*,r.engineering_project_id,r.lot,r.round_no,e.title AS project_title,b.name AS bidder_name FROM bid_profiles p JOIN bid_rounds r ON r.id=p.round_id JOIN engineering_projects e ON e.id=r.engineering_project_id JOIN bidders b ON b.id=p.bidder_id WHERE p.bid_id=%s",
        (bid,),
    )
    sources = c.execute(
        "SELECT d.id,d.original_name,d.obtained_at FROM document_versions d WHERE d.bid_id=%s AND d.artifact_kind='source_original' AND (EXISTS(SELECT 1 FROM material_assignments a WHERE a.bid_id=d.bid_id AND a.document_version_id=d.id AND a.role='official_result') OR EXISTS(SELECT 1 FROM document_files f JOIN document_file_versions v ON v.file_id=f.id WHERE v.version_id=d.id AND f.role='official_result')) ORDER BY d.created_at DESC,d.id DESC LIMIT 100",
        (bid,),
    ).fetchall()
    materials = c.execute(
        "SELECT id,revision,created_at FROM material_snapshots WHERE bid_id=%s ORDER BY revision DESC LIMIT 100",
        (bid,),
    ).fetchall()
    frozen = (
        c.execute(
            "SELECT i.material_snapshot_id FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s",
            (submission["review_id"],),
        ).fetchone()
        if submission["review_id"]
        else None
    )
    return clean(
        {
            "bid_id": bid,
            "submission_id": submission_id,
            "identity": {
                "project_id": str(profile["engineering_project_id"]),
                "round_id": str(profile["round_id"]),
                "bidder_id": str(profile["bidder_id"]),
                "body_version_id": str(submission["body_version_id"]),
            },
            "display": {
                "project_title": profile["project_title"],
                "lot": profile["lot"],
                "round_no": profile["round_no"],
                "bidder_name": profile["bidder_name"],
                "body_filename": submission["original_name"],
            },
            "submitted_at": submission["submitted_at"],
            "kind": submission["kind"],
            "material_snapshot_id": frozen["material_snapshot_id"] if frozen else None,
            "material_is_frozen": frozen is not None,
            "material_candidates": materials,
            "source_candidates": sources,
            "identity_ready": profile["bidder_name"] != "本公司（部署主体待核验）",
        }
    )


def register(c, *, actor, bid, body, command, previous=None, condition=None):
    fetch(c, "SELECT * FROM bids WHERE id=%s FOR UPDATE", (bid,))
    submission = fetch(
        c,
        "SELECT * FROM submission_records WHERE bid_id=%s AND id=%s",
        (bid, body["submission_id"]),
    )
    validate_score(body["score_kind"], body["value"], body["maximum"])
    known = datetime.fromisoformat(body["known_at"].replace("Z", "+00:00"))
    if known > now() or known < submission["submitted_at"]:
        raise DomainError("OUTCOME_TIME_INVALID", "成绩取得时间不能早于实际提交或晚于当前时间", 422)
    fetch(
        c,
        "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s AND artifact_kind='source_original'",
        (bid, body["source_version_id"]),
    )
    head = c.execute(
        "SELECT * FROM outcome_heads WHERE bid_id=%s AND submission_id=%s FOR UPDATE",
        (bid, submission["id"]),
    ).fetchone()
    if previous is not None:
        if not head or head["outcome_id"] != previous:
            raise DomainError(
                "OUTCOME_NOT_CURRENT", "只能更正当前有效成绩，不得跳过未解决的新冲突", 409
            )
        match(condition, "outcome-head", submission["id"], head["revision"])
    elif head:
        raise DomainError(
            "OUTCOME_ALREADY_EXISTS", "此提交已有正式结果，请通过追加更正保留原始记录", 409
        )
    identity, revision = uid(), head["revision"] + 1 if head else 1
    row = c.execute(
        "INSERT INTO outcomes(id,bid_id,submission_id,score_kind,value,maximum,source_version_id,known_at,supersedes_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            identity,
            bid,
            submission["id"],
            body["score_kind"],
            body["value"],
            body["maximum"],
            body["source_version_id"],
            known,
            previous,
            actor,
        ),
    ).fetchone()
    c.execute(
        "INSERT INTO outcome_heads(submission_id,bid_id,outcome_id,revision) VALUES(%s,%s,%s,%s) ON CONFLICT(submission_id) DO UPDATE SET outcome_id=excluded.outcome_id,revision=excluded.revision",
        (submission["id"], bid, identity, revision),
    )
    # 更正前样本立即失去准入；历史outcomes和旧manifest保持可还原。
    c.execute(
        "UPDATE samples SET admission='pending',reasons=%s WHERE submission_id=%s",
        (Jsonb(["outcome_changed"]), submission["id"]),
    )
    audit(
        c,
        actor,
        bid,
        "outcome.corrected" if previous else "outcome.registered",
        {
            "outcome_id": identity,
            "supersedes_id": previous,
            "affected_model_status": "requires_revalidation",
        },
        command,
    )
    return outcome_view(c, row), etag("outcome-head", submission["id"], revision)


def verify(c, *, actor, outcome_id, body, condition, command):
    initial = fetch(c, "SELECT bid_id FROM outcomes WHERE id=%s", (outcome_id,))
    bid = initial["bid_id"]
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    row = fetch(c, "SELECT * FROM outcomes WHERE id=%s", (outcome_id,))
    current = fetch(
        c, "SELECT * FROM outcome_heads WHERE submission_id=%s", (row["submission_id"],)
    )
    if current["outcome_id"] != outcome_id:
        raise DomainError("OUTCOME_NOT_CURRENT", "旧成绩只读，不能用旧核验覆盖当前结果", 409)
    head = c.execute(
        "SELECT * FROM decision_heads WHERE bid_id=%s AND object_kind='outcome' AND object_id=%s FOR UPDATE",
        (bid, outcome_id),
    ).fetchone()
    revision = head["revision"] if head else 0
    match(condition, "outcome-verification", outcome_id, revision)
    if body["state"] == "verified":
        submission = fetch(
            c,
            "SELECT * FROM submission_records WHERE bid_id=%s AND id=%s",
            (bid, row["submission_id"]),
        )
        profile = c.execute(
            "SELECT p.*,r.engineering_project_id,b.name AS bidder_name FROM bid_profiles p JOIN bid_rounds r ON r.id=p.round_id JOIN bidders b ON b.id=p.bidder_id WHERE p.bid_id=%s",
            (bid,),
        ).fetchone()
        expected = (
            None
            if not profile
            else {
                "project_id": str(profile["engineering_project_id"]),
                "round_id": str(profile["round_id"]),
                "bidder_id": str(profile["bidder_id"]),
                "body_version_id": str(submission["body_version_id"]),
            }
        )
        if (
            not expected
            or body["identity"] != expected
            or profile["bidder_name"] == "本公司（部署主体待核验）"
        ):
            raise DomainError(
                "OUTCOME_IDENTITY_UNVERIFIED", "项目、标段轮次、主体或实际提交稿对应关系未核实", 409
            )
        if not body["material_snapshot_id"] or not body["effective_material_confirmed"]:
            raise DomainError(
                "OUTCOME_MATERIAL_UNVERIFIED", "请核实该次提交的生效招标及全部补遗版本", 409
            )
        fetch(
            c,
            "SELECT m.id FROM material_snapshots m WHERE m.bid_id=%s AND m.id=%s AND EXISTS(SELECT 1 FROM material_assignments a WHERE a.snapshot_id=m.id AND a.role='tender')",
            (bid, body["material_snapshot_id"]),
        )
        if not body["aggregation"].strip() or not body["evidence_ids"] or not body["published_at"]:
            raise DomainError(
                "OUTCOME_SOURCE_INCOMPLETE", "核验需成绩原页、聚合方式和发布时间", 422
            )
        published = datetime.fromisoformat(body["published_at"].replace("Z", "+00:00"))
        if (
            published.tzinfo is None
            or published > row["known_at"]
            or published < submission["submitted_at"]
        ):
            raise DomainError("OUTCOME_TIME_INVALID", "公布、取得和提交时间关系不成立", 422)
        refs = c.execute(
            "SELECT e.id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id WHERE e.bid_id=%s AND p.document_version_id=%s AND e.id=ANY(%s::uuid[])",
            (bid, row["source_version_id"], body["evidence_ids"]),
        ).fetchall()
        if {str(ref["id"]) for ref in refs} != set(body["evidence_ids"]):
            raise DomainError("OUTCOME_SOURCE_MISMATCH", "核验引文必须来自本次正式成绩原件", 422)
    decision = uid()
    c.execute(
        "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'outcome',%s,%s,%s,%s,%s,%s)",
        (
            decision,
            bid,
            outcome_id,
            current["revision"],
            body["state"],
            json.dumps(body, ensure_ascii=False),
            body["evidence_ids"],
            actor,
        ),
    )
    c.execute(
        "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'outcome',%s,%s,%s) ON CONFLICT(bid_id,object_kind,object_id) DO UPDATE SET revision=excluded.revision,decision_id=excluded.decision_id",
        (bid, outcome_id, revision + 1, decision),
    )
    c.execute(
        "UPDATE samples SET admission='pending',reasons=%s WHERE submission_id=%s",
        (Jsonb(["verification_changed"]), row["submission_id"]),
    )
    audit(
        c,
        actor,
        bid,
        "outcome.verification_recorded",
        {"outcome_id": outcome_id, "decision_id": decision, "state": body["state"]},
        command,
    )
    return outcome_view(c, row), etag("outcome-verification", outcome_id, revision + 1)


def sample_rows(c):
    rows = c.execute("""SELECT s.*,sr.body_version_id,sr.submitted_at,dv.blob_id,b.digest AS body_digest,
        p.round_id,p.bidder_id,br.engineering_project_id AS project_id,
        o.score_kind,o.value,o.maximum,h.outcome_id AS head_outcome_id,
        d.decision AS verification
        FROM samples s JOIN submission_records sr ON sr.id=s.submission_id
        JOIN document_versions dv ON dv.id=sr.body_version_id JOIN blobs b ON b.id=dv.blob_id
        JOIN outcomes o ON o.id=s.outcome_id JOIN outcome_heads h ON h.submission_id=s.submission_id
        LEFT JOIN bid_profiles p ON p.bid_id=s.bid_id LEFT JOIN bid_rounds br ON br.id=p.round_id
        LEFT JOIN decision_heads dh ON dh.bid_id=s.bid_id AND dh.object_kind='outcome' AND dh.object_id=o.id
        LEFT JOIN decisions d ON d.id=dh.decision_id ORDER BY s.id""").fetchall()
    metadata = c.execute(
        "SELECT h.object_id,d.reason FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.object_kind='sample_group'"
    ).fetchall()
    indexed = {str(row["object_id"]): json.loads(row["reason"]) for row in metadata}
    for row in rows:
        info = indexed.get(str(row["id"]), {})
        row["grouping_reviewed"] = info.get("reviewed", False)
        row["template_group"] = info.get("template_key")
        row["near_duplicate_group"] = info.get("near_duplicate_key")
    return rows


def grouping_view(c, sample_id):
    sample = fetch(c, "SELECT * FROM samples WHERE id=%s", (sample_id,))
    head = c.execute(
        "SELECT h.revision,d.reason FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.object_kind='sample_group' AND h.object_id=%s",
        (sample_id,),
    ).fetchone()
    metadata = (
        json.loads(head["reason"])
        if head
        else {
            "template_key": None,
            "near_duplicate_key": None,
            "reviewed": False,
            "reason": "尚未核对模板与近重复来源",
        }
    )
    related = sample_rows(c)
    groups = connected_groups(related)
    return {
        "sample_id": str(sample_id),
        "source_group_id": groups[str(sample_id)],
        "revision": head["revision"] if head else 0,
        "metadata": metadata,
        "related_sample_ids": [
            str(row["id"]) for row in related if groups[str(row["id"])] == groups[str(sample_id)]
        ],
        "eligible_label_pair": sample["admission"] == "admitted",
    }


def record_grouping(c, *, sample_id, actor, body, condition, command):
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    sample = fetch(c, "SELECT * FROM samples WHERE id=%s", (sample_id,))
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (sample["bid_id"],))
    view = grouping_view(c, sample_id)
    match(condition, "sample-group", sample_id, view["revision"])
    if not body["reason"].strip():
        raise DomainError("INVALID_REQUEST", "请说明模板与近重复来源核对依据", 422)
    identity = uid()
    c.execute(
        "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'sample_group',%s,%s,%s,%s,'{}',%s)",
        (
            identity,
            sample["bid_id"],
            sample_id,
            view["revision"],
            "reviewed" if body["reviewed"] else "pending",
            json.dumps(body, ensure_ascii=False),
            actor,
        ),
    )
    c.execute(
        "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'sample_group',%s,%s,%s) ON CONFLICT(bid_id,object_kind,object_id) DO UPDATE SET revision=excluded.revision,decision_id=excluded.decision_id",
        (sample["bid_id"], sample_id, view["revision"] + 1, identity),
    )
    rows = sample_rows(c)
    groups = connected_groups(rows)
    for row in rows:
        c.execute(
            "UPDATE samples SET source_group_id=%s WHERE id=%s",
            (UUID(groups[str(row["id"])]), row["id"]),
        )
    if any(
        view["metadata"].get(key) != body[key]
        for key in ("template_key", "near_duplicate_key", "reviewed")
    ):
        affected = set(view["related_sample_ids"]) | {
            str(row["id"]) for row in rows if groups[str(row["id"])] == groups[str(sample_id)]
        }
        c.execute(
            "UPDATE research_runs r SET state='paused',revision=r.revision+1 FROM experiment_previews p WHERE p.id=r.preview_id AND r.state IN ('queued','running') AND (p.request->'sample_ids') ?| %s::text[]",
            (list(affected),),
        )
    audit(
        c,
        actor,
        sample["bid_id"],
        "sample.grouping_checked",
        {"sample_id": sample_id, "group_revision": view["revision"] + 1},
        command,
    )
    return grouping_view(c, sample_id)


def admissibility(c, bid, submission):
    sub = fetch(c, "SELECT * FROM submission_records WHERE bid_id=%s AND id=%s", (bid, submission))
    head = fetch(
        c, "SELECT * FROM outcome_heads WHERE bid_id=%s AND submission_id=%s", (bid, submission)
    )
    outcome = fetch(
        c, "SELECT * FROM outcomes WHERE bid_id=%s AND id=%s", (bid, head["outcome_id"])
    )
    view = outcome_view(c, outcome)
    reasons = []
    submission_head = c.execute(
        "SELECT submission_id FROM submission_heads WHERE bid_id=%s", (bid,)
    ).fetchone()
    if not submission_head or submission_head["submission_id"] != sub["id"]:
        reasons.append("submission_not_current")
    if view["verification"] != "verified":
        reasons.append("outcome_" + view["verification"])
    if outcome["score_kind"] != "technical_total" or outcome["value"] is None:
        reasons.append("no_official_technical_label")
    if sub["submitted_at"] > outcome["known_at"]:
        reasons.append("outcome_time_invalid")
    identity = c.execute(
        "SELECT p.bid_id FROM bid_profiles p JOIN bid_rounds r ON r.id=p.round_id WHERE p.bid_id=%s",
        (bid,),
    ).fetchone()
    if not identity:
        reasons.append("project_identity_missing")
    materials = c.execute(
        "SELECT 1 FROM material_assignments WHERE bid_id=%s AND role='tender' LIMIT 1", (bid,)
    ).fetchone()
    if not materials:
        reasons.append("effective_tender_missing")
    body = fetch(
        c,
        "SELECT artifact_kind FROM document_versions WHERE bid_id=%s AND id=%s",
        (bid, sub["body_version_id"]),
    )
    if body["artifact_kind"] != "bid_body":
        reasons.append("actual_submission_is_not_technical_body")
    return sub, outcome, reasons


def admit(c, *, bid, submission, actor, command):
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    sub, outcome, reasons = admissibility(c, bid, submission)
    identity = uid()
    row = c.execute(
        "INSERT INTO samples(id,bid_id,submission_id,outcome_id,source_group_id,known_at,admission,reasons) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(submission_id,outcome_id) DO UPDATE SET admission=excluded.admission,reasons=excluded.reasons RETURNING *",
        (
            identity,
            bid,
            submission,
            outcome["id"],
            identity,
            outcome["known_at"],
            "pending" if reasons else "admitted",
            Jsonb(reasons),
        ),
    ).fetchone()
    # 序列化跨项目组归并。这里只改派生样本组，不改成绩/提交原件。
    rows = sample_rows(c)
    groups = connected_groups(rows)
    for sample in rows:
        group = UUID(groups[str(sample["id"])])
        if sample["source_group_id"] != group:
            c.execute("UPDATE samples SET source_group_id=%s WHERE id=%s", (group, sample["id"]))
        if sample["outcome_id"] != sample["head_outcome_id"]:
            c.execute(
                "UPDATE samples SET admission='pending',reasons=%s WHERE id=%s",
                (Jsonb(["outcome_superseded"]), sample["id"]),
            )
    audit(
        c,
        actor,
        bid,
        "sample.admission_checked",
        {"sample_id": row["id"], "admission": row["admission"], "reasons": reasons},
        command,
    )
    return clean(
        row | {"source_group_id": groups[str(row["id"])], "eligible_label_pair": not reasons}
    )


def history(c, *, query="", limit=30, offset=0):
    rows = c.execute(
        """SELECT b.id,b.title,b.created_at,
        (SELECT count(*) FROM submission_records s WHERE s.bid_id=b.id) AS submission_count,
        (SELECT count(*) FROM outcome_heads h WHERE h.bid_id=b.id) AS outcome_count,
        (SELECT count(*) FROM samples s JOIN outcome_heads h ON h.submission_id=s.submission_id AND h.outcome_id=s.outcome_id WHERE s.bid_id=b.id AND s.admission='admitted') AS admitted_samples
        FROM bids b WHERE b.title ILIKE %s ORDER BY b.created_at DESC,b.id DESC LIMIT %s OFFSET %s""",
        ("%" + query + "%", limit + 1, offset),
    ).fetchall()
    return {
        "items": clean(rows[:limit]),
        "next_offset": offset + limit if len(rows) > limit else None,
        "read_only": True,
    }
