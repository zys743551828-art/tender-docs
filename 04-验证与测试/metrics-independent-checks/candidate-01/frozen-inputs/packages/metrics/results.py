"""Business stage facts are independent of technical outcomes and never inferred from scores."""

from datetime import datetime

from packages.domain.runtime import DomainError, clean, etag, fetch, match, uid
from packages.metrics import records
from packages.research.identities import require_submission_identity, submission_identity


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def _submission(c, bid, identity, current=False):
    row = fetch(c, "SELECT * FROM submission_records WHERE bid_id=%s AND id=%s", (bid, identity))
    if current:
        head = fetch(c, "SELECT submission_id FROM submission_heads WHERE bid_id=%s", (bid,))
        if head["submission_id"] != row["id"]:
            raise DomainError(
                "SUBMISSION_NOT_CURRENT", "请针对当前实际提交登记结果，旧提交仍可查看", 409
            )
    return row


def source_reasons(c, bid, source, as_of):
    if source is None:
        return []
    document = c.execute(
        "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s AND artifact_kind='source_original'",
        (bid, source),
    ).fetchone()
    if not document:
        return ["source_identity_invalid"]
    invalid = c.execute(
        "SELECT DISTINCT h.status,h.valid_from,h.valid_until FROM retrieval.source_nodes s JOIN retrieval.source_ancestors a ON a.source_id=s.id JOIN retrieval.source_heads h ON h.source_id=a.ancestor_id WHERE s.document_version_id=%s AND (h.status='withdrawn' OR h.valid_from>%s OR h.valid_until<=%s)",
        (source, as_of, as_of),
    ).fetchall()
    return ["source_no_longer_effective"] if invalid else []


def _source(c, bid, body):
    if body["source_version_id"]:
        fetch(
            c,
            "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s AND artifact_kind='source_original'",
            (bid, body["source_version_id"]),
        )
    for ref in body["evidence_refs"]:
        if ref["document_version_id"] != body["source_version_id"]:
            raise DomainError("RESULT_SOURCE_MISMATCH", "结果引文必须来自所选原件", 422)
        _reference(c, bid, ref)


def _reference(c, bid, ref):
    row = fetch(
        c,
        "SELECT e.*,p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.bid_id=e.bid_id AND p.id=e.parse_id WHERE e.bid_id=%s AND e.id=%s",
        (bid, ref["evidence_id"]),
    )
    if (
        str(row["document_version_id"]) != ref["document_version_id"]
        or row["physical_page"] != ref["physical_page"]
        or ref["quote"] not in row["text_content"]
    ):
        raise DomainError("RESULT_SOURCE_MISMATCH", "逐字引文、原件和物理页不匹配", 422)
    return row


def get(c, result_id, *, as_of=None):
    row = fetch(
        c, "SELECT * FROM audit_entries WHERE id=%s AND event_type=%s", (result_id, records.RESULT)
    )
    payload = row["payload"]
    head = records.result_head(c, row["bid_id"], payload["submission_id"], as_of=as_of)
    decisions = [
        value
        for value in records.rows(c, row["bid_id"], (records.VERIFY,), as_of=as_of)
        if value["payload"]["result_id"] == str(result_id)
    ]
    decision = decisions[-1] if decisions else None
    submission = _submission(c, row["bid_id"], payload["submission_id"])
    status = decision["payload"]["state"] if decision else "pending"
    at = as_of or c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    reasons = source_reasons(c, row["bid_id"], payload["source_version_id"], at)
    proof = submission_identity(c, submission)
    if proof["status"] != "confirmed":
        reasons.append(proof["reason"].lower())
    if payload["source_type"] == "official" and payload["source_version_id"] is None:
        reasons.append("official_source_missing")
    if status in {"conflict", "rejected"}:
        reasons.append("verification_" + status)
    if head["result_id"] != str(result_id):
        reasons.append("result_not_current")
    return clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            **{
                key: payload[key]
                for key in (
                    "submission_id",
                    "stage",
                    "source_type",
                    "source_version_id",
                    "evidence_refs",
                    "announced_at",
                    "known_at",
                    "reason",
                    "supersedes_id",
                    "correction_reason",
                )
            },
            "created_by": row["actor_id"],
            "created_at": row["created_at"],
            "verification": status,
            "verification_authority_id": str(decision["id"]) if decision else None,
            "verification_details": decision["payload"].get("details") if decision else None,
            "head_revision": head["revision"],
            "head_authority_id": head["authority_id"],
            "current": head["result_id"] == str(result_id),
            "usable": not reasons,
            "unusable_reasons": sorted(set(reasons)),
        }
    )


def list_for_submission(c, bid, submission, *, limit=50, offset=0):
    _submission(c, bid, submission)
    all_rows = [
        r
        for r in records.rows(c, bid, (records.RESULT,))
        if r["payload"]["submission_id"] == str(submission)
    ]
    head = records.result_head(c, bid, submission)
    return {
        "bid_id": str(bid),
        "submission_id": str(submission),
        "current_result": get(c, head["result_id"]) if head["result_id"] else None,
        "head_revision": head["revision"],
        "head_authority_id": head["authority_id"],
        "items": [get(c, r["id"]) for r in all_rows[offset : offset + limit]],
        "next_offset": offset + limit if len(all_rows) > offset + limit else None,
        "total": len(all_rows),
    }


def register(c, *, bid, submission_id, actor, command, body, condition, previous=None):
    records.lock_bid(c, bid)
    submission = _submission(c, bid, submission_id, True)
    require_submission_identity(c, submission)
    if body["submission_id"] != str(submission_id):
        raise DomainError("RESULT_SUBMISSION_MISMATCH", "登记目标与实际提交不一致", 422)
    head = records.result_head(c, bid, submission_id)
    match(condition, "business-result-head", submission_id, head["revision"])
    if previous is None and head["result_id"] is not None:
        raise DomainError("BUSINESS_RESULT_EXISTS", "已有当前结果，请追加更正保留原始记录", 409)
    if previous is not None and (
        head["result_id"] != str(previous) or body.get("base_result_id") != str(previous)
    ):
        raise DomainError("BUSINESS_RESULT_NOT_CURRENT", "只能更正当前结果头，请核对最新差异", 409)
    at = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    known = timestamp(body["known_at"])
    announced = timestamp(body["announced_at"]) if body["announced_at"] else None
    if (
        known > at
        or known < submission["submitted_at"]
        or announced
        and (announced > known or announced < submission["submitted_at"])
    ):
        raise DomainError("BUSINESS_RESULT_TIME_INVALID", "公布、获知与实际提交时点不成立", 422)
    _source(c, bid, body)
    identity = uid()
    payload = {
        key: body[key]
        for key in (
            "submission_id",
            "stage",
            "source_type",
            "source_version_id",
            "evidence_refs",
            "announced_at",
            "known_at",
            "reason",
        )
    }
    payload.update(
        result_id=str(identity),
        head_revision=head["revision"] + 1,
        supersedes_id=str(previous) if previous else None,
        correction_reason=body.get("correction_reason"),
    )
    records.append(
        c,
        bid=bid,
        actor=actor,
        command=command,
        event=records.RESULT,
        payload=payload,
        identity=identity,
    )
    return get(c, identity), etag("business-result-head", submission_id, head["revision"] + 1)


def verify(c, *, result_id, actor, command, condition, body):
    initial = fetch(
        c,
        "SELECT bid_id,payload FROM audit_entries WHERE id=%s AND event_type=%s",
        (result_id, records.RESULT),
    )
    bid = initial["bid_id"]
    records.lock_bid(c, bid)
    row = get(c, result_id)
    if not row["current"]:
        raise DomainError(
            "BUSINESS_RESULT_NOT_CURRENT", "旧结果不能覆盖当前核验，请核对当前头", 409
        )
    head = records.result_head(c, bid, row["submission_id"])
    match(condition, "business-result-head", row["submission_id"], head["revision"])
    if body["state"] == "verified":
        submission = _submission(c, bid, row["submission_id"], True)
        require_submission_identity(c, submission)
        if (
            row["source_type"] != "official"
            or not row["source_version_id"]
            or not row["announced_at"]
            or not body.get("identity_confirmed")
            or not body.get("evidence_refs")
        ):
            raise DomainError(
                "BUSINESS_RESULT_SOURCE_INCOMPLETE",
                "核实需要官方原件、逐字原页、公布时间和实交身份确认",
                422,
            )
        official = c.execute(
            "SELECT 1 FROM document_versions v WHERE v.bid_id=%s AND v.id=%s AND (EXISTS(SELECT 1 FROM material_assignments a WHERE a.document_version_id=v.id AND a.role='official_result') OR EXISTS(SELECT 1 FROM document_file_versions fv JOIN document_files f ON f.id=fv.file_id WHERE fv.version_id=v.id AND f.role='official_result'))",
            (bid, row["source_version_id"]),
        ).fetchone()
        if not official or source_reasons(
            c,
            bid,
            row["source_version_id"],
            c.execute("SELECT clock_timestamp() AS t").fetchone()["t"],
        ):
            raise DomainError("BUSINESS_RESULT_SOURCE_INVALID", "官方原件身份或有效性未确认", 409)
        for ref in body["evidence_refs"]:
            if ref["document_version_id"] != row["source_version_id"]:
                raise DomainError("RESULT_SOURCE_MISMATCH", "核验引文不属于当前结果原件", 422)
            _reference(c, bid, ref)
    records.append(
        c,
        bid=bid,
        actor=actor,
        command=command,
        event=records.VERIFY,
        payload={
            "submission_id": row["submission_id"],
            "result_id": str(result_id),
            "head_revision": head["revision"] + 1,
            "state": body["state"],
            "details": body,
        },
    )
    return get(c, result_id), etag(
        "business-result-head", row["submission_id"], head["revision"] + 1
    )
