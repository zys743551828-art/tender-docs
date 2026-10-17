"""从冻结review读完整正文；拒绝用检索片段、聊天或官方结果拼评分输入。"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, fetch, sha, uid
from packages.research.batches import save_manifest
from packages.research.core import FullSection, chapter_package
from packages.research.profile import digest

PACKAGE_MEDIA = "application/vnd.tender.chapter-package+json"


def prepare(c, *, actor, review_id, command):
    review = fetch(
        c,
        "SELECT r.bid_id,i.* FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s",
        (review_id,),
    )
    bid = review["bid_id"]
    fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    fetch(
        c,
        "SELECT id FROM document_versions WHERE bid_id=%s AND id=%s AND artifact_kind='bid_body'",
        (bid, review["body_version_id"]),
    )
    parse = c.execute(
        "SELECT * FROM parse_runs WHERE bid_id=%s AND document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (bid, review["body_version_id"]),
    ).fetchone()
    if parse is None or parse["coverage"] != "complete" or parse["gaps"]:
        raise DomainError(
            "SCORING_FULL_CHAPTER_UNAVAILABLE",
            "正文、表格或图页尚有读取缺口，无法冻结完整评分范围",
            409,
            {
                "prediction_mode": "unavailable",
                "gaps": parse["gaps"] if parse else ["parse_missing"],
            },
        )
    pages = c.execute(
        "SELECT * FROM pages WHERE parse_id=%s ORDER BY physical_page", (parse["id"],)
    ).fetchall()
    if (
        len(pages) != parse["page_count"]
        or not pages
        or any(page["quality"] != "usable" or page["gaps"] for page in pages)
    ):
        raise DomainError(
            "SCORING_FULL_CHAPTER_UNAVAILABLE", "实际物理页覆盖未完成，不用摘要代替缺失页", 409
        )
    evidence = c.execute(
        "SELECT * FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s ORDER BY physical_page,ordinal,id",
        (bid, parse["id"]),
    ).fetchall()
    if not evidence:
        raise DomainError("SCORING_FULL_CHAPTER_UNAVAILABLE", "正文没有可核验的完整证据", 409)
    # 首个可运行方法保守读取全文；不是top-k，后续章映射不得缩减而不留覆盖清单。
    blocks = tuple(
        {
            "evidence_id": str(item["id"]),
            "physical_page": item["physical_page"],
            "text": item["text_content"],
            "digest": sha(item["text_content"].encode()),
        }
        for item in evidence
    )
    full = FullSection(
        "full-body",
        str(review["body_version_id"]),
        str(parse["id"]),
        tuple(page["physical_page"] for page in pages),
        blocks,
    )
    ruleset = review["manifest"].get("rule_set_id")
    if not ruleset:
        raise DomainError("SCORING_RULES_UNAVAILABLE", "本轮没有冻结的评分规则包", 409)
    rules = c.execute(
        "SELECT * FROM rule_items WHERE bid_id=%s AND rule_set_id=%s AND kind='scoring' ORDER BY id",
        (bid, ruleset),
    ).fetchall()
    if not rules:
        raise DomainError("SCORING_RULES_UNAVAILABLE", "不能把没有识别到评分项当作无需评分", 409)
    output = []
    for rule in rules:
        origin = c.execute(
            "SELECT e.id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN material_assignments a ON a.document_version_id=p.document_version_id AND a.snapshot_id=%s WHERE e.id=ANY(%s::uuid[]) AND a.role IN ('tender','supplement')",
            (review["material_snapshot_id"], rule["evidence_ids"]),
        ).fetchall()
        if {row["id"] for row in origin} != set(rule["evidence_ids"]):
            raise DomainError(
                "SCORING_RULE_SOURCE_INVALID", "评分条款引文不属于本轮生效招标补遗", 409
            )
        bands = [
            {
                "id": str(rule["id"]) + ":band:" + str(index),
                "title": band["label"],
                "description": band["description"],
                "points": band["points"],
            }
            for index, band in enumerate(rule["bands"], start=1)
        ]
        package = chapter_package(
            rule={
                "id": str(rule["id"]),
                "statement": rule["statement"],
                "conditions": rule["conditions"],
                "exceptions": rule["exceptions"],
                "bands": bands,
                "maximum": str(rule["max_points"]),
            },
            body_version_id=str(review["body_version_id"]),
            parse_id=str(parse["id"]),
            sections=[full],
            required_ids=[full.id],
            method_digest=digest(),
        )
        blob_id = save_manifest(c, package, PACKAGE_MEDIA)
        existing = c.execute(
            "SELECT id FROM chapter_packages WHERE bid_id=%s AND review_id=%s AND rule_id=%s AND parse_id=%s AND digest=%s",
            (bid, review_id, rule["id"], parse["id"], package["digest"]),
        ).fetchone()
        identity = existing["id"] if existing else uid()
        if existing is None:
            c.execute(
                "INSERT INTO chapter_packages(id,bid_id,review_id,rule_id,parse_id,required_ranges,read_ranges,unread_ranges,manifest_blob_id,digest) VALUES(%s,%s,%s,%s,%s,%s,%s,'[]',%s,%s)",
                (
                    identity,
                    bid,
                    review_id,
                    rule["id"],
                    parse["id"],
                    Jsonb([full.id]),
                    Jsonb([full.id]),
                    blob_id,
                    package["digest"],
                ),
            )
        output.append(
            {
                "id": str(identity),
                "rule_id": str(rule["id"]),
                "manifest_blob_id": str(blob_id),
                "digest": package["digest"],
                "required_ranges": [full.id],
                "read_ranges": [full.id],
                "unread_ranges": [],
            }
        )
    audit(
        c,
        actor,
        bid,
        "research.full_chapters_prepared",
        {
            "review_id": review_id,
            "packages": [value["id"] for value in output],
            "method_digest": digest(),
        },
        command,
    )
    return {
        "review_id": str(review_id),
        "packages": output,
        "method_digest": digest(),
        "scope": "full_body",
        "formal_prediction_mode": "unavailable",
    }
