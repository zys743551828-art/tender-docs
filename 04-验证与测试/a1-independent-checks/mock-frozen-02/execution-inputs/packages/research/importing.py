"""历史资料作为新资料批次录入；预检查不写实交/成绩，确认保留逐项异常。"""

from __future__ import annotations

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, fetch, match, now, sha, uid
from packages.research.batches import load_manifest, save_manifest
from packages.research.core import amount, validate_score
from packages.research.outcomes import register

IMPORT_MEDIA = "application/vnd.tender.historical-import+json"


def record_digest(checked):
    outcome = checked["outcome"]
    identity = {
        "bid_id": checked["bid_id"],
        "body_digest": checked["body_digest"],
        "submitted_at": datetime.fromisoformat(checked["submitted_at"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .isoformat(),
        "outcome": None,
    }
    if outcome is not None:
        identity["outcome"] = {
            "score_kind": outcome["score_kind"],
            "value": None
            if outcome["value"] is None
            else format(amount(outcome["value"]).normalize(), "f"),
            "maximum": None
            if outcome["maximum"] is None
            else format(amount(outcome["maximum"]).normalize(), "f"),
            "source_digest": outcome["source_digest"],
            "known_at": datetime.fromisoformat(outcome["known_at"].replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .isoformat(),
        }
    return sha(identity)


def check_record(c, item):
    bid = item["bid_id"]
    fetch(c, "SELECT id FROM bids WHERE id=%s", (bid,))
    body = fetch(
        c,
        "SELECT d.*,b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.bid_id=%s AND d.id=%s AND d.artifact_kind='bid_body'",
        (bid, item["actual_document_version_id"]),
    )
    fetch(
        c, "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s", (bid, item["evidence_ref"])
    )
    submitted = datetime.fromisoformat(item["submitted_at"].replace("Z", "+00:00"))
    if submitted.tzinfo is None or submitted > now():
        raise DomainError("IMPORT_SUBMISSION_TIME_INVALID", "实际提交时间须明确且不在未来", 422)
    validate_score(item["score_kind"], item["value"], item["maximum"])
    if item["score_kind"] == "unknown" and item["source_version_id"] is None:
        outcome = None
    else:
        if not item["source_version_id"] or not item["known_at"]:
            raise DomainError(
                "IMPORT_OUTCOME_SOURCE_MISSING", "成绩需出处原件与实际取得时间，不能把未知填零", 422
            )
        source = fetch(
            c,
            "SELECT d.id,b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.bid_id=%s AND d.id=%s AND d.artifact_kind='source_original'",
            (bid, item["source_version_id"]),
        )
        known = datetime.fromisoformat(item["known_at"].replace("Z", "+00:00"))
        if known.tzinfo is None or known < submitted or known > now():
            raise DomainError("IMPORT_OUTCOME_TIME_INVALID", "成绩取得时间关系不成立", 422)
        outcome = {
            key: item[key]
            for key in ("score_kind", "value", "maximum", "source_version_id", "known_at")
        }
        outcome["source_digest"] = source["digest"]
    return {
        "bid_id": bid,
        "body_digest": body["digest"],
        "body_version_id": item["actual_document_version_id"],
        "submitted_at": item["submitted_at"],
        "evidence_ref": item["evidence_ref"],
        "outcome": outcome,
    }


def preview(c, *, actor, records, command):
    identifiers = [record["external_record_id"] for record in records]
    if len(identifiers) != len(set(identifiers)):
        raise DomainError("IMPORT_DUPLICATE_RECORD_ID", "同一批次的外部记录编号不得重复", 422)
    manifest = {"schema": "historical-import/1", "records": records}
    blob_id = save_manifest(c, manifest, IMPORT_MEDIA)
    identity = uid()
    c.execute(
        "INSERT INTO import_batches(id,manifest_blob_id,mode,revision,created_by) VALUES(%s,%s,'dry_run',1,%s)",
        (identity, blob_id, actor),
    )
    seen = set()
    for item in records:
        errors = []
        fingerprint = sha(item)
        state = "pending"
        try:
            checked = check_record(c, item)
            fingerprint = record_digest(checked)
            if fingerprint in seen:
                state = "conflict"
                errors.append("duplicate_within_batch")
            seen.add(fingerprint)
            old = c.execute(
                "SELECT bid_id FROM import_records WHERE source_digest=%s AND state='imported' LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if old:
                errors.append("already_imported_will_reuse")
        except DomainError as error:
            state = "conflict"
            errors.append(error.code)
        c.execute(
            "INSERT INTO import_records(batch_id,external_record_id,source_digest,state,bid_id,errors) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                identity,
                item["external_record_id"],
                fingerprint,
                state,
                item["bid_id"] if not errors or errors == ["already_imported_will_reuse"] else None,
                Jsonb(errors),
            ),
        )
    audit(
        c,
        actor,
        None,
        "historical_import.preview_created",
        {"batch_id": identity, "record_count": len(records), "manifest_blob_id": blob_id},
        command,
    )
    return view(c, identity)


def view(c, identity, *, limit=100, offset=0):
    batch = fetch(c, "SELECT * FROM import_batches WHERE id=%s", (identity,))
    rows = c.execute(
        "SELECT * FROM import_records WHERE batch_id=%s ORDER BY external_record_id LIMIT %s OFFSET %s",
        (identity, limit + 1, offset),
    ).fetchall()
    counts = c.execute(
        "SELECT count(*) AS total,count(*) FILTER(WHERE state='pending') AS pending,count(*) FILTER(WHERE state='imported') AS imported,count(*) FILTER(WHERE state IN ('conflict','failed')) AS errors FROM import_records WHERE batch_id=%s",
        (identity,),
    ).fetchone()
    return clean(
        {
            "id": identity,
            "mode": batch["mode"],
            "revision": batch["revision"],
            "manifest_blob_id": batch["manifest_blob_id"],
            "records": rows[:limit],
            "next_offset": offset + limit if len(rows) > limit else None,
            "counts": counts,
            "formal_sample_admission": "separate_verified_technical_label_check",
            "standard_submission_gate_checked": False,
        }
    )


def confirm(c, *, actor, identity, condition, command):
    # 同批次和跨批次同资料确认串行；不能让重复导入制造独立实交/样本。
    c.execute("SELECT pg_advisory_xact_lock(91145,1)")
    batch = fetch(c, "SELECT * FROM import_batches WHERE id=%s FOR UPDATE", (identity,))
    match(condition, "historical-import", identity, batch["revision"])
    if batch["mode"] != "dry_run":
        raise DomainError(
            "IMPORT_ALREADY_CONFIRMED", "批次已经确认，请查看既有结果，不重复增加提交", 409
        )
    manifest = load_manifest(c, batch["manifest_blob_id"], IMPORT_MEDIA)
    if manifest.get("schema") != "historical-import/1":
        raise DomainError("IMPORT_MANIFEST_INVALID", "导入清单用途不匹配", 409)
    for item in manifest["records"]:
        record = fetch(
            c,
            "SELECT * FROM import_records WHERE batch_id=%s AND external_record_id=%s FOR UPDATE",
            (identity, item["external_record_id"]),
        )
        if record["state"] != "pending":
            continue
        try:
            with c.transaction():
                checked = check_record(c, item)
                if record_digest(checked) != record["source_digest"]:
                    raise DomainError(
                        "IMPORT_INPUT_CHANGED", "预检查后资料身份发生变化，请创建新预览", 409
                    )
                old = c.execute(
                    "SELECT bid_id FROM import_records WHERE source_digest=%s AND state='imported' LIMIT 1",
                    (record["source_digest"],),
                ).fetchone()
                if old:
                    c.execute(
                        "UPDATE import_records SET state='imported',bid_id=%s,errors=%s WHERE batch_id=%s AND external_record_id=%s",
                        (
                            old["bid_id"],
                            Jsonb(["reused_existing_import"]),
                            identity,
                            item["external_record_id"],
                        ),
                    )
                    continue
                bid = item["bid_id"]
                fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
                existing = c.execute(
                    "SELECT s.id FROM submission_records s JOIN document_versions d ON d.id=s.body_version_id JOIN blobs b ON b.id=d.blob_id WHERE s.bid_id=%s AND b.digest=%s AND s.submitted_at=%s ORDER BY s.created_at,s.id LIMIT 1",
                    (bid, checked["body_digest"], item["submitted_at"]),
                ).fetchone()
                submission_id = existing["id"] if existing else uid()
                if existing is None:
                    c.execute(
                        "INSERT INTO submission_records(id,bid_id,body_version_id,kind,evidence,submitted_at,created_by) VALUES(%s,%s,%s,'offline_fact',%s,%s,%s)",
                        (
                            submission_id,
                            bid,
                            item["actual_document_version_id"],
                            Jsonb(
                                {
                                    "evidence_ref": item["evidence_ref"],
                                    "explanation": item["explanation"],
                                    "import_batch_id": str(identity),
                                    "external_record_id": item["external_record_id"],
                                    "gate_checked": False,
                                }
                            ),
                            item["submitted_at"],
                            actor,
                        ),
                    )
                c.execute(
                    "INSERT INTO submission_heads(bid_id,submission_id,revision) VALUES(%s,%s,1) ON CONFLICT(bid_id) DO NOTHING",
                    (bid, submission_id),
                )
                if checked["outcome"] is not None:
                    head = c.execute(
                        "SELECT outcome_id FROM outcome_heads WHERE submission_id=%s",
                        (submission_id,),
                    ).fetchone()
                    if head:
                        raise DomainError(
                            "IMPORT_OUTCOME_CONFLICT",
                            "该实交已有成绩，须显式追加更正，不由导入覆盖",
                            409,
                        )
                    outcome_body = {
                        key: item[key]
                        for key in (
                            "score_kind",
                            "value",
                            "maximum",
                            "source_version_id",
                            "known_at",
                        )
                    } | {"submission_id": str(submission_id)}
                    register(c, actor=actor, bid=bid, body=outcome_body, command=command)
                c.execute(
                    "UPDATE import_records SET state='imported',bid_id=%s,errors='[]' WHERE batch_id=%s AND external_record_id=%s",
                    (bid, identity, item["external_record_id"]),
                )
                audit(
                    c,
                    actor,
                    bid,
                    "historical_import.recorded",
                    {
                        "batch_id": identity,
                        "submission_id": submission_id,
                        "gate_checked": False,
                        "sample_admission": "pending_separate_check",
                    },
                    command,
                )
        except DomainError as error:
            c.execute(
                "UPDATE import_records SET state='failed',errors=%s WHERE batch_id=%s AND external_record_id=%s",
                (Jsonb([error.code]), identity, item["external_record_id"]),
            )
    c.execute(
        "UPDATE import_batches SET mode='confirmed_import',revision=revision+1 WHERE id=%s",
        (identity,),
    )
    audit(c, actor, None, "historical_import.confirmed", {"batch_id": identity}, command)
    return view(c, identity)
