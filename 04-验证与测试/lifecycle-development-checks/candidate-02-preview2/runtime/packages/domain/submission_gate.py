"""标准实交四类闸门：来源/当前输入、完整核心、人工确认、未处理问题。"""

from packages.domain.finding_lifecycle import lifecycle, origin
from packages.domain.review_data import effective_at, source_closure
from packages.domain.reviews import POLICY, method_profile
from packages.domain.rule_sets import current_decision, rules
from packages.domain.runtime import DomainError, clean, fetch, sha
from packages.identity.repository import snapshot as identity_snapshot


def blocking_findings(c, bid):
    rows = c.execute(
        "SELECT f.* FROM findings f LEFT JOIN rule_items r ON r.id=f.rule_id JOIN review_runs run ON run.id=f.review_id WHERE f.bid_id=%s AND (f.severity='critical' OR r.kind IN('disqualification','mandatory')) ORDER BY run.created_at DESC,f.id",
        (bid,),
    ).fetchall()
    result, seen = {}, set()
    for row in rows:
        root = origin(c, row)
        if root["id"] in seen:
            continue
        seen.add(root["id"])
        state = lifecycle(c, row)
        if state not in ("dismissed", "resolved_by_recheck"):
            result[root["id"]] = dict(row) | {"origin_id": root["id"], "lifecycle": state}
    return list(result.values())


def evaluate(c, bid, *, lock_sources=False):
    """标准写入调用方持bid锁，并请求source闭包锁；GET在RR快照只读。"""
    profile = fetch(
        c,
        "SELECT b.*,p.current_body_id,p.current_material_id,p.current_rule_set_id,p.current_review_id,p.archived_at,p.phase FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    identity = identity_snapshot(c, bid)
    blockers = []

    def block(code, object_id, message):
        blockers.append({"code": code, "object_id": str(object_id or bid), "message": message})

    if profile["archived_at"]:
        block("PROJECT_ARCHIVED", bid, "项目已归档，请先明确恢复工作状态")
    if identity["binding_status"] != "confirmed":
        block("BID_IDENTITY_UNCONFIRMED", bid, "请确认本项目的真实投标主体及工程轮次")
    body = profile["current_body_id"]
    if not body:
        block("ACTUAL_BODY_REQUIRED", bid, "请上传并入库实际受检正文")
    else:
        document = fetch(
            c,
            "SELECT v.artifact_kind,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=%s",
            (bid, body),
        )
        if document["artifact_kind"] != "bid_body" or document["media_type"] != "application/pdf":
            block(
                "SUBMISSION_BODY_KIND_INVALID",
                body,
                "标准登记须选择本轮受检PDF正文，报告或批注稿不具备正文资格",
            )
        conflicting = c.execute(
            "SELECT 1 FROM document_versions v JOIN document_versions body ON body.blob_id=v.blob_id WHERE body.id=%s AND v.artifact_kind IN('review_report','annotated_review_copy') LIMIT 1",
            (body,),
        ).fetchone()
        if conflicting:
            block(
                "SUBMISSION_ARTIFACT_PURPOSE_CONFLICT",
                body,
                "该字节原件已登记为报告或批注副本，不能通过重新上传角色作为干净正文登记",
            )
    review_id = profile["current_review_id"]
    review = None
    pending = []
    if not review_id:
        block("CURRENT_REVIEW_REQUIRED", body, "请先完成当前实际稿件的核心评审")
    else:
        review = fetch(
            c,
            "SELECT r.id,r.input_id,r.operation_id,i.manifest,i.manifest_digest,i.method_digest,o.state,o.finished_at FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE r.bid_id=%s AND r.id=%s",
            (bid, review_id),
        )
        manifest = review["manifest"]
        fact = c.execute("SELECT fact_set_id FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
        current = (
            str(body) == manifest["body_version_id"]
            and str(profile["current_material_id"]) == manifest["material_snapshot_id"]
            and profile["material_revision"] == manifest["material_revision"]
            and str(profile["current_rule_set_id"]) == manifest["rule_set_id"]
            and (str(fact["fact_set_id"]) if fact and fact["fact_set_id"] else None)
            == manifest["fact_set_id"]
            and review["method_digest"] == sha(method_profile())
            and manifest["policy_digest"] == sha(POLICY)
            and manifest["content_checks"]
            == {
                key: bool(profile["settings"].get(key, False))
                for key in ("consistency", "scope_residue")
            }
        )
        changed_parse = c.execute(
            "SELECT 1 FROM parse_runs p WHERE p.bid_id=%s AND p.id=ANY(%s::uuid[]) AND p.id IS DISTINCT FROM(SELECT latest.id FROM parse_runs latest WHERE latest.document_version_id=p.document_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1) LIMIT 1",
            (bid, manifest["parse_run_ids"]),
        ).fetchone()
        if not current or changed_parse:
            block(
                "REVIEW_INPUT_STALE",
                review_id,
                "正文、资料、规则、事实或核对方法已更新，请重新评审当前稿件",
            )
        try:
            closure = source_closure(c, manifest["source_refs"], lock=lock_sources)
            effective_at(closure, c.execute("SELECT clock_timestamp() AS at").fetchone()["at"])
            if any(
                sha([row for row in closure if row["source_id"] == source["id"]])
                != source["lineage_manifest_digest"]
                for source in manifest["source_refs"]
            ):
                raise DomainError("SOURCE_CHANGED", "来源效力发生变化", 409)
        except DomainError:
            block(
                "CURRENT_SOURCE_UNRESOLVED", review_id, "生效来源已变化、撤回、过期或适用关系待核验"
            )
        if review["state"] != "succeeded":
            block(
                "CORE_REVIEW_NOT_FINISHED",
                review_id,
                "核心评审尚未成功完成，请查看任务及未覆盖范围",
            )
        result = c.execute(
            "SELECT core_coverage,coverage_layers FROM review_results WHERE review_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (review_id,),
        ).fetchone()
        if (
            not result
            or result["core_coverage"] != "complete"
            or any(
                result["coverage_layers"].get(key, {}).get("state") != "complete"
                for key in ("source_scope", "core_extraction", "judgment")
            )
        ):
            block(
                "CORE_COVERAGE_INCOMPLETE",
                review_id,
                "必要来源、核心提取或核对范围仍有已知缺口；评分采用独立覆盖",
            )
        items = rules(c, manifest["rule_set_id"])
        core = [
            item
            for item in items
            if item["kind"] in ("mandatory", "disqualification")
            and item["provenance_status"] != "superseded"
        ]
        matrix = c.execute(
            "SELECT rule_id,state FROM matrix_checks WHERE bid_id=%s AND review_id=%s",
            (bid, review_id),
        ).fetchall()
        complete = {row["rule_id"] for row in matrix if row["state"] == "succeeded"}
        if any(
            item["id"] not in complete or item["provenance_status"] != "effective" for item in core
        ):
            block(
                "CORE_RULE_CHECKS_INCOMPLETE",
                review_id,
                "已识别生效核心要求仍有未完成核对或待核验关系",
            )
        pending = [
            item["id"]
            for item in core
            if not (current_decision(c, bid, "rule", item["id"]) or {}).get("decision")
            == "confirmed"
        ]
        if pending and not profile["settings"].get("accept_rules_as_written", False):
            block(
                "CORE_RULE_CONFIRMATION_REQUIRED",
                manifest["rule_set_id"],
                f"尚有{len(pending)}项否决或强制要求需人工确认",
            )
    findings = blocking_findings(c, bid)
    for finding in findings:
        block("OPEN_BLOCKING_FINDING", finding["origin_id"], finding["statement"])
    return clean(
        {
            "allowed": not blockers,
            "bid_revision": profile["revision"],
            "body_version_id": body,
            "blockers": blockers,
            "review_id": review_id,
            "material_revision": profile["material_revision"],
            "current_identity_digest": identity["digest"],
            "current_review_manifest_digest": review["manifest_digest"] if review else None,
            "pending_core_rule_ids": pending,
            "blocking_finding_ids": [row["origin_id"] for row in findings],
            "warnings": ["登记记录不等于交易平台提交成功；评分不可预测本身不阻断核心提交检查"],
        }
    )
