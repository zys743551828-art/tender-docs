"""Explicit identity records; registration never establishes qualification or performance."""

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain.input_changes import invalidate_pending
from packages.domain.runtime import DomainError, clean, fetch, sha, uid

REGISTERED = "identity.bidder_registered"
BOUND = "identity.bound"
DEFAULT = "identity.default_changed"


def record(c, actor, bid, event, payload, command):
    """Serialize epochs and stamp the actual action after the business lock is acquired."""
    if bid:
        fetch(c, "SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
    else:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("identity-default:" + str(actor),),
        )
    epoch = c.execute(
        "SELECT coalesce(max((payload->>'identity_epoch')::bigint),0)+1 AS n FROM audit_entries WHERE event_type=%s AND bid_id IS NOT DISTINCT FROM %s AND (%s::uuid IS NOT NULL OR actor_id=%s)",
        (event, bid, bid, actor),
    ).fetchone()["n"]
    value = clean(payload | {"identity_epoch": epoch})
    event_id = uid()
    recorded = c.execute(
        "INSERT INTO audit_entries(id,actor_id,bid_id,command_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp()) RETURNING created_at",
        (event_id, actor, bid, command, event, Jsonb(value)),
    ).fetchone()["created_at"]
    if bid:
        stream = c.execute(
            "UPDATE stream_heads SET last_seq=last_seq+1 WHERE bid_id=%s RETURNING id,last_seq",
            (bid,),
        ).fetchone()
        if stream:
            c.execute(
                "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                (stream["id"], stream["last_seq"], event_id, event, Jsonb(value), recorded),
            )


def bidder(c, bidder_id):
    row = fetch(c, "SELECT id,name FROM bidders WHERE id=%s", (bidder_id,))
    proof = c.execute(
        "SELECT id FROM audit_entries WHERE event_type=%s AND payload->>'bidder_id'=%s ORDER BY created_at,id LIMIT 1",
        (REGISTERED, str(bidder_id)),
    ).fetchone()
    return clean(row) | {
        "registered": proof is not None,
        "registration_id": str(proof["id"]) if proof else None,
    }


def require_bidder(c, bidder_id):
    result = bidder(c, bidder_id)
    if not result["registered"]:
        raise DomainError(
            "BIDDER_IDENTITY_UNCONFIRMED",
            "请明确登记真实投标主体，不能把占位身份当成已确认主体",
            409,
        )
    return result


def register_bidder(c, actor, name, reason, command_id):
    identity = uid()
    c.execute("INSERT INTO bidders(id,name) VALUES(%s,%s)", (identity, name.strip()))
    record(
        c,
        actor,
        None,
        REGISTERED,
        {"bidder_id": identity, "name": name.strip(), "reason": reason.strip()},
        command_id,
    )
    return bidder(c, identity)


def default_bidder(c, actor):
    row = c.execute(
        "SELECT id,payload FROM audit_entries WHERE actor_id=%s AND bid_id IS NULL AND event_type=%s ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1",
        (actor, DEFAULT),
    ).fetchone()
    return {
        "bidder": require_bidder(c, row["payload"]["bidder_id"]) if row else None,
        "authority_id": str(row["id"]) if row else None,
    }


def set_default(c, actor, bidder_id, command_id):
    require_bidder(c, bidder_id)
    record(c, actor, None, DEFAULT, {"bidder_id": bidder_id}, command_id)
    return default_bidder(c, actor)


def association(c, bid):
    return clean(
        fetch(
            c,
            "SELECT p.bidder_id,b.name AS bidder_name,p.round_id,r.engineering_project_id,e.title AS engineering_title,e.region,e.engineering_type,r.lot,r.round_no FROM bid_profiles p JOIN bidders b ON b.id=p.bidder_id JOIN bid_rounds r ON r.id=p.round_id JOIN engineering_projects e ON e.id=r.engineering_project_id WHERE p.bid_id=%s",
            (bid,),
        )
    )


def snapshot(c, bid):
    current = association(c, bid)
    row = c.execute(
        "SELECT id,payload FROM audit_entries WHERE bid_id=%s AND event_type=%s ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1",
        (bid, BOUND),
    ).fetchone()
    confirmed = bool(row and row["payload"]["after"] == current and row["payload"]["confirmed"])
    value = current | {
        "binding_status": "confirmed" if confirmed else "pending",
        "authority_id": str(row["id"]) if row else None,
    }
    return value | {"digest": sha(value)}


def at_time(c, bid, as_of):
    """Never reconstruct a past submission from a corrected current profile."""
    row = c.execute(
        "SELECT id,payload FROM audit_entries WHERE bid_id=%s AND event_type=%s AND created_at<=%s ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1",
        (bid, BOUND, as_of),
    ).fetchone()
    if not row:
        return {
            "binding_status": "unavailable",
            "authority_id": None,
            "reason": "NO_FROZEN_IDENTITY_AT_TIME",
        }
    return row["payload"]["after"] | {
        "binding_status": "confirmed" if row["payload"]["confirmed"] else "pending",
        "authority_id": str(row["id"]),
        "qualification_evidence": False,
        "notice": "审计时点观察不代替实交或评审中冻结的身份依据",
    }


def record_binding(c, bid, actor, command_id, *, before=None, reason, confirmed):
    after = association(c, bid)
    record(
        c,
        actor,
        bid,
        BOUND,
        {"before": before, "after": after, "confirmed": confirmed, "reason": reason},
        command_id,
    )
    return snapshot(c, bid)


def create_association(c, body):
    """Caller inserts bid/profile and records the explicit binding in the same transaction."""
    bidder_id = UUID(body["bidder_id"]) if body.get("bidder_id") else uid()
    confirmed = bool(body.get("bidder_id"))
    if confirmed:
        require_bidder(c, bidder_id)
    else:
        c.execute(
            "INSERT INTO bidders(id,name) VALUES(%s,'本公司（部署主体待核验）')", (bidder_id,)
        )
    engineering = (
        UUID(body["engineering_project_id"]) if body.get("engineering_project_id") else None
    )
    if body.get("round_id"):
        row = fetch(c, "SELECT * FROM bid_rounds WHERE id=%s", (UUID(body["round_id"]),))
        if engineering and row["engineering_project_id"] != engineering:
            raise DomainError("ROUND_ENGINEERING_MISMATCH", "所选轮次不属于所选工程", 422)
        if body.get("lot") is not None and body["lot"] != row["lot"]:
            raise DomainError("ROUND_LOT_MISMATCH", "所选轮次的标段与表单不一致", 422)
        project = fetch(
            c, "SELECT * FROM engineering_projects WHERE id=%s", (row["engineering_project_id"],)
        )
        for field in ("region", "engineering_type"):
            if field in body and body[field] != project[field]:
                raise DomainError(
                    "ENGINEERING_CONTEXT_MISMATCH",
                    "表单地区或专业与所选工程不一致",
                    422,
                    {"field": field},
                )
        return {
            "engineering_project_id": row["engineering_project_id"],
            "round_id": row["id"],
            "bidder_id": bidder_id,
            "confirmed": confirmed,
        }
    if engineering:
        project = fetch(
            c, "SELECT * FROM engineering_projects WHERE id=%s FOR UPDATE", (engineering,)
        )
        for field in ("region", "engineering_type"):
            if field in body and body[field] != project[field]:
                raise DomainError(
                    "ENGINEERING_CONTEXT_MISMATCH",
                    "表单地区或专业与所选工程不一致",
                    422,
                    {"field": field},
                )
    else:
        engineering = uid()
        c.execute(
            "INSERT INTO engineering_projects(id,title,region,engineering_type) VALUES(%s,%s,%s,%s)",
            (engineering, body["title"], body.get("region"), body.get("engineering_type")),
        )
    round_no = c.execute(
        "SELECT coalesce(max(round_no),0)+1 AS n FROM bid_rounds WHERE engineering_project_id=%s AND lot IS NOT DISTINCT FROM %s",
        (engineering, body.get("lot")),
    ).fetchone()["n"]
    round_id = uid()
    c.execute(
        "INSERT INTO bid_rounds(id,engineering_project_id,lot,round_no) VALUES(%s,%s,%s,%s)",
        (round_id, engineering, body.get("lot"), round_no),
    )
    return {
        "engineering_project_id": engineering,
        "round_id": round_id,
        "bidder_id": bidder_id,
        "confirmed": confirmed,
    }


def impact(c, bid):
    queries = {
        "reviews": "SELECT count(*) AS n FROM review_runs WHERE bid_id=%s",
        "submissions": "SELECT count(*) AS n FROM submission_records WHERE bid_id=%s",
        "outcomes": "SELECT count(*) AS n FROM outcomes WHERE bid_id=%s",
        "samples": "SELECT count(*) AS n FROM samples WHERE bid_id=%s",
        "drafts": "SELECT count(*) AS n FROM drafts WHERE bid_id=%s",
        "documents": "SELECT count(*) AS n FROM document_versions WHERE bid_id=%s",
        "facts": "SELECT count(*) AS n FROM fact_items WHERE bid_id=%s",
        "knowledge_citations": "SELECT count(*) AS n FROM retrieval.citations c JOIN retrieval.source_nodes n ON n.id=c.source_id WHERE c.bid_id=%s AND n.kind='knowledge'",
    }
    return {key: c.execute(query, (bid,)).fetchone()["n"] for key, query in queries.items()}


def correct(c, bid, actor, command_id, body):
    # Caller holds the ordinary bid execution lock and validates its strong ETag.
    old = snapshot(c, bid)
    require_bidder(c, body["bidder_id"])
    round_id = UUID(body["round_id"]) if body.get("round_id") else UUID(old["round_id"])
    fetch(c, "SELECT id FROM bid_rounds WHERE id=%s", (round_id,))
    if (
        old["binding_status"] == "confirmed"
        and str(round_id) == old["round_id"]
        and body["bidder_id"] == old["bidder_id"]
    ):
        raise DomainError("IDENTITY_UNCHANGED", "当前身份已经确认，无需重复更正", 409)
    affected = impact(c, bid)
    c.execute(
        "UPDATE bid_profiles SET bidder_id=%s,round_id=%s,current_review_id=NULL WHERE bid_id=%s",
        (UUID(body["bidder_id"]), round_id, bid),
    )
    c.execute("UPDATE bids SET revision=revision+1 WHERE id=%s", (bid,))
    invalidate_pending(
        c, bid, "投标主体或工程轮次已更正，请核对当前身份后重新评审", "REVIEW_IDENTITY_CHANGED"
    )
    c.execute(
        "UPDATE review_slots SET context_fingerprint=NULL,context_as_of=NULL,context_valid_until=NULL,revision=revision+1 WHERE bid_id=%s",
        (bid,),
    )
    current = record_binding(
        c, bid, actor, command_id, before=old, reason=body["reason"].strip(), confirmed=True
    )
    return {
        "identity": current,
        "affected": affected,
        "historical_records_rewritten": False,
        "qualification_recheck_required": True,
    }
