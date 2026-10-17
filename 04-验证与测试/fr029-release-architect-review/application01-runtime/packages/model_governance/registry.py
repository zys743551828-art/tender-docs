"""API orchestration for immutable artifacts; object I/O must run outside connections."""

from uuid import UUID, uuid5

import psycopg

from packages.domain import storage
from packages.domain.runtime import DomainError, audit, clean, etag, fetch, sha, uid
from packages.model_governance import artifacts
from packages.model_governance.common import GovernanceError, digest
from packages.model_governance.repository import match


def configured(c):
    return c.execute(
        "SELECT to_regprocedure('model_release.register_artifact(uuid,uuid,uuid,text,uuid)') IS NOT NULL AND to_regprocedure('model_release.change_route(text,text,uuid,bigint,uuid,text,uuid,text,uuid)') IS NOT NULL AS value"
    ).fetchone()["value"]


def require(c):
    if not configured(c):
        raise DomainError(
            "MODEL_RELEASE_NOT_CONFIGURED", "模型登记与路由能力尚未配置，正式模型仍不可用", 503
        )


def _execute(c, query, params):
    try:
        return c.execute(query, params)
    except psycopg.Error as error:
        # No raw SQL exception text, parameters, provider output or DSN is exposed.
        if error.sqlstate == "40001":
            raise DomainError(
                "MODEL_ROUTE_REVISION_CONFLICT", "模型路由已变化，请重新读取", 412
            ) from None
        if error.sqlstate == "55000":
            raise DomainError(
                "MODEL_AUTHORITY_UNAVAILABLE", "来源已失效或缺少正式模型验收权威", 409
            ) from None
        if error.sqlstate == "23505":
            raise DomainError(
                "MODEL_REGISTRATION_EXISTS", "训练结果已经登记，请读取既有候选", 409
            ) from None
        if error.sqlstate in {"23514", "22023", "22P02"}:
            raise DomainError(
                "MODEL_AUTHORITY_MISMATCH", "模型产物、范围或命令与冻结权威不一致", 422
            ) from None
        raise


def preview(c, training_job_id):
    require(c)
    identity = uuid5(UUID(str(training_job_id)), "registered-score-artifact/1")
    document = _execute(
        c, "SELECT model_release.registration_document(%s,%s) AS value", (training_job_id, identity)
    ).fetchone()["value"]
    try:
        artifacts.validate(document)
    except GovernanceError as error:
        raise DomainError(error.code, str(error), 409) from None
    body = {
        "model_id": str(identity),
        "training_job_id": str(training_job_id),
        "document": document,
        "formal_eligibility": "unavailable",
    }
    return body, etag("model-registration-preview", identity, sha(body))


def stage(document):
    raw = artifacts.encode(document)
    identity = uuid5(UUID(document["model_id"]), "registered-score-artifact-blob/1")
    key = f"models/registered/{document['model_id']}/{sha(raw)}.json"
    storage.put(key, raw, artifacts.MEDIA)
    return {
        "id": identity,
        "digest": sha(raw),
        "object_key": key,
        "byte_size": len(raw),
        "media_type": artifacts.MEDIA,
        "raw": raw,
    }


def publish(c, *, actor, training_job_id, document, staged, condition, reason, command):
    current, tag = preview(c, training_job_id)
    match(condition, tag)
    if digest(document) != digest(current["document"]) or sha(staged["raw"]) != staged["digest"]:
        raise DomainError("MODEL_REGISTRATION_CHANGED", "登记期间冻结输入发生变化", 409)
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s)",
        tuple(staged[k] for k in ("id", "digest", "object_key", "byte_size", "media_type")),
    )
    _execute(
        c,
        "SELECT model_release.register_artifact(%s,%s,%s,%s,%s)",
        (document["model_id"], training_job_id, staged["id"], staged["raw"].decode(), actor),
    )
    audit(
        c,
        actor,
        None,
        "model.candidate_registered",
        {
            "model_id": document["model_id"],
            "training_job_id": str(training_job_id),
            "reason": reason,
            "formal_eligibility": "unavailable",
        },
        command,
    )
    return view(c, document["model_id"])


def blob(c, model_id):
    require(c)
    return fetch(
        c,
        "SELECT b.*,r.document,r.training_job_id,r.cohort_id,r.created_at AS registered_at FROM model_release.registrations r JOIN model_artifacts m ON m.id=r.model_id JOIN blobs b ON b.id=m.artifact_blob_id WHERE r.model_id=%s",
        (model_id,),
    )


def read(stored):
    if stored["byte_size"] > artifacts.MAX_BYTES:
        raise DomainError("ARTIFACT_TOO_LARGE", "登记模型超过受控容量", 409)
    raw = storage.get(stored["object_key"])
    try:
        return artifacts.load(
            raw,
            expected_digest=stored["digest"],
            expected_size=stored["byte_size"],
            expected_document=stored["document"],
        )
    except GovernanceError as error:
        raise DomainError(error.code, str(error), 409) from None


def view(c, model_id):
    stored = blob(c, model_id)
    current = fetch(
        c, "SELECT model_governance.current_cohort(%s) AS value", (stored["cohort_id"],)
    )["value"]
    row = fetch(
        c,
        "SELECT encode(sha256(convert_to((document->'scope')::text,'UTF8')),'hex') AS route_key FROM model_release.registrations WHERE model_id=%s",
        (model_id,),
    )
    value = {
        "model_id": str(model_id),
        "training_job_id": str(stored["training_job_id"]),
        "artifact_digest": stored["digest"],
        "registered_at": clean(stored["registered_at"]),
        "origin": stored["document"]["origin"],
        "research_only": stored["document"]["research_only"],
        "current": current,
        "scope": stored["document"]["scope"],
        "route_key": row["route_key"],
        "formal_eligibility": "unavailable",
    }
    return value, etag("registered-score-model", model_id, sha(value))


def route(c, key, lane):
    require(c)
    # Acquire union scope BEFORE current_cohort's graph lock, just like SQL change_route.
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    row = c.execute(
        "SELECT * FROM model_release.routes WHERE route_key=%s AND lane=%s", (key, lane)
    ).fetchone()
    value = {
        "route_key": key,
        "lane": lane,
        "revision": 0,
        "model_id": None,
        "rollback_model_id": None,
        "current": False,
        "formal_eligibility": "unavailable",
    }
    if row:
        c.execute(
            "SELECT model_release.lock_scope(%s)", ([row["model_id"], row["rollback_model_id"]],)
        )
        value.update({k: clean(row[k]) for k in ("revision", "model_id", "rollback_model_id")})
        if row["model_id"]:
            candidate = fetch(
                c,
                "SELECT cohort_id FROM model_release.registrations WHERE model_id=%s",
                (row["model_id"],),
            )
            value["current"] = fetch(
                c, "SELECT model_governance.current_cohort(%s) AS value", (candidate["cohort_id"],)
            )["value"]
    return value, etag("model-route", f"{lane}:{key}", sha(value))


def change_route(
    c, *, key, lane, model_id, certification_id, action, actor, reason, condition, command
):
    # Calling route() here before acquiring the candidate's whole scope would lock the graph
    # too early. First take the union of target/current/rollback bids under group coordination.
    require(c)
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    old = c.execute(
        "SELECT model_id,rollback_model_id FROM model_release.routes WHERE route_key=%s AND lane=%s",
        (key, lane),
    ).fetchone()
    c.execute("SELECT model_release.lock_scope(%s)", ([model_id, *(old.values() if old else [])],))
    current, tag = route(c, key, lane)
    match(condition, tag)
    _execute(
        c,
        "SELECT model_release.change_route(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (key, lane, model_id, current["revision"], certification_id, action, actor, reason, uid()),
    )
    audit(
        c,
        actor,
        None,
        "model.route_changed",
        {
            "route_key": key,
            "lane": lane,
            "action": action,
            "model_id": str(model_id) if model_id else None,
            "formal_eligibility": "unavailable",
        },
        command,
    )
    return route(c, key, lane)
