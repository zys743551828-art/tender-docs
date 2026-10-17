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
    existing = c.execute(
        "SELECT id FROM model_governance.protocols WHERE digest=%s", (protocol_digest,)
    ).fetchone()
    if existing:
        raise DomainError(
            "TRAINING_PROTOCOL_EXISTS",
            "相同冻结协议已登记，请读取既有协议",
            409,
            {"protocol_id": str(existing["id"])},
        )
    inserted = c.execute(
        "INSERT INTO model_governance.protocols(id,digest,document,origin,created_by) VALUES(%s,%s,%s,'synthetic',%s) ON CONFLICT(digest) DO NOTHING RETURNING id",
        (identity, protocol_digest, Jsonb(document), actor),
    ).fetchone()
    if inserted is None:
        existing = fetch(
            c, "SELECT id FROM model_governance.protocols WHERE digest=%s", (protocol_digest,)
        )
        raise DomainError(
            "TRAINING_PROTOCOL_EXISTS",
            "相同冻结协议已登记，请读取既有协议",
            409,
            {"protocol_id": str(existing["id"])},
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


def evaluation_metadata(c, training_job_id, condition):
    view, tag = job_view(c, training_job_id)
    match(condition, tag)
    if (
        not view["current"]
        or view["state"] != "succeeded"
        or view["kind"] != "train"
        or view["result"] is None
    ):
        raise DomainError(
            "MODEL_TRAINING_RESULT_UNAVAILABLE", "独立评测须固定当前有效且完成的训练结果", 409
        )
    row = fetch(
        c,
        "SELECT e.payload,e.digest,p.digest AS protocol_digest,c.origin FROM model_governance.exports e JOIN model_governance.cohorts c ON c.id=e.cohort_id JOIN model_governance.protocols p ON p.id=c.protocol_id WHERE e.cohort_id=%s AND e.purpose='final_features'",
        (view["cohort_id"],),
    )
    if digest(row["payload"]) != row["digest"]:
        raise DomainError("MODEL_FINAL_FEATURES_CORRUPTED", "最终特征导出摘要不匹配", 409)
    return {"training_job": view, "features": clean(row)}


def final_predictions(metadata):
    from packages.model_governance.statistics import predict, validate_model

    artifact = metadata["training_job"]["result"]["payload"]
    if (
        artifact["schema"] != "frozen-training-result/1"
        or artifact["model_digest"] != digest(artifact["model"])
        or artifact["formal_eligibility"] != "unavailable"
    ):
        raise DomainError("MODEL_ARTIFACT_INVALID", "冻结模型身份或软件资格状态不正确", 409)
    validate_model(artifact["model"])
    models = artifact["baselines"]
    for model in models.values():
        validate_model(model)
    rows = metadata["features"]["payload"]["rows"]
    # Targets are absent from this export; no final labels are read to select/fix a model.
    if any("target" in row or "label" in row for row in rows):
        raise DomainError("MODEL_FINAL_FEATURES_DIRTY", "最终预测输入不得夹带监督标签", 409)
    return {
        "rows": [
            {
                "id": row["id"],
                "status": "succeeded",
                "prediction": predict(artifact["model"], row["features"], method=row["method"]),
            }
            for row in rows
        ],
        "intervals": artifact["intervals"],
        "baseline_predictions": {
            name: {row["id"]: predict(model, row["features"], method=row["method"]) for row in rows}
            for name, model in models.items()
        },
    }


def create_evaluation(c, *, actor, training_job_id, metadata, predictions, condition, command):
    current = evaluation_metadata(c, training_job_id, condition)
    if digest(current) != digest(metadata):
        raise DomainError("MODEL_TRAINING_CHANGED", "最终预测期间的模型或来源已变化", 409)
    artifact = metadata["training_job"]["result"]["payload"]
    identity = uid()
    request_digest = digest(
        {
            "training_job_id": str(training_job_id),
            "model_digest": artifact["model_digest"],
            "predictions_digest": digest(predictions),
            "protocol_digest": metadata["features"]["protocol_digest"],
        }
    )
    c.execute(
        "INSERT INTO model_governance.jobs(id,cohort_id,kind,input_digest,created_by) VALUES(%s,%s,'evaluate',%s,%s)",
        (identity, metadata["training_job"]["cohort_id"], request_digest, actor),
    )
    c.execute(
        "INSERT INTO model_governance.evaluations(job_id,training_job_id,model_digest,predictions_digest,predictions,protocol_digest) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identity,
            training_job_id,
            artifact["model_digest"],
            digest(predictions),
            Jsonb(predictions),
            metadata["features"]["protocol_digest"],
        ),
    )
    audit(
        c,
        actor,
        None,
        "model.final_predictions_frozen",
        {"job_id": str(identity), "model_digest": artifact["model_digest"], "labels_opened": False},
        command,
    )
    return job_view(c, identity)
