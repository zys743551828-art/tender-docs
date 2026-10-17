"""Historical proof API only. No model content, online qualification or worker access."""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, etag, fetch, sha, uid
from packages.model_governance.repository import match
from packages.rag.indexing import original_source_id, parse_source_id


def configured(c):
    return c.execute(
        "SELECT to_regprocedure('historical_backtest.decide_subject(uuid,bigint,text,text,uuid,uuid)') IS NOT NULL AND to_regprocedure('historical_backtest.decide_claim(uuid,bigint,text,text,uuid,uuid)') IS NOT NULL AS value"
    ).fetchone()["value"]


def require(c):
    if not configured(c):
        raise DomainError(
            "HISTORICAL_PROOF_NOT_CONFIGURED", "历史证明能力尚未配置，历史回测输入仍不可用", 503
        )


def lock_submission(c, identity, *, require_current=True):
    if (
        c.execute("SELECT current_setting('transaction_isolation') AS isolation").fetchone()[
            "isolation"
        ]
        != "read committed"
    ):
        raise DomainError(
            "HISTORICAL_CONTEXT_REQUIRES_READ_COMMITTED",
            "当前历史依据核验不能使用旧事务快照，请从最新上下文重新发起",
            409,
        )
    require(c)
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    initial = fetch(c, "SELECT bid_id FROM submission_records WHERE id=%s", (identity,))
    c.execute("SELECT design.lock_worker_bid(%s)", (initial["bid_id"],))
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    row = fetch(
        c,
        "SELECT s.*,b.digest AS body_digest FROM submission_records s JOIN document_versions d ON d.id=s.body_version_id JOIN blobs b ON b.id=d.blob_id WHERE s.id=%s",
        (identity,),
    )
    current = c.execute(
        "SELECT revision FROM submission_heads WHERE bid_id=%s AND submission_id=%s",
        (row["bid_id"], identity),
    ).fetchone()
    if not current and require_current:
        raise DomainError(
            "HISTORICAL_SUBMISSION_STALE", "实交有效头已变化，请从当前实交补充依据", 409
        )
    row["current_submission"] = bool(current)
    return row


def context(c, submission_id):
    submission = lock_submission(c, submission_id)
    subjects = c.execute(
        "SELECT h.subject_id,h.decision_id,h.revision,d.decision FROM historical_backtest.subject_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=%s",
        (submission_id,),
    ).fetchone()
    claims = c.execute(
        "SELECT h.document_version_id,h.claim_id,h.decision_id,h.revision,d.decision FROM historical_backtest.claim_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=%s ORDER BY h.document_version_id",
        (submission_id,),
    ).fetchall()
    value = {
        "submission_id": str(submission_id),
        "bid_id": str(submission["bid_id"]),
        "body_version_id": str(submission["body_version_id"]),
        "body_digest": submission["body_digest"],
        "submitted_at": clean(submission["submitted_at"]),
        "original_review_id": clean(submission["review_id"]),
        "subject_head": clean(subjects),
        "claim_heads": clean(claims),
        "historical_input_available": False,
        "formal_eligibility": "unavailable",
        "prediction_timing": "retrospective",
    }
    return value, etag("historical-proof-context", submission_id, sha(value))


def exact_proof(c, bid, value):
    source, root = (
        parse_source_id(UUID(str(value["parse_id"]))),
        original_source_id(UUID(str(value["document_version_id"]))),
    )
    row = c.execute(
        "SELECT e.id FROM evidence_blocks e JOIN parse_runs p ON p.id=e.parse_id JOIN document_versions d ON d.id=p.document_version_id JOIN blobs b ON b.id=d.blob_id JOIN pages page ON page.parse_id=p.id AND page.physical_page=e.physical_page JOIN retrieval.source_nodes n ON n.id=%s JOIN retrieval.source_nodes root ON root.id=%s WHERE e.id=%s AND e.bid_id=%s AND e.parse_id=%s AND e.physical_page=%s AND e.digest=%s AND position(%s IN e.text_content)>0 AND d.id=%s AND b.digest=%s AND p.id=(SELECT latest.id FROM parse_runs latest WHERE latest.document_version_id=d.id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1) AND p.coverage='complete' AND p.gaps='[]'::jsonb AND page.quality='usable' AND page.gaps='[]'::jsonb AND n.kind='document' AND root.kind='document' AND n.document_version_id=d.id AND root.document_version_id=d.id AND n.digest=b.digest AND root.digest=b.digest AND EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=n.id AND edge.parent_id=root.id)",
        (
            source,
            root,
            value["evidence_id"],
            bid,
            value["parse_id"],
            value["physical_page"],
            value["evidence_digest"],
            value["quote"],
            value["document_version_id"],
            value.get("blob_digest", value.get("document_digest")),
        ),
    ).fetchone()
    if not row:
        raise DomainError(
            "HISTORICAL_PROOF_EVIDENCE_INVALID",
            "历史依据的原页、逐字引用、版本或当前解析不匹配",
            422,
        )
    heads = c.execute(
        "SELECT n.id AS ancestor_id,h.revision,n.digest,n.policy_class,n.known_at,h.valid_from,h.valid_until,h.status,d.bid_id AS document_bid,n.known_at<=clock_timestamp() AND h.valid_from<=clock_timestamp() AND (h.valid_until IS NULL OR h.valid_until>clock_timestamp()) AS in_window FROM retrieval.source_ancestors ancestor JOIN retrieval.source_nodes n ON n.id=ancestor.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN document_versions d ON d.id=n.document_version_id WHERE ancestor.source_id=%s ORDER BY n.id",
        (source,),
    ).fetchall()
    if not heads or any(
        h["status"] != "published" or not h["in_window"] or h["document_bid"] != UUID(str(bid))
        for h in heads
    ):
        raise DomainError(
            "HISTORICAL_PROOF_SOURCE_UNAVAILABLE",
            "历史依据的完整来源存在未发布、撤回、未来时点或跨项目缺口",
            409,
        )
    return source, root, heads


def insert_proof(c, *, bid, subject_id=None, claim_id=None, fact_kind=None, value):
    source, root, heads = exact_proof(c, bid, value)
    identity = uid()
    _execute(
        c,
        "INSERT INTO historical_backtest.proofs(id,bid_id,subject_id,claim_id,fact_kind,document_version_id,document_digest,parse_id,evidence_id,physical_page,evidence_digest,source_id,root_source_id,quote,explanation) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            bid,
            subject_id,
            claim_id,
            fact_kind,
            value["document_version_id"],
            value["blob_digest"],
            value["parse_id"],
            value["evidence_id"],
            value["physical_page"],
            value["evidence_digest"],
            source,
            root,
            value["quote"],
            value["explanation"],
        ),
    )
    for head in heads:
        _execute(
            c,
            "INSERT INTO historical_backtest.proof_heads(proof_id,ancestor_id,revision,digest,policy_class,known_at,valid_from,valid_until) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identity,
                *(
                    head[k]
                    for k in (
                        "ancestor_id",
                        "revision",
                        "digest",
                        "policy_class",
                        "known_at",
                        "valid_from",
                        "valid_until",
                    )
                ),
            ),
        )


def _execute(c, query, values):
    try:
        return c.execute(query, values)
    except psycopg.Error as error:
        if error.sqlstate == "40001":
            raise DomainError(
                "HISTORICAL_AUTHORITY_REVISION_CONFLICT", "历史证明有效头已变化", 412
            ) from None
        if error.sqlstate in {"55000", "23514", "23503"}:
            raise DomainError(
                "HISTORICAL_AUTHORITY_NOT_CONFIRMED",
                "历史关系或依据不完整、已冲突或失效，尚未确认",
                409,
            ) from None
        raise


def _proofs(c, kind, identity):
    statement = (
        "SELECT * FROM historical_backtest.proofs WHERE subject_id=%s ORDER BY id"
        if kind == "subject"
        else "SELECT * FROM historical_backtest.proofs WHERE claim_id=%s ORDER BY id"
    )
    rows = c.execute(statement, (identity,)).fetchall()
    return rows


def proof_state(c, kind, identity):
    values = _proofs(c, kind, identity)
    if not values:
        return False
    for proof in values:
        try:
            source, root, current = exact_proof(c, proof["bid_id"], clean(proof))
        except DomainError:
            return False
        if source != proof["source_id"] or root != proof["root_source_id"]:
            return False
        frozen = c.execute(
            "SELECT ancestor_id,revision,digest,policy_class,known_at,valid_from,valid_until FROM historical_backtest.proof_heads WHERE proof_id=%s ORDER BY ancestor_id",
            (proof["id"],),
        ).fetchall()
        projected = [
            {
                key: head[key]
                for key in (
                    "ancestor_id",
                    "revision",
                    "digest",
                    "policy_class",
                    "known_at",
                    "valid_from",
                    "valid_until",
                )
            }
            for head in current
        ]
        if frozen != projected:
            return False
    return True


def subject_relationship(c, subject, submission):
    if (
        subject["bid_id"] != submission["bid_id"]
        or subject["submission_id"] != submission["id"]
        or subject["body_version_id"] != submission["body_version_id"]
        or subject["body_digest"] != submission["body_digest"]
        or subject["submitted_at"] != submission["submitted_at"]
        or subject["identity_snapshot"] is None
    ):
        return False
    return bool(
        c.execute(
            "SELECT EXISTS(SELECT FROM bid_rounds r WHERE r.id=%s AND r.engineering_project_id=%s AND %s::jsonb->'round_no'=to_jsonb(r.round_no) AND %s::jsonb->'lot' IS NOT DISTINCT FROM coalesce(to_jsonb(r.lot),'null'::jsonb)) AS value",
            (
                subject["round_id"],
                subject["engineering_project_id"],
                Jsonb(subject["identity_snapshot"]),
                Jsonb(subject["identity_snapshot"]),
            ),
        ).fetchone()["value"]
    )


def view(c, kind, identity):
    require(c)
    statement = (
        "SELECT * FROM historical_backtest.subjects WHERE id=%s"
        if kind == "subject"
        else "SELECT * FROM historical_backtest.claims WHERE id=%s"
    )
    row = fetch(c, statement, (identity,))
    submission = lock_submission(c, row["submission_id"], require_current=False)
    if kind == "subject":
        head = c.execute(
            "SELECT h.subject_id AS object_id,h.revision,d.* FROM historical_backtest.subject_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=%s",
            (row["submission_id"],),
        ).fetchone()
    else:
        head = c.execute(
            "SELECT h.claim_id AS object_id,h.revision,d.* FROM historical_backtest.claim_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=%s AND h.document_version_id=%s",
            (row["submission_id"], row["document_version_id"]),
        ).fetchone()
    own_decision = c.execute(
        "SELECT id,decision,actor_id,reason,created_at FROM historical_backtest.decisions WHERE object_kind=%s AND object_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (kind, identity),
    ).fetchone()
    value = clean(row) | {
        "last_own_decision": clean(own_decision),
        "kind": kind,
        "head_revision": head["revision"] if head else 0,
        "is_current_head": bool(
            head and head["object_id"] == row["id"] and submission["current_submission"]
        ),
        "submission_current": submission["current_submission"],
        "manual_decision": head["decision"]
        if head and head["object_id"] == row["id"]
        else "superseded",
        "proofs_current": proof_state(c, kind, identity),
        "proof_refs": clean(_proofs(c, kind, identity)),
        "historical_input_available": False,
        "formal_eligibility": "unavailable",
    }
    value["subject_relationship_current"] = (
        subject_relationship(c, row, submission) if kind == "subject" else None
    )
    value["related_subject_current"] = None
    if kind == "claim":
        subject = c.execute(
            "SELECT s.* FROM historical_backtest.subjects s JOIN historical_backtest.subject_heads h ON h.subject_id=s.id JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE s.id=%s AND s.bid_id=%s AND s.submission_id=%s AND d.decision='confirmed'",
            (row["subject_id"], row["bid_id"], row["submission_id"]),
        ).fetchone()
        value["related_subject_current"] = bool(
            subject
            and subject_relationship(c, subject, submission)
            and proof_state(c, "subject", subject["id"])
        )
        value["facts"] = clean(
            c.execute(
                "SELECT * FROM historical_backtest.facts WHERE claim_id=%s ORDER BY fact_kind",
                (identity,),
            ).fetchall()
        )
    return value, etag("historical-" + kind, identity, sha(value))


def save_subject(c, *, actor, body, condition, command):
    _, tag = context(c, body["submission_id"])
    match(condition, tag)
    submission = lock_submission(c, body["submission_id"])
    identity = uid()
    proposal = body["proposed_identity"]
    _execute(
        c,
        "INSERT INTO historical_backtest.subjects(id,bid_id,submission_id,body_version_id,body_digest,submitted_at,bidder_id,round_id,engineering_project_id,identity_snapshot,declared_origin,supersedes_id,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            submission["bid_id"],
            submission["id"],
            submission["body_version_id"],
            submission["body_digest"],
            submission["submitted_at"],
            proposal["bidder_id"] if proposal else None,
            proposal["round_id"] if proposal else None,
            proposal["engineering_project_id"] if proposal else None,
            Jsonb(proposal) if proposal else None,
            body["declared_data_origin"],
            body["supersedes_id"],
            body["reason"],
            actor,
        ),
    )
    for proof in body["proof_refs"]:
        insert_proof(c, bid=submission["bid_id"], subject_id=identity, value=proof)
    head = c.execute(
        "SELECT revision FROM historical_backtest.subject_heads WHERE submission_id=%s",
        (submission["id"],),
    ).fetchone()
    _execute(
        c,
        "SELECT historical_backtest.decide_subject(%s,%s,'pending',%s,%s,%s)",
        (identity, head["revision"] if head else 0, body["reason"], actor, uid()),
    )
    audit(
        c,
        actor,
        submission["bid_id"],
        "historical.subject_saved_pending",
        {"subject_id": str(identity), "reason": body["reason"]},
        command,
    )
    return view(c, "subject", identity)


def save_claim(c, *, actor, body, condition, command):
    _, tag = context(c, body["submission_id"])
    match(condition, tag)
    submission = lock_submission(c, body["submission_id"])
    document = fetch(
        c,
        "SELECT b.digest FROM document_versions d JOIN blobs b ON b.id=d.blob_id WHERE d.id=%s AND d.bid_id=%s",
        (body["document_version_id"], submission["bid_id"]),
    )
    identity = uid()
    _execute(
        c,
        "INSERT INTO historical_backtest.claims(id,bid_id,submission_id,document_version_id,document_digest,subject_id,declared_origin,supersedes_id,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            submission["bid_id"],
            submission["id"],
            body["document_version_id"],
            document["digest"],
            body["historical_subject_authority_id"],
            body["declared_data_origin"],
            body["supersedes_id"],
            body["reason"],
            actor,
        ),
    )
    for fact in body["facts"]:
        _execute(
            c,
            "INSERT INTO historical_backtest.facts(claim_id,fact_kind,claimed_at,valid_until,explanation) VALUES(%s,%s,%s,%s,%s)",
            (
                identity,
                fact["fact_kind"],
                fact["claimed_at"],
                fact["valid_until"],
                fact["explanation"],
            ),
        )
        for proof in fact["proof_refs"]:
            insert_proof(
                c,
                bid=submission["bid_id"],
                claim_id=identity,
                fact_kind=fact["fact_kind"],
                value=proof,
            )
    head = c.execute(
        "SELECT revision FROM historical_backtest.claim_heads WHERE submission_id=%s AND document_version_id=%s",
        (submission["id"], body["document_version_id"]),
    ).fetchone()
    _execute(
        c,
        "SELECT historical_backtest.decide_claim(%s,%s,'pending',%s,%s,%s)",
        (identity, head["revision"] if head else 0, body["reason"], actor, uid()),
    )
    audit(
        c,
        actor,
        submission["bid_id"],
        "historical.claim_saved_pending",
        {"claim_id": str(identity), "reason": body["reason"]},
        command,
    )
    return view(c, "claim", identity)


def confirm(c, *, kind, identity, actor, reason, condition, command):
    value, tag = view(c, kind, identity)
    match(condition, tag)
    if not value["is_current_head"]:
        raise DomainError("HISTORICAL_AUTHORITY_STALE", "请使用当前历史证明版本", 409)
    statement = (
        "SELECT historical_backtest.decide_subject(%s,%s,'confirmed',%s,%s,%s)"
        if kind == "subject"
        else "SELECT historical_backtest.decide_claim(%s,%s,'confirmed',%s,%s,%s)"
    )
    _execute(
        c,
        statement,
        (identity, value["head_revision"], reason, actor, uid()),
    )
    audit(
        c,
        actor,
        value["bid_id"],
        "historical.human_fact_confirmed",
        {
            "kind": kind,
            "authority_id": str(identity),
            "reason": reason,
            "formal_eligibility": "unavailable",
        },
        command,
    )
    return view(c, kind, identity)
