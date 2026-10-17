"""API冻结可信来源胶囊；研究角色仅通过受限执行身份函数重核。"""

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, clean, fetch
from packages.rag.indexing import original_source_id, parse_source_id


def configured(c):
    row = c.execute(
        "SELECT to_regclass('design.research_chapter_authorities') IS NOT NULL AND to_regclass('design.research_preview_chapters') IS NOT NULL AND to_regprocedure('design.qualify_research_chapter(uuid,text,design.sha256,bigint,bigint,uuid,uuid)') IS NOT NULL AND to_regprocedure('design.capture_research_source_heads(uuid[])') IS NOT NULL AS configured"
    ).fetchone()
    return bool(row and row["configured"])


def require(c):
    if not configured(c):
        raise DomainError(
            "RESEARCH_AUTHORITY_NOT_CONFIGURED",
            "研究来源资格基础尚未初始化；资料核验仍可使用，评分研究暂不可执行",
            503,
        )


def capture(c, *, review, parse_id, rule_evidence):
    """只有API调用；无HTTP、对象I/O或模型计算。"""
    require(c)
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    evidence = c.execute(
        "SELECT e.*,p.document_version_id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id WHERE e.bid_id=%s AND (e.parse_id=%s OR e.id=ANY(%s::uuid[])) ORDER BY e.parse_id,e.id",
        (review["bid_id"], parse_id, rule_evidence),
    ).fetchall()
    found = {row["id"] for row in evidence}
    if not set(rule_evidence) <= found:
        raise DomainError("RESEARCH_SOURCE_INVALID", "冻结评分条款证据不属于本项目", 409)
    grouped: dict[str, dict] = {}
    allowed = {review["body_version_id"]} | {
        row["document_version_id"]
        for row in c.execute(
            "SELECT document_version_id FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND role IN ('tender','supplement')",
            (review["bid_id"], review["material_snapshot_id"]),
        ).fetchall()
    }
    for row in evidence:
        if row["document_version_id"] not in allowed:
            raise DomainError(
                "RESEARCH_SOURCE_INVALID", "评分证据不属于冻结正文及生效招标补遗", 409
            )
        source_id, original_id = (
            parse_source_id(row["parse_id"]),
            original_source_id(row["document_version_id"]),
        )
        node = c.execute(
            "SELECT n.*,h.revision FROM retrieval.source_nodes n JOIN retrieval.source_heads h ON h.source_id=n.id JOIN document_versions d ON d.id=n.document_version_id JOIN blobs b ON b.id=d.blob_id WHERE n.id=%s AND n.kind='document' AND n.document_version_id=%s AND n.digest=b.digest AND EXISTS(SELECT 1 FROM retrieval.lineage_edges edge JOIN retrieval.source_nodes parent ON parent.id=edge.parent_id WHERE edge.child_id=n.id AND parent.id=%s AND parent.kind='document' AND parent.document_version_id=d.id AND parent.digest=b.digest)",
            (source_id, row["document_version_id"], original_id),
        ).fetchone()
        if node is None:
            raise DomainError(
                "RESEARCH_SOURCE_UNREGISTERED",
                "原件或精确解析来源尚未登记/核验，不能冻结评分包",
                409,
            )
        group = grouped.setdefault(
            str(row["parse_id"]),
            {
                "parse_id": str(row["parse_id"]),
                "document_version_id": str(row["document_version_id"]),
                "source_id": str(source_id),
                "original_source_id": str(original_id),
                "source_revision": node["revision"],
                "evidence_ids": [],
            },
        )
        group["evidence_ids"].append(str(row["id"]))
    bindings = [grouped[key] for key in sorted(grouped)]
    if not bindings:
        raise DomainError("RESEARCH_SOURCE_INVALID", "没有完整评分证据来源", 409)
    heads = fetch(
        c,
        "SELECT design.capture_research_source_heads(%s::uuid[]) AS heads",
        ([UUID(binding["source_id"]) for binding in bindings],),
    )["heads"]

    def instant(head, key):
        return (
            None if head[key] is None else datetime.fromisoformat(head[key].replace("Z", "+00:00"))
        )

    for head in heads:
        if (
            head["document_version_id"] is None
            or UUID(head["document_version_id"]) not in allowed
            or head["kind"] != "document"
            or head["policy_class"] != "prebid_content"
            or head["status"] != "published"
            or instant(head, "known_at") > review["as_of"]
            or instant(head, "valid_from") is None
            or instant(head, "valid_from") > review["as_of"]
            or (
                instant(head, "valid_until") is not None
                and instant(head, "valid_until") <= review["as_of"]
            )
        ):
            raise DomainError(
                "RESEARCH_SOURCE_CHANGED",
                "评分原件或祖先来源已撤回、时效/用途不符，未授权新评分请求",
                409,
            )
    digest = fetch(
        c,
        "SELECT encode(sha256(convert_to(jsonb_build_object('bindings',%s::jsonb,'ancestor_heads',%s::jsonb)::text,'UTF8')),'hex') AS digest",
        (Jsonb(bindings), Jsonb(heads)),
    )["digest"]
    return {
        "bindings": bindings,
        "ancestor_heads": heads,
        "authority_digest": digest,
        "as_of": review["as_of"],
    }


def register(c, *, chapter_id, bid, actor, captured):
    c.execute(
        "INSERT INTO research_chapter_authorities(chapter_package_id,bid_id,as_of,bindings,ancestor_heads,authority_digest,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            chapter_id,
            bid,
            captured["as_of"],
            Jsonb(captured["bindings"]),
            Jsonb(captured["ancestor_heads"]),
            captured["authority_digest"],
            actor,
        ),
    )


def assert_current_api(c, chapter_id):
    require(c)
    row = fetch(
        c,
        "SELECT cp.*,a.authority_digest,i.material_snapshot_id,i.body_version_id,i.as_of,ri.evidence_ids FROM chapter_packages cp JOIN research_chapter_authorities a ON a.chapter_package_id=cp.id JOIN review_runs r ON r.id=cp.review_id JOIN review_inputs i ON i.id=r.input_id JOIN rule_items ri ON ri.id=cp.rule_id WHERE cp.id=%s",
        (chapter_id,),
    )
    captured = capture(c, review=row, parse_id=row["parse_id"], rule_evidence=row["evidence_ids"])
    if captured["authority_digest"] != row["authority_digest"]:
        raise DomainError(
            "RESEARCH_SOURCE_CHANGED", "章节包的原件或祖先head已改变，请重新冻结", 409
        )
    return captured


def qualify(c, message, lease, chapter_id=None):
    require(c)
    verdict = fetch(
        c,
        "SELECT design.qualify_research_chapter(%s,%s,%s,%s,%s,%s,%s) AS result",
        (
            message["operation_id"],
            message["step_key"],
            message["input_digest"],
            message["generation"],
            lease["fence"],
            lease["owner"],
            chapter_id,
        ),
    )["result"]
    if verdict.get("qualified") is not True:
        raise DomainError(
            "RESEARCH_SOURCE_CHANGED",
            "执行、完整章节或来源资格已改变，未授权新请求/成功产物",
            409,
            {"qualification_reason": verdict.get("reason", "unknown")},
        )
    return clean(verdict)
