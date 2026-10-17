"""API orchestration for dedicated score inputs/jobs and same-result shadow snapshots."""

import json
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.review_data import check_frozen_sources
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
from packages.model_governance.repository import match
from packages.model_governance.scoring_producer import conservative_fee, request_for
from packages.research import chapters
from packages.research.batches import price_config
from packages.research.profile import digest


def configured(c):
    return bool(
        c.execute(
            "SELECT to_regprocedure('score_execution.seal_input(uuid,uuid)') IS NOT NULL AND to_regprocedure('score_execution.start_job(uuid,numeric,uuid,uuid)') IS NOT NULL AS ready"
        ).fetchone()["ready"]
    )


def require(c):
    if not configured(c):
        raise DomainError(
            "SCORE_AUTHORITY_NOT_CONFIGURED",
            "专用评分输入与账本尚未初始化，不能借普通核对或研究任务代发",
            503,
        )


def rpc(c, query, values):
    try:
        return c.execute(query, values)
    except psycopg.Error as error:
        if error.sqlstate == "40001":
            raise DomainError(
                "SCORE_REVISION_CONFLICT", "评分有效上下文、代次或影子路由已变化", 412
            ) from None
        if error.sqlstate in {"55000", "23514", "23505", "22023", "P0002"}:
            raise DomainError(
                "SCORE_AUTHORITY_UNAVAILABLE",
                "完整范围、依赖、账本、预算或模型资格不满足受控条件",
                409,
            ) from None
        raise


def lock(c, bid):
    require_read_committed(c)
    require(c)
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    c.execute("SELECT design.lock_worker_bid(%s)", (bid,))


def context(c, bid):
    lock(c, bid)
    row = fetch(
        c,
        "SELECT p.bid_id,p.current_body_id,p.current_material_id,p.current_rule_set_id,p.current_review_id,p.phase,p.archived_at FROM bid_profiles p WHERE bid_id=%s",
        (bid,),
    )
    prices = price_config()
    value = clean(row) | {
        "identity": identity_snapshot(c, bid),
        "judgment_method_digest": digest(),
        "price_config_digest": sha(prices),
        "full_chapters_required": True,
        "valid_replicates_required": 3,
        "formal_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }
    return value, etag("score-input-context", bid, sha(value))


def root_metadata(c, bid, review):
    lock(c, bid)
    row = fetch(
        c,
        "SELECT b.*,i.manifest AS review_manifest,i.input_digest AS review_input_digest,i.body_version_id,i.material_snapshot_id,i.as_of,i.frozen_at,r.id AS review_id FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN blobs b ON b.id=i.evidence_manifest_blob_id JOIN bid_profiles p ON p.bid_id=r.bid_id AND p.current_review_id=r.id WHERE r.id=%s AND r.bid_id=%s",
        (review, bid),
    )
    if (
        row["byte_size"] > 8388608
        or row["review_manifest"].get("evidence_manifest_digest") != row["digest"]
    ):
        raise DomainError(
            "SCORE_REVIEW_ROOT_INVALID", "本轮缺少实际冻结证据根，不能从当前身份推造旧评分输入", 409
        )
    return row


def read_root(metadata):
    raw = storage.get(metadata["object_key"])
    if len(raw) != metadata["byte_size"] or sha(raw) != metadata["digest"]:
        raise DomainError("SCORE_REVIEW_ROOT_INVALID", "冻结评审根实际字节不匹配", 409)
    try:
        content = raw.decode("utf-8")
        root = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise DomainError("SCORE_REVIEW_ROOT_INVALID", "冻结根不是有效JSON", 409) from None
    if (
        not isinstance(root, dict)
        or not root.get("identity")
        or not isinstance(root.get("documents"), list)
        or not isinstance(root.get("rules"), list)
        or not isinstance(root.get("rule_set"), dict)
        or root["rule_set"].get("coverage") != "complete"
        or root.get("confirmed_facts")
        or any(
            doc.get("role") not in {"body", "tender", "supplement"}
            for doc in root.get("documents", [])
        )
    ):
        raise DomainError(
            "SCORE_SCOPE_UNSUPPORTED",
            "首个完整判断方法不忽略必要事前事实、图纸或BOQ；当前完整评分范围尚不支持",
            409,
        )
    return root, content


def prepare_current(c, *, bid, review, root, root_content, metadata, condition):
    _, tag = context(c, bid)
    match(condition, tag)
    assert_work_writable(c, bid)
    current_metadata = root_metadata(c, bid, review)
    if sha(clean(current_metadata)) != sha(clean(metadata)):
        raise DomainError("SCORE_REVIEW_CHANGED", "读取证据根期间评审输入已变化", 409)
    if sha(root_content.encode()) != metadata["digest"] or json.loads(root_content) != root:
        raise DomainError("SCORE_REVIEW_ROOT_INVALID", "评分根必须是实际冻结对象原字节", 409)
    identity = identity_snapshot(c, bid)
    if identity["binding_status"] != "confirmed" or root["identity"] != identity:
        raise DomainError(
            "SCORE_IDENTITY_CHANGED",
            "当前身份未确认或不等于评审根实际冻结身份，不能重建过去身份",
            409,
        )
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    check_frozen_sources(c, metadata["review_manifest"], root, lock=True)
    for document in root["documents"]:
        parsed = historical_input._parse(c, bid, UUID(document["document_version_id"]))
        if str(parsed["parse_id"]) != document["parse_id"] or any(
            head["known_at"] > metadata["as_of"]
            or head["valid_from"] > metadata["as_of"]
            or (head["valid_until"] is not None and head["valid_until"] <= metadata["as_of"])
            for head in parsed["heads"]
        ):
            raise DomainError(
                "SCORE_REVIEW_CHANGED", "评审实际冻结解析、完整页范围或事前来源边界已变化", 409
            )
    built = chapters.build(c, review_id=review)
    require_frozen_scoring_scope(root, built)
    for candidate in built["candidates"]:
        request_for(candidate["package"])
    return {
        "id": uid(),
        "bid_id": bid,
        "review_id": review,
        "historical_input_id": None,
        "identity_snapshot": identity,
        "origin": "uncertain",
        "price_config": price_config(),
        "current": built,
        "metadata": metadata,
        "root": root,
        "review_root_content": root_content,
        "review_root_digest": metadata["digest"],
    }


def require_frozen_scoring_scope(root, built):
    frozen = [str(rule["id"]) for rule in root["rules"] if rule["kind"] == "scoring"]
    necessary = [str(item["rule_id"]) for item in built["candidates"]]
    if not frozen or len(set(frozen)) != len(frozen) or sorted(frozen) != sorted(necessary):
        raise DomainError(
            "SCORE_REVIEW_CHANGED", "当前必要评分项不等于评审根实际冻结范围，请重新核对", 409
        )


def prepare_historical(c, *, bid, identity, condition):
    _, tag = context(c, bid)
    match(condition, tag)
    value, _ = historical_input.view(c, identity)
    if value["bid_id"] != str(bid) or not value["current"]:
        raise DomainError("SCORE_HISTORICAL_INPUT_STALE", "历史输入不属于本项目或已失效", 409)
    return {
        "id": uid(),
        "bid_id": bid,
        "review_id": None,
        "historical_input_id": identity,
        "identity_snapshot": None,
        "origin": value["origin"],
        "price_config": price_config(),
        "historical_digest": value["authority_digest"],
        "review_root_content": None,
        "review_root_digest": None,
    }


def stage(prepared):
    conservative_fee(prepared["price_config"])
    return (
        prepared | {"current": chapters.stage(prepared["current"])}
        if prepared["review_id"] is not None
        else prepared
    )


def publish(c, *, prepared, actor, condition, reason, command):
    bid = prepared["bid_id"]
    if prepared["review_id"] is not None:
        fresh = prepare_current(
            c,
            bid=bid,
            review=prepared["review_id"],
            root=prepared["root"],
            root_content=prepared["review_root_content"],
            metadata=prepared["metadata"],
            condition=condition,
        )

        def projection(value):
            return [
                (str(x["rule_id"]), x["package"]["digest"], x["authority"]["authority_digest"])
                for x in value["current"]["candidates"]
            ]

        if (
            projection(fresh) != projection(prepared)
            or fresh["price_config"] != prepared["price_config"]
        ):
            raise DomainError(
                "SCORE_INPUT_CHANGED", "对象准备期间完整章节或模型费用快照已变化", 409
            )
        output = chapters.publish(c, actor=actor, prepared=prepared["current"], command=command)
    else:
        fresh = prepare_historical(
            c, bid=bid, identity=prepared["historical_input_id"], condition=condition
        )
        if (
            fresh["historical_digest"] != prepared["historical_digest"]
            or fresh["price_config"] != prepared["price_config"]
        ):
            raise DomainError("SCORE_INPUT_CHANGED", "历史authority或费用快照已变化", 409)
    c.execute(
        "INSERT INTO score_execution.inputs(id,bid_id,review_id,historical_input_id,identity_snapshot,origin,method_digest,price_config,actor_id,review_root_content,review_root_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            prepared["id"],
            bid,
            prepared["review_id"],
            prepared["historical_input_id"],
            Jsonb(prepared["identity_snapshot"])
            if prepared["identity_snapshot"] is not None
            else None,
            prepared["origin"],
            digest(),
            Jsonb(prepared["price_config"]),
            actor,
            prepared["review_root_content"],
            prepared["review_root_digest"],
        ),
    )
    if prepared["review_id"] is not None:
        by_rule = {str(x["rule_id"]): x for x in prepared["current"]["candidates"]}
        for package in output["packages"]:
            value = by_rule[package["rule_id"]]
            raw = json.dumps(
                clean(value["package"]), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            c.execute(
                "INSERT INTO score_execution.current_chapters VALUES(%s,%s,%s,%s)",
                (prepared["id"], package["id"], raw, sha(raw.encode())),
            )
    rpc(c, "SELECT score_execution.seal_input(%s,%s)", (prepared["id"], actor))
    audit(
        c,
        actor,
        bid,
        "score.input_sealed",
        {"input_id": prepared["id"], "formal_eligibility": "unavailable", "reason": reason},
        command,
    )
    return input_view(c, prepared["id"])


def input_view(c, identity):
    require(c)
    row = fetch(
        c,
        "SELECT i.*,s.digest AS input_digest,s.execution_key,s.rule_count FROM score_execution.inputs i JOIN score_execution.input_seals s ON s.input_id=i.id WHERE i.id=%s",
        (identity,),
    )
    lock(c, row["bid_id"])
    current = rpc(c, "SELECT score_execution.current_input(%s) AS value", (identity,)).fetchone()[
        "value"
    ]
    value = {
        key: clean(row[key])
        for key in (
            "id",
            "bid_id",
            "review_id",
            "historical_input_id",
            "origin",
            "method_digest",
            "technical_maximum",
            "maximum_verified",
            "actor_id",
            "created_at",
            "input_digest",
            "execution_key",
            "rule_count",
        )
    }
    value.update(
        {
            "current": current,
            "call_count": row["rule_count"] * 3,
            "conservative_budget": str(
                conservative_fee(row["price_config"]) * row["rule_count"] * 3
            ),
            "currency": row["price_config"]["currency"],
            "fee_basis": "conservative_peak_cache_miss_upper_bound",
            "formal_eligibility": "unavailable",
            "paid_execution_enabled": False,
        }
    )
    return value, etag("score-input", identity, sha(value))


def job_view(c, identity):
    require(c)
    row = fetch(
        c,
        "SELECT j.*,i.bid_id,i.review_id,i.historical_input_id FROM score_execution.jobs j JOIN score_execution.inputs i ON i.id=j.input_id WHERE j.id=%s",
        (identity,),
    )
    lock(c, row["bid_id"])
    # Reread mutable job state AFTER the leading scope; source currentness is separate
    # from truthful billing and immutable success. Archived results stay readable.
    row = fetch(
        c,
        "SELECT j.*,i.bid_id,i.review_id,i.historical_input_id FROM score_execution.jobs j JOIN score_execution.inputs i ON i.id=j.input_id WHERE j.id=%s",
        (identity,),
    )
    current = rpc(
        c, "SELECT score_execution.current_input(%s) AS value", (row["input_id"],)
    ).fetchone()["value"]
    calls = c.execute(
        "SELECT call.state,count(*) AS count FROM score_execution.call_bindings bind JOIN score_execution.calls call ON call.id=bind.call_id WHERE bind.job_id=%s GROUP BY call.state ORDER BY call.state",
        (identity,),
    ).fetchall()
    reused = c.execute(
        "SELECT count(*) AS count FROM score_execution.call_bindings WHERE job_id=%s AND reused",
        (identity,),
    ).fetchone()["count"]
    result = c.execute(
        "SELECT digest,created_at FROM score_execution.results WHERE job_id=%s", (identity,)
    ).fetchone()
    value = clean(
        {key: value for key, value in row.items() if key not in {"owner_token", "lease_until"}}
    ) | {
        "source_current": current,
        "calls": clean(calls),
        "reused_calls": reused,
        "result": clean(result),
        "fee_basis": "usage_price_estimate_not_invoice",
        "formal_eligibility": "unavailable",
        "paid_execution_enabled": False,
    }
    return value, etag("score-job", identity, sha(value))


def start(c, *, identity, actor, budget, reason, condition, command):
    value, tag = input_view(c, identity)
    match(condition, tag)
    if value["review_id"] is not None:
        assert_work_writable(c, value["bid_id"])
    if not value["current"] or Decimal(budget) < Decimal(value["conservative_budget"]):
        raise DomainError(
            "SCORE_BUDGET_INSUFFICIENT", "完整三判断范围失效或预算小于冻结的全部请求保守上界", 409
        )
    job = rpc(
        c,
        "SELECT score_execution.start_job(%s,%s,%s,%s) AS value",
        (identity, budget, actor, command),
    ).fetchone()["value"]
    audit(
        c,
        actor,
        value["bid_id"],
        "score.job_created",
        {"job_id": job, "budget": budget, "reason": reason, "dispatch_configured": False},
        command,
    )
    return job_view(c, job)


def result_view(c, identity):
    job, _ = job_view(c, identity)
    row = fetch(
        c,
        "SELECT document,digest,created_at FROM score_execution.results WHERE job_id=%s",
        (identity,),
    )
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (job["bid_id"],))["settings"]
    document = clean(row["document"])
    value = {
        "job_id": str(identity),
        "input_id": job["input_id"],
        "result_digest": row["digest"],
        "created_at": clean(row["created_at"]),
        "source_current": job["source_current"],
        "items": document["items"],
        "formal_eligibility": "unavailable",
        "prediction_hidden": not settings.get("show_prediction", True),
    }
    if not value["prediction_hidden"]:
        value["features"] = document["features"]
        value["prediction"] = document["prediction"]
    return value, etag("score-result", identity, sha(value))


def shadow_context(c, job_id, model_id):
    from packages.model_governance import registry

    require_read_committed(c)
    require(c)
    # Read immutable input/model headers to derive the complete union scope. No graph or
    # single-bid lock precedes this union; current_cohort may cover other projects.
    job = fetch(
        c,
        "SELECT j.input_id,i.bid_id FROM score_execution.jobs j JOIN score_execution.inputs i ON i.id=j.input_id WHERE j.id=%s",
        (job_id,),
    )
    model = registry.blob(c, model_id)
    c.execute("SELECT pg_advisory_xact_lock(91143,1)")
    route_header = fetch(
        c,
        "SELECT encode(sha256(convert_to((document->'scope')::text,'UTF8')),'hex') AS route_key FROM model_release.registrations WHERE model_id=%s",
        (model_id,),
    )
    route_models = c.execute(
        "SELECT model_id,rollback_model_id FROM model_release.routes WHERE route_key=%s AND lane='shadow'",
        (route_header["route_key"],),
    ).fetchone()
    targets = [model_id, *(route_models.values() if route_models else [])]
    bids = c.execute(
        "SELECT DISTINCT bid_id FROM (SELECT %s::uuid AS bid_id UNION SELECT member.bid_id FROM model_governance.members member JOIN model_release.registrations registration ON registration.cohort_id=member.cohort_id WHERE registration.model_id=ANY(%s::uuid[]) UNION SELECT sample.bid_id FROM samples sample WHERE sample.source_group_id IN (SELECT member.group_id FROM model_governance.members member JOIN model_release.registrations registration ON registration.cohort_id=member.cohort_id WHERE registration.model_id=ANY(%s::uuid[]))) scope ORDER BY bid_id",
        (job["bid_id"], targets, targets),
    ).fetchall()
    for bid in bids:
        c.execute("SELECT design.lock_worker_bid(%s)", (bid["bid_id"],))
    score, _ = input_view(c, job["input_id"])
    registered, _ = registry.view(c, model_id)
    route, _ = registry.route(c, registered["route_key"], "shadow")
    result = fetch(
        c,
        "SELECT digest,input_digest,document FROM score_execution.results WHERE job_id=%s",
        (job_id,),
    )
    value = {
        "job_id": str(job_id),
        "model_id": str(model_id),
        "route_key": registered["route_key"],
        "route_revision": route["revision"],
        "input_digest": result["input_digest"],
        "score_result_digest": result["digest"],
        "model_artifact_digest": model["digest"],
        "method_digest": score["method_digest"],
        "source_current": score["current"],
        "model_current": registered["current"],
        "route_current": route["model_id"] == str(model_id) and route["current"],
        "formal_eligibility": "unavailable",
        "additional_model_calls": 0,
        "numeric_prediction_available": False,
    }
    return value, etag("score-shadow-context", f"{job_id}:{model_id}", sha(value))


def persist_shadow(c, *, job_id, model_id, actor, condition, reason, command, artifact_digest):
    state, tag = shadow_context(c, job_id, model_id)
    match(condition, tag)
    if (
        not all(state[key] for key in ("source_current", "model_current", "route_current"))
        or state["model_artifact_digest"] != artifact_digest
    ):
        raise DomainError(
            "SCORE_SHADOW_STALE", "同输入、模型实际字节或影子路由不再满足当前资格", 409
        )
    document = {
        "schema": "persisted-score-shadow/1",
        **{
            key: state[key]
            for key in (
                "job_id",
                "model_id",
                "input_digest",
                "score_result_digest",
                "model_artifact_digest",
                "method_digest",
            )
        },
        "prediction": {
            "mode": "unavailable",
            "value": None,
            "intervals": None,
            "reasons": ["verified_prebid_technical_maximum_unavailable"],
        },
        "formal_eligibility": "unavailable",
    }
    identity = rpc(
        c,
        "SELECT score_execution.persist_shadow(%s,%s,%s,%s,%s,%s) AS value",
        (job_id, model_id, state["route_key"], state["route_revision"], Jsonb(document), actor),
    ).fetchone()["value"]
    job = fetch(
        c,
        "SELECT i.bid_id FROM score_execution.jobs j JOIN score_execution.inputs i ON i.id=j.input_id WHERE j.id=%s",
        (job_id,),
    )
    audit(
        c,
        actor,
        job["bid_id"],
        "score.shadow_saved",
        {
            "shadow_id": identity,
            "job_id": job_id,
            "reason": reason,
            "additional_calls": 0,
            "formal_eligibility": "unavailable",
        },
        command,
    )
    return shadow_view(c, identity)


def shadow_view(c, identity):
    require(c)
    require_read_committed(c)
    row = fetch(
        c,
        "SELECT s.*,i.bid_id FROM score_execution.shadows s JOIN score_execution.jobs j ON j.id=s.job_id JOIN score_execution.inputs i ON i.id=j.input_id WHERE s.id=%s",
        (identity,),
    )
    settings = fetch(c, "SELECT settings FROM bids WHERE id=%s", (row["bid_id"],))["settings"]
    hidden = not settings.get("show_prediction", True)
    value = clean(
        {
            key: row[key]
            for key in (
                "id",
                "job_id",
                "model_id",
                "route_key",
                "route_revision",
                "digest",
                "created_by",
                "created_at",
                "bid_id",
            )
        }
    ) | {
        "historical_snapshot": True,
        "prediction_hidden": hidden,
        "additional_model_calls": 0,
        "formal_eligibility": "unavailable",
    }
    if not hidden:
        value["comparison"] = row["document"]
    return value, etag("persisted-score-shadow", identity, sha(value))
