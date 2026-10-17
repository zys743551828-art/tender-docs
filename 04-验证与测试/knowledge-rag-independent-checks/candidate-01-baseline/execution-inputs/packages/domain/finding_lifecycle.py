"""关键问题跨轮保留；只有有效新稿复检或有理由人工驳回才能关闭。"""

from psycopg.types.json import Jsonb

from packages.domain.review_data import effective_at, source_closure
from packages.domain.rule_sets import semantic_key
from packages.domain.runtime import DomainError, fetch, sha, uid


def origin(c, row):
    seen = set()
    while row["carried_from_id"]:
        if row["id"] in seen:
            raise DomainError("FINDING_LINEAGE_INVALID", "问题来源链不合法", 409)
        seen.add(row["id"])
        row = fetch(
            c,
            "SELECT * FROM findings WHERE bid_id=%s AND id=%s",
            (row["bid_id"], row["carried_from_id"]),
        )
    return row


def decision(c, row):
    root = origin(c, row)
    head = c.execute(
        "SELECT h.revision,d.* FROM decision_heads h LEFT JOIN decisions d ON d.id=h.decision_id WHERE h.bid_id=%s AND h.object_kind='finding' AND h.object_id=%s",
        (row["bid_id"], root["id"]),
    ).fetchone()
    return (head if head and head["id"] else None), head["revision"] if head else 1


def rule_key(c, rule_id):
    if rule_id is None:
        return None
    rule = fetch(c, "SELECT * FROM rule_items WHERE id=%s", (rule_id,))
    sources = c.execute(
        "SELECT DISTINCT b.digest FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN document_versions v ON v.id=p.document_version_id JOIN blobs b ON b.id=v.blob_id WHERE e.id=ANY(%s::uuid[]) ORDER BY b.digest",
        (rule["evidence_ids"],),
    ).fetchall()
    return sha({"rule": semantic_key(rule), "originals": [r["digest"] for r in sources]})


def body_digest(c, manifest):
    return fetch(
        c,
        "SELECT b.digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s AND v.bid_id=%s",
        (manifest["body_version_id"], manifest["bid_id"]),
    )["digest"]


def _valid_sources(c, manifest):
    try:
        closure = source_closure(c, manifest["source_refs"])
        effective_at(closure, manifest["as_of"])
        return all(
            sha([s for s in closure if s["source_id"] == r["id"]]) == r["lineage_manifest_digest"]
            for r in manifest["source_refs"]
        )
    except DomainError:
        return False


def lifecycle(c, row):
    manual, _ = decision(c, row)
    if manual and manual["decision"] == "dismiss":
        return "dismissed"
    key = rule_key(c, row["rule_id"])
    if key:
        original = origin(c, row)
        base = fetch(
            c,
            "SELECT i.manifest,r.created_at FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s",
            (original["review_id"],),
        )
        checks = c.execute(
            "SELECT m.rule_id,m.evidence_ids,m.verdict,r.created_at,i.manifest,o.finished_at,o.id AS operation_id FROM matrix_checks m JOIN review_runs r ON r.id=m.review_id JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE m.bid_id=%s AND m.state='succeeded' AND m.verdict IN('satisfied','not_applicable') AND o.state='succeeded' AND r.created_at>%s ORDER BY r.created_at DESC",
            (row["bid_id"], base["created_at"]),
        ).fetchall()
        for check in checks:
            # 新一轮零发现、同稿随机重测或未读范围不能自动消除旧关键风险。
            rule = fetch(
                c, "SELECT provenance_status FROM rule_items WHERE id=%s", (check["rule_id"],)
            )
            ended = rule["provenance_status"] == "superseded"
            if (
                not ended and body_digest(c, check["manifest"]) == body_digest(c, base["manifest"])
            ) or rule_key(c, check["rule_id"]) != key:
                continue
            if manual and manual["created_at"] > check["finished_at"]:
                continue
            if not ended:
                from packages.domain.core_recheck_scope import evaluate

                scope = evaluate(
                    c,
                    manifest=check["manifest"],
                    rule_id=check["rule_id"],
                    evidence_ids=check["evidence_ids"],
                    operation_id=check["operation_id"],
                )
                if not scope["eligible"]:
                    continue
            if _valid_sources(c, check["manifest"]):
                return "resolved_by_recheck"
    return "deferred" if manual and manual["decision"] == "defer" else "open"


def visible_rows(c, review_id):
    rows = c.execute(
        "SELECT * FROM findings WHERE review_id=%s ORDER BY id", (review_id,)
    ).fetchall()
    grouped: dict = {}
    for row in rows:
        root = origin(c, row)
        old = grouped.get(root["id"])
        # 本轮真实重新核对的证据优先；准备时保留的旧发现不重复计数。
        if old is None or (bool(row["searched_ranges"]), str(row["id"])) > (
            bool(old["searched_ranges"]),
            str(old["id"]),
        ):
            grouped[root["id"]] = row
    return sorted(grouped.values(), key=lambda row: str(row["id"]))


def retain_critical(c, bid, review_id):
    rows = c.execute(
        "SELECT f.* FROM findings f JOIN review_runs r ON r.id=f.review_id WHERE f.bid_id=%s AND f.review_id<>%s AND f.severity='critical' ORDER BY r.created_at,(jsonb_array_length(f.searched_ranges)>0),f.id",
        (bid, review_id),
    ).fetchall()
    latest = {}
    for row in rows:
        root = origin(c, row)
        latest[root["id"]] = row
    manifest = fetch(
        c,
        "SELECT i.manifest FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s",
        (review_id,),
    )["manifest"]
    candidates = c.execute(
        "SELECT id FROM rule_items WHERE rule_set_id=%s", (manifest["rule_set_id"],)
    ).fetchall()
    new_keys = {rule_key(c, item["id"]): item["id"] for item in candidates}
    for old in latest.values():
        if lifecycle(c, old) in ("dismissed", "resolved_by_recheck"):
            continue
        rule = new_keys.get(rule_key(c, old["rule_id"]), old["rule_id"])
        c.execute(
            "INSERT INTO findings(id,bid_id,review_id,rule_id,category,severity,verdict,statement,searched_ranges,evidence_ids,carried_from_id) VALUES(%s,%s,%s,%s,%s,'critical',%s,%s,%s,%s,%s)",
            (
                uid(),
                bid,
                review_id,
                rule,
                old["category"],
                old["verdict"],
                old["statement"],
                Jsonb([]),
                old["evidence_ids"],
                old["id"],
            ),
        )


def carry_target(c, bid, review_id, rule_id):
    """本轮保留项或同稿同义发现复用身份，避免重复计数和清空人工决定。"""
    key = rule_key(c, rule_id)
    for row in visible_rows(c, review_id):
        if row["severity"] == "critical" and rule_key(c, row["rule_id"]) == key:
            return row["id"]
    manifest = fetch(
        c,
        "SELECT i.manifest FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s",
        (review_id,),
    )["manifest"]
    rows = c.execute(
        "SELECT f.*,i.manifest FROM findings f JOIN review_runs r ON r.id=f.review_id JOIN review_inputs i ON i.id=r.input_id WHERE f.bid_id=%s AND f.review_id<>%s AND f.severity='critical' ORDER BY r.created_at DESC,f.id",
        (bid, review_id),
    ).fetchall()
    for row in rows:
        if (
            body_digest(c, row["manifest"]) == body_digest(c, manifest)
            and rule_key(c, row["rule_id"]) == key
        ):
            return row["id"]
    return None
