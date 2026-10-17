"""Authenticated API-side registry. Purpose workers never import this module."""

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, etag, fetch, sha, uid
from packages.model_governance import training
from packages.model_governance.common import GovernanceError, digest


def configured(c):
    return bool(
        c.execute(
            "SELECT to_regclass('model_governance.cohorts') IS NOT NULL AND to_regprocedure('model_governance.current_cohort(uuid)') IS NOT NULL AS value"
        ).fetchone()["value"]
    )


def require(c):
    if not configured(c):
        raise DomainError(
            "MODEL_GOVERNANCE_NOT_CONFIGURED",
            "模型治理尚未在独立初始化基线中配置，正式模型仍不可用",
            503,
        )


def match(header, expected):
    if not header:
        raise DomainError("PRECONDITION_REQUIRED", "请先读取冻结研究或任务资源", 428)
    if header != expected:
        raise DomainError(
            "REVISION_CONFLICT",
            "冻结输入或模型任务已变化，请重新核对",
            412,
            {"current_etag": expected},
        )


def protocol_view(c, identity):
    require(c)
    row = fetch(c, "SELECT * FROM model_governance.protocols WHERE id=%s", (identity,))
    value = clean(row) | {
        "formal_eligibility": "unavailable",
        "research_only": row["origin"] == "synthetic",
    }
    return value, etag("model-training-protocol", identity, sha(value))


def register_protocol(c, *, actor, document, reason, command):
    require(c)
    try:
        training.validate_protocol(document)
    except GovernanceError as error:
        raise DomainError(error.code, str(error), 422) from None
    identity = uid()
    # Current new software surface is explicitly synthetic research-only. No client origin or
    # qualification flag exists. Real provenance+business acceptance is a separate gate.
    protocol_digest = digest(document)
    if c.execute(
        "SELECT id FROM model_governance.protocols WHERE digest=%s", (protocol_digest,)
    ).fetchone():
        raise DomainError("TRAINING_PROTOCOL_EXISTS", "相同冻结协议已登记，请读取既有协议", 409)
    c.execute(
        "INSERT INTO model_governance.protocols(id,digest,document,origin,created_by) VALUES(%s,%s,%s,'synthetic',%s)",
        (identity, protocol_digest, Jsonb(document), actor),
    )
    audit(
        c,
        actor,
        None,
        "model.training_protocol_registered",
        {"protocol_id": str(identity), "reason": reason, "formal_eligibility": "unavailable"},
        command,
    )
    return protocol_view(c, identity)


def cohort_view(c, identity):
    require(c)
    # current_cohort takes the global grouping lock and sorted complete related bid scope first.
    current = fetch(c, "SELECT model_governance.current_cohort(%s) AS value", (identity,))["value"]
    row = fetch(
        c,
        "SELECT c.*,p.digest AS protocol_digest FROM model_governance.cohorts c JOIN model_governance.protocols p ON p.id=c.protocol_id WHERE c.id=%s",
        (identity,),
    )
    counts = c.execute(
        "SELECT purpose,count(*) AS count FROM model_governance.members WHERE cohort_id=%s GROUP BY purpose ORDER BY purpose",
        (identity,),
    ).fetchall()
    value = clean(row) | {
        "member_counts": clean(counts),
        "current": current,
        "formal_eligibility": "unavailable",
        "research_only": row["origin"] == "synthetic",
    }
    return value, etag("model-cohort", identity, sha(value))


def publish_cohort(
    c, *, actor, run_id, protocol_id, exports, records, condition, preview_tag, command
):
    require(c)
    match(condition, preview_tag)
    protocol, _ = protocol_view(c, protocol_id)
    identity = uid()
    origin = protocol["origin"]
    purpose = {row["id"]: row["purpose"] for row in exports["training"]["members"]}
    c.execute(
        "INSERT INTO model_governance.cohorts(id,research_run_id,protocol_id,digest,origin,test_start,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            run_id,
            protocol_id,
            digest({"records": records, "exports": exports}),
            origin,
            exports["training"]["test_start"],
            actor,
        ),
    )
    json_fields = {"identity_snapshot"}
    for record in records["members"]:
        row = {"cohort_id": identity, **record, "purpose": purpose[record["sample_id"]]}
        _insert(c, "members", row, json_fields)
    for collection, table in [
        ("chapters", "chapter_bindings"),
        ("parses", "parse_bindings"),
        ("evidence", "evidence_bindings"),
        ("sources", "source_bindings"),
    ]:
        for record in records[collection]:
            _insert(c, table, {"cohort_id": identity, **record}, set())
    for purpose_name, payload in exports.items():
        c.execute(
            "INSERT INTO model_governance.exports(id,cohort_id,purpose,digest,payload) VALUES(%s,%s,%s,%s,%s)",
            (uid(), identity, purpose_name, digest(payload), Jsonb(payload)),
        )
    current = fetch(c, "SELECT model_governance.current_cohort(%s) AS value", (identity,))["value"]
    if not current:
        raise DomainError("MODEL_COHORT_STALE", "最终登记时来源、身份、解析或用途时点已不成立", 409)
    audit(
        c,
        actor,
        None,
        "model.cohort_frozen",
        {"cohort_id": str(identity), "formal_eligibility": "unavailable"},
        command,
    )
    return cohort_view(c, identity)


def _insert(c, table, row, json_fields):
    # Both identifiers and column names come only from server-owned literal mappings above.
    from psycopg import sql

    statement = sql.SQL("INSERT INTO model_governance.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, row)),
        sql.SQL(",").join(sql.Placeholder() for _ in row),
    )
    c.execute(
        statement, [Jsonb(value) if key in json_fields else value for key, value in row.items()]
    )


def create_training_job(c, *, actor, cohort_id, condition, command):
    view, tag = cohort_view(c, cohort_id)
    match(condition, tag)
    if not view["current"]:
        raise DomainError("MODEL_COHORT_STALE", "冻结训练来源已失效，不能新建执行", 409)
    identity = uid()
    request_digest = digest(
        {
            "kind": "train",
            "cohort_digest": view["digest"],
            "protocol_digest": view["protocol_digest"],
        }
    )
    c.execute(
        "INSERT INTO model_governance.jobs(id,cohort_id,kind,input_digest,created_by) VALUES(%s,%s,'train',%s,%s)",
        (identity, cohort_id, request_digest, actor),
    )
    audit(
        c,
        actor,
        None,
        "model.training_queued",
        {"job_id": str(identity), "external_calls": 0},
        command,
    )
    return job_view(c, identity)


def job_view(c, identity):
    require(c)
    job = fetch(c, "SELECT * FROM model_governance.jobs WHERE id=%s", (identity,))
    current = fetch(c, "SELECT model_governance.current_cohort(%s) AS value", (job["cohort_id"],))[
        "value"
    ]
    result = c.execute(
        "SELECT digest,payload,created_at FROM model_governance.results WHERE job_id=%s",
        (identity,),
    ).fetchone()
    value = clean(job) | {
        "current": current,
        "result": clean(result),
        "formal_eligibility": "unavailable",
    }
    return value, etag("model-job", identity, sha(value))
