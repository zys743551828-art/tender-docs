"""补遗效力核验卡：Cookie/CSRF、同键重放、当前影响强CAS。"""

from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from apps.api.human_routes import check_tag, command, saved, tag
from apps.api.review_views import rule_set_view, rule_view
from apps.api.source_effectivity_contracts import validate_request
from packages.domain.rule_sets import rules
from packages.domain.runtime import DomainError, clean, connection, fetch, sha
from packages.domain.source_effectivity import EVENT, decide, verify_originals

router = APIRouter(prefix="/api/v1", tags=["source effectivity"])


def source_effectivity_view(c, bid):
    profile = fetch(c, "SELECT * FROM bid_profiles WHERE bid_id=%s", (bid,))
    items = rules(c, profile["current_rule_set_id"]) if profile["current_rule_set_id"] else []
    evidence_ids = sorted({str(identity) for item in items for identity in item["evidence_ids"]})
    versions = c.execute(
        "SELECT DISTINCT v.id,v.original_name,p.id AS parse_id,p.coverage FROM document_versions v LEFT JOIN LATERAL(SELECT id,coverage FROM parse_runs WHERE document_version_id=v.id ORDER BY created_at DESC,id DESC LIMIT 1) p ON true WHERE v.bid_id=%s AND (EXISTS(SELECT 1 FROM material_assignments a WHERE a.snapshot_id=%s AND a.bid_id=v.bid_id AND a.document_version_id=v.id AND a.role IN('tender','supplement','drawing','boq')) OR EXISTS(SELECT 1 FROM evidence_blocks e JOIN parse_runs r ON r.id=e.parse_id WHERE e.bid_id=v.bid_id AND r.document_version_id=v.id AND e.id=ANY(%s::uuid[]))) ORDER BY v.id",
        (bid, profile["current_material_id"], evidence_ids),
    ).fetchall()
    sources = []
    for version in versions:
        roles = c.execute(
            "SELECT DISTINCT role FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND document_version_id=%s ORDER BY role",
            (bid, profile["current_material_id"], version["id"]),
        ).fetchall()
        evidences = c.execute(
            "SELECT id FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s ORDER BY physical_page,ordinal,id",
            (bid, version["parse_id"]),
        ).fetchall()
        sources.append(
            {
                "document_version_id": version["id"],
                "original_name": version["original_name"],
                "roles": [role["role"] for role in roles],
                "current": bool(roles),
                "parse_run_id": version["parse_id"],
                "coverage": version["coverage"] or "pending",
                "evidence_ids": [evidence["id"] for evidence in evidences],
            }
        )
    authorities = c.execute(
        "SELECT n.id,n.digest,n.known_at,n.policy_class,h.revision,h.status,h.valid_from,h.valid_until FROM retrieval.source_nodes n LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE n.id IN(SELECT ancestor_id FROM retrieval.source_ancestors a JOIN retrieval.source_nodes s ON s.id=a.source_id WHERE s.document_version_id=ANY(%s::uuid[])) ORDER BY n.id",
        ([version["id"] for version in versions],),
    ).fetchall()
    last = c.execute(
        "SELECT id FROM audit_entries WHERE bid_id=%s AND event_type IN(%s,'sources.effectivity_note_recorded') ORDER BY created_at DESC,id DESC LIMIT 1",
        (bid, EVENT),
    ).fetchone()
    requires_review = bool(last)
    if last and profile["current_review_id"]:
        from apps.api.review_views import review_view

        review = review_view(c, profile["current_review_id"])
        state = fetch(c, "SELECT state FROM operations WHERE id=%s", (review["operation_id"],))[
            "state"
        ]
        requires_review = not (review["current"] and state == "succeeded")
    return clean(
        {
            "bid_id": bid,
            "material_snapshot_id": profile["current_material_id"],
            "rule_set_id": profile["current_rule_set_id"],
            "rules": [rule_view(c, item["id"]) for item in items],
            "sources": sources,
            "source_authority_digest": sha(authorities),
            "last_decision_id": last["id"] if last else None,
            "requires_review": requires_review,
        }
    )


@router.get("/bids/{bid_id}/source-effectivity")
def get_effectivity(bid_id: UUID, r: Request):
    auth(r)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        value = source_effectivity_view(c, bid_id)
        return JSONResponse(value, headers={"ETag": tag("source-effectivity", bid_id, value)})


@router.post("/bids/{bid_id}/source-effectivity-decisions")
def decide_effectivity(bid_id: UUID, r: Request, body: dict = Body(...)):
    actor = auth(r, True)
    validate_request(body)
    # 幂等返回先于对象存储读取，旧If-Match不会导致成功命令重放失败。
    with connection() as c:
        cmd = command(c, r, actor, "decide_source_effectivity", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        if not r.headers.get("if-match"):
            raise DomainError("PRECONDITION_REQUIRED", "请先核对当前生效要求及影响清单", 428)
        versions = [body["old_document_version_id"]]
        if body["new_document_version_id"]:
            versions.append(body["new_document_version_id"])
        objects = c.execute(
            "SELECT b.object_key,b.digest,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=ANY(%s::uuid[]) ORDER BY v.id",
            (bid_id, versions),
        ).fetchall()
        # 只完成重放/读取准备，持久命令与领域写入一起提交；I/O不保持事务锁。
        c.rollback()
    if body["action"] != "defer":
        verify_originals(objects)
    with connection() as c:
        cmd = command(c, r, actor, "decide_source_effectivity", bid_id, body)
        if cmd.replay:
            return saved(cmd.replay, True)
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid_id,))
        value = source_effectivity_view(c, bid_id)
        check_tag(r, tag("source-effectivity", bid_id, value))
        new_set = decide(c, bid_id, actor["member_id"], cmd.id, body)
        result = rule_set_view(c, new_set)
        return saved(cmd.finish(result, headers={"ETag": tag("rule-set", new_set, result)}))
