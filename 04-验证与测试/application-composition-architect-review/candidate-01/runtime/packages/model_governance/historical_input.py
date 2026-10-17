"""API-only historical reconstruction. No labels, fake review, or backdated source times.

Capture and publish run in separate RC transactions. Remote object writes/reads occur
outside both. SQL is the final authority; this module mirrors guards before any PUT.
"""

import json
from typing import NoReturn
from uuid import UUID

from packages.domain import storage
from packages.domain.runtime import DomainError, audit, clean, etag, fetch, now, sha, uid
from packages.model_governance import historical_repository as proofs
from packages.model_governance.repository import match
from packages.rag.indexing import original_source_id, parse_source_id
from packages.research.artifacts import commit_stage, stage_json
from packages.research.core import FullSection, amount, chapter_package
from packages.research.profile import digest as method_digest

MEDIA = "application/vnd.tender.historical-input+json"
CHAPTER_MEDIA = "application/vnd.tender.chapter-package+json"


def configured(c):
    return bool(
        c.execute(
            "SELECT to_regprocedure('historical_input.seal_input(uuid,uuid)') IS NOT NULL AND to_regprocedure('historical_input.current_input(uuid)') IS NOT NULL AS ready"
        ).fetchone()["ready"]
    )


def require(c):
    if not configured(c):
        raise DomainError(
            "HISTORICAL_INPUT_NOT_CONFIGURED",
            "历史回测输入权威尚未配置，不能以普通研究输入代替",
            503,
        )


def unavailable(message) -> NoReturn:
    raise DomainError("HISTORICAL_INPUT_UNAVAILABLE", message, 409)


def context(c, submission_id):
    require(c)
    base, _ = proofs.context(c, submission_id)
    rules = c.execute(
        "SELECT r.id,r.material_id,r.revision,r.digest,r.coverage,(SELECT coalesce(jsonb_agg(jsonb_build_object('document_version_id',a.document_version_id,'role',a.role,'original_name',d.original_name) ORDER BY a.role,a.ordinal),'[]'::jsonb) FROM material_assignments a JOIN document_versions d ON d.id=a.document_version_id WHERE a.snapshot_id=r.material_id AND a.bid_id=r.bid_id AND a.role IN ('tender','supplement')) AS material_versions FROM rule_sets r WHERE r.bid_id=%s ORDER BY r.id",
        (base["bid_id"],),
    ).fetchall()
    versions = c.execute(
        "SELECT d.id,d.original_name,d.artifact_kind,b.digest,p.id AS latest_parse_id,p.coverage FROM document_versions d JOIN blobs b ON b.id=d.blob_id LEFT JOIN LATERAL (SELECT id,coverage FROM parse_runs WHERE document_version_id=d.id ORDER BY created_at DESC,id DESC LIMIT 1) p ON true WHERE d.bid_id=%s AND d.artifact_kind IN ('source_original','bid_body') ORDER BY d.id",
        (base["bid_id"],),
    ).fetchall()
    value = base | {
        "rule_sets": clean(rules),
        "document_versions": clean(versions),
        "historical_input_available": True,
        "paid_execution_enabled": False,
    }
    return value, etag("historical-input-context", submission_id, sha(value))


def _confirmed(c, kind, identity):
    value, _ = proofs.view(c, kind, identity)
    if (
        not value["submission_current"]
        or not value["is_current_head"]
        or value["manual_decision"] != "confirmed"
        or not value["proofs_current"]
    ):
        unavailable("主体或来源证明尚未确认、被更正或当前原页失效")
    if kind == "subject" and not value["subject_relationship_current"]:
        unavailable("历史主体和工程轮次关系不匹配")
    if kind == "claim" and not value["related_subject_current"]:
        unavailable("来源的本方可见事实未绑定当前已确认历史主体")
    return value


def _facts(claim, as_of, purpose):
    values = {row["fact_kind"]: row for row in claim["facts"]}
    required = ["content_existed", "known_to_bidder"] + (["effective"] if purpose != "body" else [])
    proof_kinds = {p["fact_kind"] for p in claim["proof_refs"]}
    from datetime import datetime

    for kind in required:
        fact = values.get(kind)
        if not fact or fact["claimed_at"] is None or kind not in proof_kinds:
            unavailable(
                "精确原件缺少当时存在、本方知晓或条款生效的独立证明，公开日期不能替代本方知晓"
            )
        if datetime.fromisoformat(fact["claimed_at"]) > as_of:
            unavailable("证明显示来源晚于原实交时点，不能用于当时输入")
        if (
            kind == "effective"
            and fact["valid_until"] is not None
            and datetime.fromisoformat(fact["valid_until"]) <= as_of
        ):
            unavailable("历史条款生效区间未覆盖原实交时点")


def _parse(c, bid, document):
    parse = c.execute(
        "SELECT p.*,b.digest AS document_digest FROM parse_runs p JOIN document_versions d ON d.id=p.document_version_id JOIN blobs b ON b.id=d.blob_id WHERE p.document_version_id=%s AND p.bid_id=%s AND d.bid_id=%s ORDER BY p.created_at DESC,p.id DESC LIMIT 1",
        (document, bid, bid),
    ).fetchone()
    if not parse or parse["coverage"] != "complete" or parse["gaps"] or parse["page_count"] <= 0:
        unavailable("精确原件当前解析未完整，不以摘要或旧解析代替")
    pages = c.execute(
        "SELECT * FROM pages WHERE parse_id=%s ORDER BY physical_page", (parse["id"],)
    ).fetchall()
    if [p["physical_page"] for p in pages] != list(range(1, parse["page_count"] + 1)) or any(
        p["quality"] != "usable" or p["gaps"] for p in pages
    ):
        unavailable("物理页读取覆盖未完成")
    source, root = parse_source_id(parse["id"]), original_source_id(UUID(str(document)))
    if not c.execute(
        "SELECT n.id FROM retrieval.source_nodes n JOIN retrieval.source_nodes root ON root.id=%s WHERE n.id=%s AND n.kind='document' AND root.kind='document' AND n.document_version_id=%s AND root.document_version_id=%s AND n.digest=%s AND root.digest=%s AND EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=n.id AND edge.parent_id=root.id)",
        (root, source, document, document, parse["document_digest"], parse["document_digest"]),
    ).fetchone():
        unavailable("精确原件和当前解析缺少canonical来源映射")
    heads = c.execute(
        "SELECT n.id AS ancestor_id,n.document_version_id,n.kind,n.digest,n.policy_class,n.known_at,h.revision,h.status,h.valid_from,h.valid_until,d.bid_id AS document_bid FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN document_versions d ON d.id=n.document_version_id WHERE a.source_id=%s ORDER BY n.id",
        (source,),
    ).fetchall()
    current_time = now()
    if not heads or any(
        h["document_bid"] != bid
        or h["kind"] != "document"
        or h["policy_class"] != "prebid_content"
        or h["status"] != "published"
        or h["known_at"] > current_time
        or h["valid_from"] > current_time
        or (h["valid_until"] is not None and h["valid_until"] <= current_time)
        for h in heads
    ):
        unavailable("完整来源祖先存在撤回、未发布、未来时间或项目/用途缺口")
    return {
        "document_version_id": document,
        "parse_id": parse["id"],
        "source_id": source,
        "root_source_id": root,
        "document_digest": parse["document_digest"],
        "heads": heads,
        "pages": [p["physical_page"] for p in pages],
    }


def capture(c, value, condition):
    require(c)
    _, tag = context(c, value["submission_id"])
    match(condition, tag)
    submission = proofs.lock_submission(c, value["submission_id"])
    subject = _confirmed(c, "subject", value["historical_subject_authority_id"])
    if subject["submission_id"] != str(submission["id"]):
        unavailable("历史主体属于另一实交")
    ruleset = fetch(
        c,
        "SELECT * FROM rule_sets WHERE id=%s AND bid_id=%s",
        (value["rule_set_id"], submission["bid_id"]),
    )
    if ruleset["coverage"] != "complete":
        unavailable("评分规则包识别未完整或存在冲突")
    assignments = c.execute(
        "SELECT a.document_version_id,a.role,d.artifact_kind FROM material_assignments a JOIN document_versions d ON d.id=a.document_version_id WHERE a.snapshot_id=%s AND a.bid_id=%s ORDER BY a.document_version_id",
        (ruleset["material_id"], submission["bid_id"]),
    ).fetchall()
    if not any(a["role"] == "tender" for a in assignments) or any(
        a["role"] not in {"tender", "supplement", "body"}
        or (a["role"] in {"tender", "supplement"} and a["artifact_kind"] != "source_original")
        or (a["role"] == "body" and a["artifact_kind"] != "bid_body")
        for a in assignments
    ):
        unavailable("首个历史完整输入方法只支持有据招标/补遗，其他必要资料不能静默省略")
    assignments = [a for a in assignments if a["role"] in {"tender", "supplement"}]
    if len(assignments) != len({a["document_version_id"] for a in assignments}) or {
        str(a["document_version_id"]) for a in assignments
    } != set(value["material_version_ids"]):
        unavailable("指定历史材料必须精确覆盖所选规则包的完整材料快照")
    purposes = {str(a["document_version_id"]): a["role"] for a in assignments}
    purposes[str(submission["body_version_id"])] = "body"
    claims = [_confirmed(c, "claim", identity) for identity in value["source_claim_ids"]]
    if len(claims) != len(purposes) or {x["document_version_id"] for x in claims} != set(purposes):
        unavailable("当时可见证明必须逐一且仅覆盖原正文和所选完整材料")
    for claim in claims:
        if claim["submission_id"] != str(submission["id"]) or claim["subject_id"] != subject["id"]:
            unavailable("来源声明未绑定同一实交和历史主体")
        _facts(claim, submission["submitted_at"], purposes[claim["document_version_id"]])
    parses = [_parse(c, submission["bid_id"], UUID(identity)) for identity in sorted(purposes)]
    for parse in parses:
        if any(str(h["document_version_id"]) not in purposes for h in parse["heads"]):
            unavailable("来源祖先没有对应精确版本的历史可见证明")
    body = next(p for p in parses if p["document_version_id"] == submission["body_version_id"])
    rules = c.execute(
        "SELECT * FROM rule_items WHERE bid_id=%s AND rule_set_id=%s AND kind='scoring' ORDER BY id",
        (submission["bid_id"], ruleset["id"]),
    ).fetchall()
    if not 1 <= len(rules) <= 1000:
        unavailable("没有完整必要评分项，不能当作无需评分")
    evidence_ids = {eid for r in rules for eid in r["evidence_ids"]}
    evidence = c.execute(
        "SELECT * FROM evidence_blocks WHERE bid_id=%s AND (parse_id=%s OR id=ANY(%s::uuid[])) ORDER BY id",
        (submission["bid_id"], body["parse_id"], sorted(evidence_ids)),
    ).fetchall()
    source_parses = {
        p["parse_id"] for p in parses if p["document_version_id"] != submission["body_version_id"]
    }
    if (
        not evidence
        or not evidence_ids <= {e["id"] for e in evidence}
        or any(e["parse_id"] not in source_parses for e in evidence if e["id"] in evidence_ids)
    ):
        unavailable("评分条款证据未绑定当前已选历史招标/补遗原件解析")
    if any(
        e["kind"] not in {"text", "table"}
        or not e["text_content"].strip()
        or sha(e["text_content"].encode()) != e["digest"]
        for e in evidence
    ):
        unavailable("章节存在未读取图页、空证据或字节不匹配")
    for rule in rules:
        if (
            rule["provenance_status"] != "effective"
            or rule["max_points"] is None
            or not isinstance(rule["bands"], list)
            or not 1 <= len(rule["bands"]) <= 100
        ):
            unavailable("必要评分项仍缺有效原文、满分或明确档位")
        for band in rule["bands"]:
            if (
                not isinstance(band, dict)
                or not isinstance(band.get("label"), str)
                or not band["label"].strip()
                or not isinstance(band.get("description"), str)
                or not band["description"].strip()
                or "points" not in band
                or amount(band["points"]) > amount(rule["max_points"])
            ):
                unavailable("档位点数与有效评分条款不完整或矛盾")
    return {
        "bid_id": submission["bid_id"],
        "submission_id": submission["id"],
        "body_version_id": submission["body_version_id"],
        "body_digest": submission["body_digest"],
        "historical_as_of": submission["submitted_at"],
        "subject_id": UUID(subject["id"]),
        "subject_decision_id": UUID(subject["last_own_decision"]["id"]),
        "identity_snapshot": subject["identity_snapshot"],
        "rule_set_id": ruleset["id"],
        "material_snapshot_id": ruleset["material_id"],
        "method_digest": method_digest(),
        "origin": "synthetic"
        if subject["declared_origin"] == "synthetic"
        or any(x["declared_origin"] == "synthetic" for x in claims)
        else "uncertain",
        "claims": [
            {
                "claim_id": UUID(x["id"]),
                "decision_id": UUID(x["last_own_decision"]["id"]),
                "purpose": purposes[x["document_version_id"]],
            }
            for x in sorted(claims, key=lambda row: row["id"])
        ],
        "parses": parses,
        "evidence": evidence,
        "rules": rules,
        "body_parse_id": body["parse_id"],
        "pages": body["pages"],
    }


def build(captured):
    body = sorted(
        (e for e in captured["evidence"] if e["parse_id"] == captured["body_parse_id"]),
        key=lambda e: (e["physical_page"], e["ordinal"], e["id"]),
    )
    full = FullSection(
        "full-body",
        str(captured["body_version_id"]),
        str(captured["body_parse_id"]),
        tuple(captured["pages"]),
        tuple(
            {
                "evidence_id": str(e["id"]),
                "physical_page": e["physical_page"],
                "text": e["text_content"],
                "digest": e["digest"],
            }
            for e in body
        ),
    )
    packages = []
    for rule in captured["rules"]:
        package = chapter_package(
            rule={
                "id": str(rule["id"]),
                "statement": rule["statement"],
                "conditions": rule["conditions"],
                "exceptions": rule["exceptions"],
                "maximum": str(rule["max_points"]),
                "bands": [
                    {
                        "id": f"{rule['id']}:band:{index}",
                        "title": band["label"],
                        "description": band["description"],
                        "points": band["points"],
                    }
                    for index, band in enumerate(rule["bands"], 1)
                ],
            },
            body_version_id=str(captured["body_version_id"]),
            parse_id=str(captured["body_parse_id"]),
            sections=[full],
            required_ids=["full-body"],
            method_digest=captured["method_digest"],
        )
        if len(json.dumps(package, ensure_ascii=False, sort_keys=True)) > 250000:
            unavailable("完整章节超出首个方法的实际请求容量，不分块伪装完整输入")
        packages.append({"id": uid(), "rule_id": rule["id"], "package": package})
    return {
        "id": uid(),
        "captured": captured,
        "capture_digest": sha(clean(captured)),
        "packages": packages,
    }


def stage(prepared):
    audit_manifest = {
        "schema": "historical-input/1",
        "id": str(prepared["id"]),
        "capture_digest": prepared["capture_digest"],
        "submission_id": str(prepared["captured"]["submission_id"]),
        "subject_id": str(prepared["captured"]["subject_id"]),
        "method_digest": prepared["captured"]["method_digest"],
        "prediction_timing": "retrospective",
        "research_only": True,
        "chapters": [
            {"id": str(x["id"]), "rule_id": str(x["rule_id"]), "digest": x["package"]["digest"]}
            for x in prepared["packages"]
        ],
    }
    return prepared | {
        "staged": stage_json(audit_manifest, MEDIA),
        "packages": [
            x | {"staged": stage_json(x["package"], CHAPTER_MEDIA)} for x in prepared["packages"]
        ],
    }


def publish(c, *, prepared, value, condition, actor, command):
    current = capture(c, value, condition)
    if sha(clean(current)) != prepared["capture_digest"]:
        unavailable("对象准备期间历史主体、来源head、完整证据或解析已变化，未发布新输入")
    identity, blob = prepared["id"], commit_stage(c, prepared["staged"])
    fields = (
        "bid_id",
        "submission_id",
        "body_version_id",
        "body_digest",
        "subject_id",
        "subject_decision_id",
        "historical_as_of",
        "rule_set_id",
        "material_snapshot_id",
        "method_digest",
    )
    c.execute(
        "INSERT INTO historical_backtest.inputs(id,bid_id,submission_id,body_version_id,body_digest,subject_id,subject_decision_id,historical_as_of,rule_set_id,material_snapshot_id,method_digest,manifest_blob_id,manifest_digest,origin,actor_id) VALUES("
        + ",".join(["%s"] * 15)
        + ")",
        (
            identity,
            *(current[k] for k in fields),
            blob,
            prepared["staged"]["digest"],
            current["origin"],
            actor,
        ),
    )
    for claim in current["claims"]:
        c.execute(
            "INSERT INTO historical_backtest.input_claims VALUES(%s,%s,%s,%s)",
            (identity, claim["claim_id"], claim["decision_id"], claim["purpose"]),
        )
    for parse in current["parses"]:
        c.execute(
            "INSERT INTO historical_backtest.input_parses VALUES(%s,%s,%s,%s,%s)",
            (
                identity,
                *(
                    parse[k]
                    for k in ("document_version_id", "parse_id", "source_id", "root_source_id")
                ),
            ),
        )
        for head in parse["heads"]:
            c.execute(
                "INSERT INTO historical_backtest.input_source_heads VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity,
                    parse["source_id"],
                    *(
                        head[k]
                        for k in (
                            "ancestor_id",
                            "revision",
                            "digest",
                            "known_at",
                            "valid_from",
                            "valid_until",
                        )
                    ),
                ),
            )
    for evidence in current["evidence"]:
        c.execute(
            "INSERT INTO historical_backtest.input_evidence VALUES(%s,%s,%s,%s,%s)",
            (identity, *(evidence[k] for k in ("id", "parse_id", "physical_page", "digest"))),
        )
    for item in prepared["packages"]:
        chapter_blob = commit_stage(c, item["staged"])
        c.execute(
            "INSERT INTO historical_backtest.chapters VALUES(%s,%s,%s,%s,%s,%s)",
            (
                item["id"],
                identity,
                item["rule_id"],
                current["body_parse_id"],
                chapter_blob,
                item["package"]["digest"],
            ),
        )
        raw = json.dumps(
            clean(item["package"]), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        c.execute(
            "INSERT INTO historical_input.chapter_payloads VALUES(%s,%s,%s)",
            (item["id"], raw, item["staged"]["digest"]),
        )
    proofs._execute(c, "SELECT historical_input.seal_input(%s,%s)", (identity, actor))
    audit(
        c,
        actor,
        current["bid_id"],
        "historical.input_sealed",
        {
            "input_id": identity,
            "prediction_timing": "retrospective",
            "formal_eligibility": "unavailable",
            "reason": value["reason"],
        },
        command,
    )
    return view(c, identity)


def metadata(c, identity):
    require(c)
    row = fetch(
        c,
        "SELECT i.*,s.authority_digest,s.rule_count FROM historical_backtest.inputs i JOIN historical_input.seals s ON s.input_id=i.id WHERE i.id=%s",
        (identity,),
    )
    # Reuse HIST02 leading RC guard and scope before considering replay or currentness.
    proofs.lock_submission(c, row["submission_id"], require_current=False)
    current = c.execute(
        "SELECT historical_input.current_input(%s) AS value", (identity,)
    ).fetchone()["value"]
    blobs = c.execute(
        "SELECT b.* FROM blobs b WHERE b.id=%s OR b.id IN (SELECT manifest_blob_id FROM historical_backtest.chapters WHERE input_id=%s) ORDER BY b.id",
        (row["manifest_blob_id"], identity),
    ).fetchall()
    return {"row": row, "current": current, "blobs": blobs}


def verify_bytes(snapshot):
    # Must be called with no SQL connection/graph lock held.
    for blob in snapshot["blobs"]:
        raw = storage.get(blob["object_key"])
        if len(raw) != blob["byte_size"] or sha(raw) != blob["digest"]:
            raise DomainError(
                "HISTORICAL_INPUT_ARTIFACT_INVALID", "冻结历史输入对象字节不匹配", 409
            )


def view(c, identity):
    state = metadata(c, identity)
    row = state["row"]
    chapters = c.execute(
        "SELECT id,rule_id,body_parse_id,digest,manifest_blob_id FROM historical_backtest.chapters WHERE input_id=%s ORDER BY rule_id",
        (identity,),
    ).fetchall()
    value = clean(row) | {
        "current": state["current"],
        "chapters": clean(chapters),
        "formal_eligibility": "unavailable",
        "execution_available": False,
        "paid_execution_enabled": False,
        "notes": ["当前创建的历史重建研究，不是当年预测；尚无正式模型资格"],
    }
    return value, etag("historical-input", identity, sha(value))
