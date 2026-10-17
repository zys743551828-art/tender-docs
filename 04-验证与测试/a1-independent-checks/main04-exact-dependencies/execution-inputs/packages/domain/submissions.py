"""不可变实交事实：标准登记重算闸门，线下补录不伪造通过；更正显式移动头。"""

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from packages.domain.finding_lifecycle import decision, origin
from packages.domain.rule_sets import current_decision, rules
from packages.domain.runtime import DomainError, audit, clean, fetch, uid
from packages.domain.submission_gate import blocking_findings, evaluate
from packages.identity.repository import snapshot as identity_snapshot


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def document(c, bid, identity):
    return fetch(
        c,
        "SELECT v.*,b.object_key,b.digest,b.byte_size,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=%s",
        (bid, identity),
    )


def registration_risks(c, bid):
    result = []
    for finding in blocking_findings(c, bid):
        root = origin(c, finding)
        manual, _ = decision(c, finding)
        result.append(
            {
                "finding_id": str(finding["id"]),
                "origin_id": str(root["id"]),
                "origin_review_id": str(root["review_id"]),
                "review_id": str(finding["review_id"]),
                "rule_id": str(finding["rule_id"]) if finding["rule_id"] else None,
                "severity": finding["severity"],
                "category": finding["category"],
                "lifecycle": finding["lifecycle"],
                "decision_id": str(manual["id"]) if manual else None,
            }
        )
    return result


def _check_standard_time(c, bid, body, at, identity, gate):
    review = fetch(
        c,
        "SELECT o.finished_at,i.manifest FROM review_runs r JOIN operations o ON o.id=r.operation_id JOIN review_inputs i ON i.id=r.input_id WHERE r.bid_id=%s AND r.id=%s",
        (bid, body["review_id"]),
    )
    actual = document(c, bid, body["body_version_id"])
    authority = fetch(
        c,
        "SELECT created_at FROM audit_entries WHERE id=%s AND bid_id=%s",
        (identity["authority_id"], bid),
    )
    if review["finished_at"] > at or actual["created_at"] > at or authority["created_at"] > at:
        raise DomainError(
            "SUBMISSION_CHECKS_POSTDATE_FACT",
            "所选检查、稿件或主体确认晚于实际提交，请使用已发生提交的事实补录入口",
            409,
        )
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (bid,))["settings"]
    if settings.get("accept_rules_as_written", False):
        acceptance = c.execute(
            "SELECT created_at FROM audit_entries WHERE bid_id=%s AND event_type='settings.updated' AND payload->'changed' ? 'accept_rules_as_written' ORDER BY created_at DESC,id DESC LIMIT 1",
            (bid,),
        ).fetchone()
        if not acceptance or acceptance["created_at"] > at:
            raise DomainError(
                "SUBMISSION_CHECKS_POSTDATE_FACT",
                "按原文接受的资格缺少提交前明确依据，请据实补录",
                409,
            )
    else:
        for rule in rules(c, review["manifest"]["rule_set_id"]):
            if (
                rule["kind"] not in ("mandatory", "disqualification")
                or rule["provenance_status"] == "superseded"
            ):
                continue
            confirmed = current_decision(c, bid, "rule", rule["id"])
            if not confirmed or confirmed["created_at"] > at:
                raise DomainError(
                    "SUBMISSION_CHECKS_POSTDATE_FACT",
                    "核心规则的人工确认晚于实际提交，请据实补录，不能回写事前通过",
                    409,
                )


def register(c, bid, actor, command_id, body, *, kind, supersedes=None, correction_reason=None):
    """调用方已bid锁+强ETag；字节I/O须预先完成，失败事务整体回滚。"""
    if kind not in ("standard", "offline_fact"):
        raise DomainError("SUBMISSION_KIND_INVALID", "请选择标准登记或独立事实补录", 422)
    head = c.execute("SELECT * FROM submission_heads WHERE bid_id=%s FOR UPDATE", (bid,)).fetchone()
    if supersedes:
        if not head or str(head["submission_id"]) != str(supersedes):
            raise DomainError(
                "SUBMISSION_HEAD_CONFLICT", "当前实交记录已经变化，请重新核对更正目标", 412
            )
        if not correction_reason or not correction_reason.strip():
            raise DomainError(
                "SUBMISSION_CORRECTION_REASON_REQUIRED", "请填写实际提交依据及本次更正理由", 422
            )
    elif head:
        raise DomainError(
            "SUBMISSION_ALREADY_RECORDED",
            "已有有效实交记录；如需更正，请明确选择原记录及实际依据",
            409,
        )
    profile = fetch(
        c,
        "SELECT b.material_revision,p.current_body_id,p.current_review_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    at = timestamp(body["submitted_at"])
    server_now = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    if at > server_now:
        raise DomainError("SUBMISSION_FUTURE_TIME", "只能登记已经发生的提交，请核对实际时间", 422)
    version = body["body_version_id"] if kind == "standard" else body["actual_document_version_id"]
    actual = document(c, bid, version)
    proof = document(c, bid, body["evidence_ref"])
    identity = identity_snapshot(c, bid)
    gate = None
    if kind == "standard":
        if actual["artifact_kind"] != "bid_body" or actual["media_type"] != "application/pdf":
            raise DomainError(
                "SUBMISSION_BODY_KIND_INVALID",
                "报告、批注副本或非受检PDF不能作标准登记正文；已发生错传可据实补录",
                409,
            )
        gate = evaluate(c, bid, lock_sources=True)
        if not gate["allowed"]:
            raise DomainError(
                "SUBMISSION_GATE_BLOCKED", "提交检查尚有阻断项，请核对后再登记", 409, {"gate": gate}
            )
        if (
            str(profile["current_body_id"]) != version
            or str(profile["current_review_id"]) != body["review_id"]
            or profile["material_revision"] != body["material_revision"]
        ):
            raise DomainError(
                "SUBMISSION_INPUT_CHANGED", "实际稿件、资料或对应评审已更新，请刷新确认卡", 412
            )
        _check_standard_time(c, bid, body, at, identity, gate)
    elif not body["explanation"].strip() or len(body["explanation"]) > 2000:
        raise DomainError(
            "SUBMISSION_EXPLANATION_REQUIRED", "请填写未经过完整检查的原因及已发生提交的依据", 422
        )
    identity_id = uid()
    evidence = {
        "evidence_ref": str(proof["id"]),
        "evidence_digest": proof["digest"],
        "evidence_type": "user_declared_submission_attachment",
        "platform_submission_verified": False,
        "identity": identity,
        "actual_document": {
            "document_version_id": str(actual["id"]),
            "digest": actual["digest"],
            "artifact_kind": actual["artifact_kind"],
            "original_name": actual["original_name"],
        },
        "registration_blocking_findings": registration_risks(c, bid),
        "registration_assessed_at": server_now.isoformat(),
        "checks_label": "核心提交检查已通过" if kind == "standard" else "未经过系统完整检查",
        "explanation": body.get("explanation"),
        "correction_reason": correction_reason,
    }
    prepared = {
        "id": identity_id,
        "bid_id": bid,
        "body_version_id": actual["id"],
        "review_id": body["review_id"] if kind == "standard" else None,
        "kind": kind,
        "submitted_at": at,
        "evidence": evidence,
        "created_by": actor,
        "supersedes_id": supersedes,
        "gate_snapshot": gate,
    }
    from packages.metrics.predictions import bind_submission_prediction
    from packages.metrics.snapshots import freeze_submission_risks

    # 两个hook均支持prepared；不可变记录只INSERT一次，随后不得UPDATE evidence。
    evidence["open_risk_snapshot"] = freeze_submission_risks(
        c, submission=prepared, actor=actor, command=command_id
    )
    evidence["prediction_binding"] = (
        bind_submission_prediction(c, submission=prepared, actor=actor, command=command_id)
        if kind == "standard"
        else None
    )
    c.execute(
        "INSERT INTO submission_records(id,bid_id,body_version_id,review_id,kind,gate_snapshot,evidence,submitted_at,created_by,supersedes_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity_id,
            bid,
            actual["id"],
            prepared["review_id"],
            kind,
            Jsonb(gate) if gate is not None else None,
            Jsonb(clean(evidence)),
            at,
            actor,
            supersedes,
        ),
    )
    if head:
        c.execute(
            "UPDATE submission_heads SET submission_id=%s,revision=revision+1 WHERE bid_id=%s",
            (identity_id, bid),
        )
    else:
        c.execute(
            "INSERT INTO submission_heads(bid_id,submission_id,revision) VALUES(%s,%s,1)",
            (bid, identity_id),
        )
    c.execute("UPDATE bid_profiles SET phase='submitted' WHERE bid_id=%s", (bid,))
    c.execute("UPDATE bids SET revision=revision+1 WHERE id=%s", (bid,))
    audit(
        c,
        actor,
        bid,
        "submission.corrected" if supersedes else "submission.recorded",
        {
            "submission_id": str(identity_id),
            "supersedes_id": str(supersedes) if supersedes else None,
            "kind": kind,
            "body_version_id": str(actual["id"]),
            "gate_checked": kind == "standard",
            "identity_digest": identity["digest"],
            "evidence_digest": proof["digest"],
        },
        command_id,
    )
    return identity_id


def view(c, submission_id):
    row = fetch(
        c,
        "SELECT s.*,d.original_name,d.artifact_kind,h.submission_id AS head_id FROM submission_records s JOIN document_versions d ON d.id=s.body_version_id LEFT JOIN submission_heads h ON h.bid_id=s.bid_id WHERE s.id=%s",
        (submission_id,),
    )
    identity = row["evidence"].get("identity")
    checked = (
        row["kind"] == "standard"
        and isinstance(row["gate_snapshot"], dict)
        and row["gate_snapshot"].get("allowed") is True
    )
    return clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            "body_version_id": row["body_version_id"],
            "review_id": row["review_id"],
            "kind": row["kind"],
            "submitted_at": row["submitted_at"],
            "recorded_by": row["created_by"],
            "gate_checked": checked,
            "evidence_ref": row["evidence"].get("evidence_ref"),
            "current": row["head_id"] == row["id"],
            "supersedes_id": row["supersedes_id"],
            "identity": identity if isinstance(identity, dict) and "digest" in identity else None,
            "original_name": row["original_name"],
            "artifact_kind": row["artifact_kind"],
            "checks_label": "核心提交检查已通过" if checked else "未经过系统完整检查",
            "open_risk_snapshot": row["evidence"].get("open_risk_snapshot"),
            "platform_submission_verified": False,
            "explanation": row["evidence"].get("explanation"),
            "correction_reason": row["evidence"].get("correction_reason"),
        }
    )
