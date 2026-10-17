"""报告展示快照不更改业务输入；版本分配留在API控制面。"""

from pathlib import Path

from apps.api.review_views import finding_view, matrix_view, review_row, review_view, rule_view
from packages.authoring.build import WRITER_DIGEST
from packages.domain.finding_lifecycle import origin, visible_rows
from packages.domain.review_data import effective_at, source_closure
from packages.domain.rule_sets import rules
from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.domain.submission_gate import blocking_findings

REPORT_METHOD_DIGEST = sha(
    (Path(__file__).resolve().parents[2] / "apps/worker/report_handlers.py").read_bytes()
)


def content(c, review_id):
    review = review_row(c, review_id)
    if review["state"] not in ("succeeded", "failed", "cancelled"):
        raise DomainError("REPORT_REVIEW_NOT_FINISHED", "本轮尚未结束，请等待后再生成结果副本", 409)
    bid = fetch(c, "SELECT * FROM bids WHERE id=%s", (review["bid_id"],))
    document = fetch(
        c,
        "SELECT v.id,v.original_name,v.artifact_kind,b.id AS blob_id,b.digest,b.object_key,b.byte_size,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=%s",
        (bid["id"], review["manifest"]["body_version_id"]),
    )
    items = [rule_view(c, row["id"]) for row in rules(c, review["manifest"]["rule_set_id"])]
    finding_rows = visible_rows(c, review_id)
    roots = {origin(c, row)["id"] for row in finding_rows}
    for row in blocking_findings(c, bid["id"]):
        if row["origin_id"] not in roots:
            roots.add(row["origin_id"])
            finding_rows.append(row)
    findings = [finding_view(c, row["id"]) for row in finding_rows]
    evidence_ids = sorted(
        {identity for row in items + findings for identity in row.get("evidence_ids", [])}
    )
    evidence = c.execute(
        "SELECT e.id,e.parse_id,e.physical_page,e.kind,e.text_content,e.bbox,p.document_version_id,v.original_name FROM evidence_blocks e JOIN parse_runs p ON p.bid_id=e.bid_id AND p.id=e.parse_id JOIN document_versions v ON v.bid_id=e.bid_id AND v.id=p.document_version_id WHERE e.bid_id=%s AND e.id=ANY(%s::uuid[]) ORDER BY p.document_version_id,e.physical_page,e.id",
        (bid["id"], evidence_ids),
    ).fetchall()
    source_state, closure = "effective", []
    try:
        closure = source_closure(c, review["manifest"]["source_refs"])
        effective_at(closure, c.execute("SELECT clock_timestamp() AS at").fetchone()["at"])
        if any(
            sha([row for row in closure if row["source_id"] == ref["id"]])
            != ref["lineage_manifest_digest"]
            for ref in review["manifest"]["source_refs"]
        ):
            source_state = "changed"
    except DomainError as error:
        source_state = error.code
    return clean(
        {
            "schema": "review-presentation/1",
            "bid_id": bid["id"],
            "title": bid["title"],
            "review": review_view(c, review_id),
            "manifest": review["manifest"],
            "body": document,
            "rules": items,
            "matrix": matrix_view(c, review_id),
            "findings": findings,
            "evidence": evidence,
            "source_closure": closure,
            "source_state": source_state,
            "prediction_policy": "hidden"
            if not bid["settings"].get("show_prediction", True)
            else "unavailable",
            "writer_digest": WRITER_DIGEST,
            "report_method_digest": REPORT_METHOD_DIGEST,
            "warnings": [
                "评审报告和批注副本不具备干净正文提交资格",
                "本报告不代表交易平台提交成功；未通过正式准入的分数不予预测",
            ],
        }
    )


def context(c, review_id):
    frozen = content(c, review_id)
    bid = fetch(c, "SELECT presentation_revision FROM bids WHERE id=%s", (frozen["bid_id"],))
    prior = c.execute(
        "SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='report.presentation_allocated' ORDER BY (payload->>'presentation_revision')::bigint DESC,id DESC LIMIT 1",
        (frozen["bid_id"],),
    ).fetchone()
    digest = sha(frozen)
    revision = bid["presentation_revision"]
    if prior and prior["payload"]["context_digest"] != digest:
        revision += 1
    return {
        "review_id": str(review_id),
        "body_version_id": frozen["manifest"]["body_version_id"],
        "original_name": frozen["body"]["original_name"],
        "presentation_revision": revision,
        "source_impact_revision": frozen["review"]["source_impact_revision"],
        "context_digest": digest,
        "current": frozen["review"]["current"],
        "source_state": frozen["source_state"],
        "supported": [
            {"artifact_kind": "review_report", "format": "docx"},
            {"artifact_kind": "review_report", "format": "pdf"},
            {"artifact_kind": "annotated_review_copy", "format": "pdf"},
        ],
    }, frozen


def request_row(c, identity):
    return fetch(
        c,
        "SELECT payload FROM audit_entries WHERE event_type='report.requested' AND payload->>'report_id'=%s",
        (str(identity),),
    )["payload"]


def report_view(c, identity):
    record = request_row(c, identity)
    operation = fetch(c, "SELECT state FROM operations WHERE id=%s", (record["operation_id"],))
    version = c.execute(
        "SELECT payload FROM audit_entries WHERE event_type='report.published' AND payload->>'report_id'=%s",
        (str(identity),),
    ).fetchone()
    published = version["payload"] if version else {}
    failed = c.execute(
        "SELECT output->>'error_code' AS code FROM step_receipts WHERE operation_id=%s AND state='failed' ORDER BY step_key LIMIT 1",
        (record["operation_id"],),
    ).fetchone()
    return {
        "id": str(identity),
        "review_id": record["review_id"],
        "operation_id": record["operation_id"],
        "state": operation["state"],
        "artifact_kind": record["artifact_kind"],
        "format": record["format"],
        "presentation_revision": record["presentation_revision"],
        "source_impact_revision": record["source_impact_revision"],
        "document_version_id": published.get("document_version_id"),
        "gaps": published.get("gaps", []),
        "error_code": failed["code"] if failed else None,
    }
