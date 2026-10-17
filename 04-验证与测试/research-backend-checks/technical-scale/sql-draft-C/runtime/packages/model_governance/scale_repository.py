"""Independent denominator authority API orchestration; no score/job qualification.

Objects are verified between transactions, then source/identity/proof/context is
recaptured under the same short scope before one atomic version/head/decision.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.runtime import (
    DomainError,
    audit,
    clean,
    etag,
    fetch,
    require_read_committed,
    sha,
    uid,
)
from packages.identity.repository import snapshot as identity_snapshot
from packages.lifecycle.service import assert_work_writable
from packages.model_governance import historical_input
from packages.model_governance import historical_repository as history
from packages.model_governance.repository import match
from packages.model_governance.scale_evidence import (
    SUPPORTED_SCALE_DESCRIPTIONS,
    validate_maximum_declaration,
)
from packages.rag.indexing import original_source_id, parse_source_id


def configured(c):
    return bool(
        c.execute(
            "SELECT to_regprocedure('technical_scale.apply_version(uuid,bigint,text,uuid,uuid)') IS NOT NULL AND to_regprocedure('technical_scale.current_version(uuid)') IS NOT NULL AS value"
        ).fetchone()["value"]
    )


def require(c):
    if not configured(c):
        raise DomainError(
            "TECHNICAL_SCALE_AUTHORITY_NOT_CONFIGURED",
            "技术满分权威尚未初始化，不能从标签或条款和补数",
            503,
        )


def rpc(c, query, values):
    try:
        return c.execute(query, values)
    except psycopg.Error as error:
        if error.sqlstate == "40001":
            raise DomainError(
                "TECHNICAL_SCALE_REVISION_CONFLICT", "满分用途头、身份或依据已变化", 412
            ) from None
        if error.sqlstate in {"55000", "23514", "23503", "23505", "22023"}:
            raise DomainError(
                "TECHNICAL_SCALE_UNAVAILABLE", "满分上下文、精确来源或历史证明不满足确认条件", 409
            ) from None
        raise


def lock(c, bid):
    require_read_committed(c)
    require(c)
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    c.execute("SELECT design.lock_worker_bid(%s)", (bid,))
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")


def context(c, bid, *, purpose="current_prebid", submission_id=None, material_snapshot_id=None):
    lock(c, bid)
    profile = fetch(
        c,
        "SELECT bid_id,current_material_id,phase,archived_at FROM bid_profiles WHERE bid_id=%s",
        (bid,),
    )
    subject = None
    claims = []
    historical_as_of = None
    if purpose == "historical_backtest":
        if submission_id is None:
            raise DomainError("HISTORICAL_CONTEXT_REQUIRED", "请选择本项目实际实交", 422)
        submission = history.lock_submission(c, submission_id)
        if submission["bid_id"] != bid:
            raise DomainError("TECHNICAL_SCALE_SCOPE_INVALID", "实交不属于本项目", 409)
        historical_as_of = submission["submitted_at"]
        row = c.execute(
            "SELECT subject_id FROM historical_backtest.subject_heads WHERE submission_id=%s",
            (submission_id,),
        ).fetchone()
        if row:
            subject, _ = history.view(c, "subject", row["subject_id"])
        for row in c.execute(
            "SELECT claim_id FROM historical_backtest.claim_heads WHERE submission_id=%s ORDER BY document_version_id",
            (submission_id,),
        ).fetchall():
            claim, _ = history.view(c, "claim", row["claim_id"])
            claims.append(claim)
        identity = subject["identity_snapshot"] if subject else None
        material = material_snapshot_id
    elif purpose == "current_prebid" and submission_id is None and material_snapshot_id is None:
        identity = identity_snapshot(c, bid)
        material = profile["current_material_id"]
    else:
        raise DomainError("TECHNICAL_SCALE_SCOPE_INVALID", "当前与历史用途上下文不能混用", 422)
    materials = c.execute(
        "SELECT s.id,s.revision,s.created_at FROM material_snapshots s WHERE s.bid_id=%s ORDER BY s.revision",
        (bid,),
    ).fetchall()
    if material is not None and str(material) not in {str(row["id"]) for row in materials}:
        raise DomainError("TECHNICAL_SCALE_SCOPE_INVALID", "材料快照不属于本项目", 409)
    documents = (
        c.execute(
            "SELECT a.role,a.document_version_id,d.original_name,b.digest AS blob_digest,p.id AS parse_id,p.coverage,p.page_count FROM material_assignments a JOIN document_versions d ON d.id=a.document_version_id AND d.bid_id=a.bid_id JOIN blobs b ON b.id=d.blob_id LEFT JOIN LATERAL(SELECT * FROM parse_runs WHERE document_version_id=d.id ORDER BY created_at DESC,id DESC LIMIT 1)p ON true WHERE a.bid_id=%s AND a.snapshot_id=%s AND a.role IN ('tender','supplement') ORDER BY a.role,a.ordinal",
            (bid, material),
        ).fetchall()
        if material is not None
        else []
    )
    for document in documents:
        document["evidence"] = c.execute(
            "SELECT id,physical_page,digest,left(text_content,16000) AS text_content FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s ORDER BY physical_page,ordinal LIMIT 500",
            (bid, document["parse_id"]),
        ).fetchall()
        document["source_heads"] = (
            c.execute(
                "SELECT n.id AS ancestor_id,n.document_version_id,d.original_name,n.digest,n.policy_class,n.known_at,h.revision,h.status,h.valid_from,h.valid_until FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN document_versions d ON d.id=n.document_version_id AND d.bid_id=%s WHERE a.source_id=%s ORDER BY n.id",
                (bid, parse_source_id(document["parse_id"])),
            ).fetchall()
            if document["parse_id"]
            else []
        )
    scope_id = submission_id if purpose == "historical_backtest" else bid
    head = c.execute(
        "SELECT h.*,d.state FROM technical_scale.heads h JOIN technical_scale.decisions d ON d.id=h.decision_id WHERE h.bid_id=%s AND h.purpose=%s AND h.context_id=%s",
        (bid, purpose, scope_id),
    ).fetchone()
    value = clean(
        {
            "bid_id": bid,
            "purpose": purpose,
            "submission_id": submission_id,
            "historical_as_of": historical_as_of,
            "identity": identity,
            "historical_subject": subject,
            "historical_claims": claims,
            "material_snapshot_id": material,
            "material_choices": materials,
            "documents": documents,
            "head": head,
            "ordinary_work_writable": profile["archived_at"] is None
            and profile["phase"] not in {"closed", "terminated"},
            "supported_declaration_method": "explicit-whole-technical-total-and-scale/1",
            "supported_scale_descriptions": list(SUPPORTED_SCALE_DESCRIPTIONS),
            "executable_aggregation": False,
            "formal_eligibility": "unavailable",
            "paid_execution_enabled": False,
        }
    )
    return value, etag("technical-scale-context", bid, sha(value))


def capture(c, bid, body, condition, base=None):
    scope = body["context"]
    value, tag = context(
        c,
        bid,
        purpose=scope["purpose"],
        submission_id=UUID(scope["submission_id"]) if scope["submission_id"] else None,
        material_snapshot_id=UUID(scope["material_snapshot_id"])
        if scope["material_snapshot_id"]
        else None,
    )
    match(condition, tag)
    if scope["purpose"] == "current_prebid":
        assert_work_writable(c, bid)
    if value["material_snapshot_id"] is None:
        raise DomainError("TECHNICAL_SCALE_MATERIAL_REQUIRED", "请选择精确招标／补遗材料快照", 409)
    head = value["head"]
    if (head["version_id"] if head else None) != (str(base) if base else None):
        raise DomainError(
            "TECHNICAL_SCALE_REVISION_CONFLICT", "新增或更正必须指向本用途当前版本", 412
        )
    confirm = body["intent"] == "confirm"
    subject = value["historical_subject"]
    if scope["purpose"] == "historical_backtest":
        if not subject or subject["id"] != scope["historical_subject_authority_id"]:
            raise DomainError("TECHNICAL_SCALE_SCOPE_INVALID", "请选择该实交当前历史主体权威", 409)
        if confirm and (subject["manual_decision"] != "confirmed" or not subject["proofs_current"]):
            raise DomainError(
                "TECHNICAL_SCALE_UNAVAILABLE", "历史主体尚未有据确认或依据已失效", 409
            )
    elif confirm and value["identity"]["binding_status"] != "confirmed":
        raise DomainError("TECHNICAL_SCALE_UNAVAILABLE", "当前项目身份尚未显式确认", 409)
    allowed = {doc["document_version_id"]: doc for doc in value["documents"]}
    refs, blobs = [], []
    for supplied in body["evidence_refs"]:
        document = allowed.get(supplied["document_version_id"])
        if (
            not document
            or supplied["blob_digest"] != document["blob_digest"]
            or supplied["parse_id"] != document["parse_id"]
        ):
            raise DomainError(
                "TECHNICAL_SCALE_EVIDENCE_INVALID", "依据必须是本用途精确材料的当前解析", 409
            )
        parsed = historical_input._parse(c, bid, UUID(supplied["document_version_id"]))
        block = fetch(
            c,
            "SELECT * FROM evidence_blocks WHERE id=%s AND bid_id=%s AND parse_id=%s AND physical_page=%s",
            (supplied["evidence_id"], bid, supplied["parse_id"], supplied["physical_page"]),
        )
        if (
            sha(block["text_content"].encode()) != block["digest"]
            or supplied["quote"] != block["text_content"]
        ):
            raise DomainError(
                "TECHNICAL_SCALE_EVIDENCE_INVALID", "请选择完整、逐字对应的原文声明块", 409
            )
        if confirm and set(supplied["supported_fields"]) & {
            "technical_maximum",
            "scale_description",
        }:
            validate_maximum_declaration(
                source_text=block["text_content"],
                quote=supplied["quote"],
                maximum=body["technical_maximum"],
                scale_description=body["scale_description"],
            )
        refs.append(
            supplied
            | {
                "evidence_digest": block["digest"],
                "source_id": parsed["source_id"],
                "root_source_id": original_source_id(UUID(supplied["document_version_id"])),
                "heads": parsed["heads"],
            }
        )
        blobs.append(
            fetch(
                c,
                "SELECT b.* FROM blobs b JOIN document_versions d ON d.blob_id=b.id WHERE d.id=%s AND d.bid_id=%s",
                (supplied["document_version_id"], bid),
            )
        )
    # Only the canonical server _parse closure determines original versions. A Word
    # parent needs its exact original bytes and historical claim, never fake blocks.
    ancestor_documents = {
        UUID(str(head["document_version_id"])) for ref in refs for head in ref["heads"]
    }
    ancestor_blobs = (
        c.execute(
            "SELECT d.id AS document_version_id,b.* FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.bid_id=%s AND d.id=ANY(%s::uuid[]) ORDER BY d.id",
            (bid, sorted(ancestor_documents)),
        ).fetchall()
        if ancestor_documents
        else []
    )
    require_ancestor_bytes(refs, ancestor_blobs)
    blobs = list({blob["id"]: blob for blob in [*blobs, *ancestor_blobs]}.values())
    selected_claims = []
    if scope["purpose"] == "historical_backtest":
        available = {claim["id"]: claim for claim in value["historical_claims"]}
        selected_claims = [
            available[identity] for identity in scope["claim_ids"] if identity in available
        ]
        if len(selected_claims) != len(scope["claim_ids"]):
            raise DomainError(
                "TECHNICAL_SCALE_SCOPE_INVALID", "来源声明不属于该实交当前有效头", 409
            )
        if confirm:
            require_historical_claim_closure(
                refs=refs,
                claims=selected_claims,
                originals=ancestor_blobs,
                bid=str(bid),
                submission=value["submission_id"],
                subject=subject["id"],
                submitted_at=value["historical_as_of"],
            )
    return {
        "context": value,
        "context_digest": sha(value),
        "refs": refs,
        "claims": selected_claims,
        "blobs": blobs,
        "body": body,
        "base": base,
    }


def require_ancestor_bytes(refs, originals):
    documents = {str(head["document_version_id"]) for ref in refs for head in ref["heads"]}
    by_document = {str(blob["document_version_id"]): blob for blob in originals}
    if set(by_document) != documents or any(
        head["digest"] != by_document[str(head["document_version_id"])]["digest"]
        for ref in refs
        for head in ref["heads"]
    ):
        raise DomainError(
            "TECHNICAL_SCALE_EVIDENCE_INVALID", "完整祖先原件缺失、跨项目或实际摘要不一致", 409
        )


def require_historical_claim_closure(
    *, refs, claims, originals, bid, submission, subject, submitted_at
):
    from datetime import datetime

    require_ancestor_bytes(refs, originals)
    documents = {str(head["document_version_id"]) for ref in refs for head in ref["heads"]}
    selected = {str(claim["document_version_id"]) for claim in claims}
    if selected != documents or len(selected) != len(claims):
        raise DomainError(
            "TECHNICAL_SCALE_UNAVAILABLE",
            "历史可见声明必须逐份且仅覆盖完整来源祖先原件，包含受控Word原件",
            409,
        )
    by_document = {str(blob["document_version_id"]): blob for blob in originals}
    as_of = datetime.fromisoformat(submitted_at)
    for claim in claims:
        if (
            claim["bid_id"] != bid
            or claim["submission_id"] != submission
            or claim["subject_id"] != subject
            or claim["document_digest"] != by_document[str(claim["document_version_id"])]["digest"]
            or claim["manual_decision"] != "confirmed"
            or not all(
                claim[key]
                for key in (
                    "is_current_head",
                    "submission_current",
                    "related_subject_current",
                    "proofs_current",
                )
            )
        ):
            raise DomainError(
                "TECHNICAL_SCALE_UNAVAILABLE",
                "祖先原件历史声明未绑定当前实交、主体、实际字节或有据决定",
                409,
            )
        historical_input._facts(claim, as_of, "tender")
        existed = next(
            datetime.fromisoformat(fact["claimed_at"])
            for fact in claim["facts"]
            if fact["fact_kind"] == "content_existed"
        )
        if any(
            fact["claimed_at"] is not None and datetime.fromisoformat(fact["claimed_at"]) < existed
            for fact in claim["facts"]
        ):
            raise DomainError(
                "TECHNICAL_SCALE_UNAVAILABLE", "祖先声明知晓或生效不能早于同一精确内容存在", 409
            )


def verify_bytes(prepared):
    for blob in prepared["blobs"]:
        raw = storage.get(blob["object_key"])
        if len(raw) != blob["byte_size"] or sha(raw) != blob["digest"]:
            raise DomainError(
                "TECHNICAL_SCALE_SOURCE_BYTES_INVALID",
                "招标依据实际对象字节不匹配，未登记满分",
                409,
            )


def publish(c, *, bid, prepared, condition, actor, command):
    fresh = capture(c, bid, prepared["body"], condition, prepared["base"])
    if sha(clean(fresh)) != sha(clean(prepared)):
        raise DomainError(
            "TECHNICAL_SCALE_REVISION_CONFLICT", "对象核验期间满分上下文或依据已变化", 412
        )
    identity, value, body = uid(), fresh["context"], fresh["body"]
    subject = value["historical_subject"]
    c.execute(
        "INSERT INTO technical_scale.versions(id,bid_id,purpose,context_id,submission_id,subject_id,subject_decision_id,material_snapshot_id,identity_snapshot,technical_maximum,scale_description,aggregation,supersedes_id,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            bid,
            value["purpose"],
            UUID(value["submission_id"]) if value["submission_id"] else bid,
            value["submission_id"],
            subject["id"] if subject else None,
            subject["last_own_decision"]["id"] if subject else None,
            value["material_snapshot_id"],
            Jsonb(value["identity"]) if value["identity"] is not None else None,
            body["technical_maximum"],
            body["scale_description"],
            Jsonb(body["aggregation"]),
            prepared["base"],
            body["reason"],
            actor,
        ),
    )
    for ref in fresh["refs"]:
        evidence_id = uid()
        c.execute(
            "INSERT INTO technical_scale.evidence VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                evidence_id,
                identity,
                bid,
                *(
                    ref[key]
                    for key in (
                        "document_version_id",
                        "blob_digest",
                        "parse_id",
                        "evidence_id",
                        "physical_page",
                        "evidence_digest",
                        "source_id",
                        "root_source_id",
                        "quote",
                        "supported_fields",
                    )
                ),
            ),
        )
        for head in ref["heads"]:
            c.execute(
                "INSERT INTO technical_scale.source_heads VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    evidence_id,
                    *(
                        head[key]
                        for key in (
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
    for claim in fresh["claims"]:
        c.execute(
            "INSERT INTO technical_scale.claims VALUES(%s,%s,%s,%s)",
            (identity, claim["id"], claim["last_own_decision"]["id"], claim["document_version_id"]),
        )
    rpc(
        c,
        "SELECT technical_scale.apply_version(%s,%s,%s,%s,%s)",
        (
            identity,
            value["head"]["revision"] if value["head"] else 0,
            "confirmed" if body["intent"] == "confirm" else "pending",
            actor,
            command,
        ),
    )
    audit(
        c,
        actor,
        bid,
        "technical_scale.registered",
        {
            "version_id": identity,
            "purpose": value["purpose"],
            "formal_eligibility": "unavailable",
            "reason": body["reason"],
        },
        command,
    )
    return view(c, identity)


def view(c, identity):
    require(c)
    initial = fetch(c, "SELECT bid_id FROM technical_scale.versions WHERE id=%s", (identity,))
    lock(c, initial["bid_id"])
    row = fetch(
        c,
        "SELECT v.*,h.version_id AS current_version_id,h.revision,h.decision_id,d.state FROM technical_scale.versions v LEFT JOIN technical_scale.heads h ON h.bid_id=v.bid_id AND h.purpose=v.purpose AND h.context_id=v.context_id LEFT JOIN technical_scale.decisions d ON d.id=h.decision_id WHERE v.id=%s",
        (identity,),
    )
    lock(c, row["bid_id"])
    current = rpc(c, "SELECT technical_scale.current_version(%s) AS value", (identity,)).fetchone()[
        "value"
    ]
    refs = c.execute(
        "SELECT e.id,e.document_version_id,e.blob_digest,e.parse_id,e.evidence_id,e.physical_page,e.evidence_digest,e.source_id,e.quote,e.supported_fields,h.revision AS source_revision FROM technical_scale.evidence e LEFT JOIN technical_scale.source_heads h ON h.evidence_id=e.id AND h.ancestor_id=e.source_id WHERE e.version_id=%s ORDER BY e.id",
        (identity,),
    ).fetchall()
    active_decision = row["decision_id"] if row["current_version_id"] == row["id"] else None
    own_decision = c.execute(
        "SELECT id,state,created_at,actor_id,reason FROM technical_scale.decisions WHERE version_id=%s AND (%s::uuid IS NULL OR id=%s) ORDER BY created_at DESC,id DESC LIMIT 1",
        (identity, active_decision, active_decision),
    ).fetchone()
    value = clean(
        {
            "id": row["id"],
            "bid_id": row["bid_id"],
            "purpose": row["purpose"],
            "submission_id": row["submission_id"],
            "historical_subject_authority_id": row["subject_id"],
            "material_snapshot_id": row["material_snapshot_id"],
            "identity": row["identity_snapshot"],
            "technical_maximum": str(row["technical_maximum"]) if current else None,
            "recorded_maximum": str(row["technical_maximum"])
            if row["technical_maximum"] is not None
            else None,
            "scale_description": row["scale_description"],
            "aggregation": row["aggregation"],
            "maximum_usable": current,
            "aggregation_usable": False,
            "current_applicability": current,
            "state": "superseded"
            if row["current_version_id"] != row["id"]
            else "needs_revalidation"
            if own_decision and own_decision["state"] == "confirmed" and not current
            else own_decision["state"]
            if own_decision
            else "pending",
            "reasons": [] if current else ["CURRENT_AUTHORITY_OR_SOURCE_UNAVAILABLE"],
            "is_current_head": row["current_version_id"] == row["id"],
            "head_revision": row["revision"],
            "decision": own_decision,
            "supersedes_id": row["supersedes_id"],
            "evidence_refs": refs,
            "created_at": row["created_at"],
            "created_by": row["actor_id"],
            "formal_eligibility": "unavailable",
            "paid_execution_enabled": False,
        }
    )
    return value, etag("technical-scale", identity, sha(value))
