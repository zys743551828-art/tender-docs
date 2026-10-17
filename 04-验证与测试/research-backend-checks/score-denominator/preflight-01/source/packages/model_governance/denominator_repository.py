"""API-only denominator references and zero-call derived scoring artifacts."""

import psycopg
from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, clean, etag, fetch, require_read_committed, sha
from packages.model_governance import denominator, scale_repository
from packages.model_governance.repository import match


def require(c):
    require_read_committed(c)
    if not c.execute(
        "SELECT to_regprocedure('score_basis.freeze(uuid,uuid,uuid,uuid,uuid,uuid)') IS NOT NULL AS ready"
    ).fetchone()["ready"]:
        raise DomainError(
            "SCORE_DENOMINATOR_NOT_CONFIGURED", "有据分母引用尚未初始化，原评分保持未知分母", 503
        )


def rpc(c, query, params):
    try:
        return c.execute(query, params)
    except psycopg.Error as error:
        if error.sqlstate in {"55000", "23514", "22023", "P0002", "23505"}:
            raise DomainError(
                "SCORE_DENOMINATOR_UNAVAILABLE", "评分、满分权威或冻结用途已变化，请重新读取", 409
            ) from None
        raise


def context(c, kind, target, sample=None):
    require(c)
    if kind == "score_input":
        current = rpc(
            c, "SELECT score_execution.current_input(%s) AS current", (target,)
        ).fetchone()["current"]
        row = fetch(
            c,
            "SELECT i.id,i.bid_id,i.historical_input_id,i.method_digest,s.digest AS input_digest FROM score_execution.inputs i JOIN score_execution.input_seals s ON s.input_id=i.id WHERE i.id=%s",
            (target,),
        )
        historical = row["historical_input_id"]
    elif kind == "historical_input":
        current = rpc(
            c, "SELECT historical_input.current_input(%s) AS current", (target,)
        ).fetchone()["current"]
        row = fetch(
            c,
            "SELECT id,bid_id,method_digest,manifest_digest AS input_digest FROM historical_backtest.inputs WHERE id=%s",
            (target,),
        )
        historical = target
    elif kind == "stagea_member" and sample:
        current = rpc(
            c, "SELECT model_governance.current_cohort(%s) AS current", (target,)
        ).fetchone()["current"]
        row = fetch(
            c,
            "SELECT m.bid_id,c.digest AS input_digest,e.method_digest FROM model_governance.members m JOIN model_governance.cohorts c ON c.id=m.cohort_id JOIN research_runs run ON run.id=c.research_run_id JOIN experiments e ON e.id=run.experiment_id WHERE m.cohort_id=%s AND m.sample_id=%s AND m.purpose<>'excluded'",
            (target, sample),
        )
        historical = None
    else:
        raise DomainError("SCORE_DENOMINATOR_SCOPE_INVALID", "请选择确定评分或历史研究对象", 422)
    if not current:
        raise DomainError(
            "SCORE_DENOMINATOR_UNAVAILABLE", "原完整输入或独立训练集合当前资格已失效", 409
        )
    if historical:
        submission = fetch(
            c, "SELECT submission_id FROM historical_backtest.inputs WHERE id=%s", (historical,)
        )["submission_id"]
        purpose, scope = "historical_backtest", submission
    else:
        purpose, scope = "current_prebid", row["bid_id"]
    selected = c.execute(
        "SELECT version_id FROM technical_scale.heads WHERE bid_id=%s AND purpose=%s AND context_id=%s",
        (row["bid_id"], purpose, scope),
    ).fetchone()
    scale = scale_repository.view(c, selected["version_id"])[0] if selected else None
    value = clean(
        {
            "target_kind": kind,
            "target_id": target,
            "sample_id": sample,
            "bid_id": row["bid_id"],
            "input_digest": row["input_digest"],
            "method_digest": row["method_digest"],
            "scale_authority": scale,
            "formal_eligibility": "unavailable",
            "paid_execution_enabled": False,
        }
    )
    return value, etag("score-denominator-context", target, sha(value))


def freeze(c, *, kind, target, sample, actor, condition):
    value, tag = context(c, kind, target, sample)
    match(condition, tag)
    scale = value["scale_authority"]
    args = {
        "score_input": (target, None, None, None),
        "historical_input": (None, target, None, None),
        "stagea_member": (None, None, target, sample),
    }[kind]
    identity = rpc(
        c,
        "SELECT score_basis.freeze(%s,%s,%s,%s,%s,%s) AS id",
        (*args, scale["id"] if scale else None, actor),
    ).fetchone()["id"]
    return view(c, identity)


def binding(c, identity):
    require(c)
    row = fetch(c, "SELECT * FROM score_basis.bindings WHERE id=%s", (identity,))
    row["current"] = rpc(
        c, "SELECT score_basis.current_binding(%s) AS current", (identity,)
    ).fetchone()["current"]
    return clean(row)


def view(c, identity):
    row = binding(c, identity)
    value = {
        key: row[key]
        for key in (
            "id",
            "score_input_id",
            "historical_input_id",
            "cohort_id",
            "sample_id",
            "scale_version_id",
            "scale_decision_id",
            "scale_head_revision",
            "scope_digest",
            "recorded_maximum",
            "scale_description",
            "recorded_status",
            "reason",
            "created_at",
            "current",
        )
    }
    value.update(
        technical_maximum=row["recorded_maximum"] if row["current"] else None,
        formal_eligibility="unavailable",
        paid_execution_enabled=False,
    )
    return value, etag("score-denominator", identity, sha(value))


def snapshot(c, binding_id, job_id):
    row = binding(c, binding_id)
    if not row["current"]:
        raise DomainError(
            "SCORE_DENOMINATOR_UNAVAILABLE", "原分母权威当前已失效，历史快照仍可阅读", 409
        )
    job = fetch(
        c,
        "SELECT j.input_id,i.historical_input_id,i.method_digest,r.document,r.digest FROM score_execution.jobs j JOIN score_execution.inputs i ON i.id=j.input_id JOIN score_execution.results r ON r.job_id=j.id WHERE j.id=%s AND j.state='succeeded'",
        (job_id,),
    )
    if row["score_input_id"] != str(job["input_id"]) and (
        not row["historical_input_id"]
        or row["historical_input_id"] != str(job["historical_input_id"])
    ):
        raise DomainError(
            "SCORE_DENOMINATOR_SCOPE_INVALID", "分母引用与实际三判断结果不是同一完整输入", 409
        )
    if job["historical_input_id"]:
        packages = c.execute(
            "SELECT p.raw_content::jsonb AS package FROM historical_backtest.chapters c JOIN historical_input.chapter_payloads p ON p.chapter_id=c.id WHERE c.input_id=%s ORDER BY c.rule_id",
            (job["historical_input_id"],),
        ).fetchall()
    else:
        packages = c.execute(
            "SELECT c.raw_content::jsonb AS package FROM score_execution.current_chapters c JOIN chapter_packages p ON p.id=c.chapter_package_id WHERE c.input_id=%s ORDER BY p.rule_id",
            (job["input_id"],),
        ).fetchall()
    return {
        "binding": row,
        "packages": [p["package"] for p in packages],
        "result": job["document"],
        "source_result_digest": job["digest"],
        "method_digest": job["method_digest"],
    }


def compute(frozen):
    return denominator.derive(**frozen)


def publish(c, *, binding_id, job_id, payload, actor):
    require(c)
    actual_digest = c.execute(
        "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS digest",
        (Jsonb(payload),),
    ).fetchone()["digest"]
    identity = rpc(
        c,
        "SELECT score_basis.publish_result(%s,%s,%s,%s,%s) AS id",
        (binding_id, job_id, Jsonb(payload), actual_digest, actor),
    ).fetchone()["id"]
    return result_view(c, identity)


def result_view(c, identity):
    require(c)
    row = fetch(
        c,
        "SELECT id,binding_id,job_id,document,digest,created_at FROM score_basis.results WHERE id=%s",
        (identity,),
    )
    basis = binding(c, row["binding_id"])
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (basis["scope"]["bid_id"],))[
        "settings"
    ]
    recorded = clean(row.pop("document"))
    hidden = not settings.get("show_prediction", True)
    if hidden:
        recorded.pop("features", None)
        recorded.pop("prediction", None)
    value = clean(row) | {
        "recorded_document": recorded,
        "historical_snapshot": True,
        "prediction_hidden": hidden,
        "current": basis["current"],
        "technical_maximum": basis["recorded_maximum"] if basis["current"] else None,
        "formal_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }
    return value, etag("sourced-score-coverage", identity, sha(value))
