"""有据报价公式、不可变模拟与显式保存；远端I/O由路由分阶段调用。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.runtime import DomainError, audit, clean, etag, fetch, now, sha, uid
from packages.pricing import calculator, sources, terms
from packages.pricing.models import SUPPORTED_FIELDS, required_evidence_fields

FORMULA_MEDIA = "application/vnd.tender.pricing-formula+json"
MAX_FORMULA_BYTES = 4 * 1024 * 1024


def _work(c, bid):
    from packages.domain.runtime import require_read_committed
    from packages.lifecycle.service import assert_work_writable

    require_read_committed(c)
    assert_work_writable(c, bid)


def calculator_identity():
    return {
        "name": "bounded-decimal-price-calculator",
        "version": 1,
        "implementation_sha256": sha(Path(calculator.__file__).read_bytes()),
    }


def exact_match(header, expected):
    if not header:
        raise DomainError("PRECONDITION_REQUIRED", "请读取当前资源后再操作", 428)
    if header != expected:
        raise DomainError(
            "REVISION_CONFLICT",
            "报价依据或情景已更新，请核对最新内容；输入尚未保存",
            412,
            {"current_etag": expected},
        )


def validate_terms(value):
    if not value["title"].strip() or not value["reason"].strip():
        raise DomainError("PRICING_CONFIRMATION_REQUIRED", "请填写公式标题与原文对应解释", 422)
    try:
        calculator.validate_formula(value["formula"])
    except ValueError as error:
        raise DomainError(
            "PRICING_FORMULA_UNSUPPORTED", str(error), 422, {"field": "formula"}
        ) from None
    fields = set()
    for quote in value["evidence_quotes"]:
        if (
            len(set(quote["supported_fields"])) != len(quote["supported_fields"])
            or not set(quote["supported_fields"]) <= SUPPORTED_FIELDS
        ):
            raise DomainError(
                "PRICING_EVIDENCE_FIELDS_INVALID", "请选择本原文实际支持的受限计算字段", 422
            )
        fields.update(quote["supported_fields"])
    required = required_evidence_fields(value["formula"]["method"])
    if not required <= fields:
        raise DomainError(
            "PRICING_FORMULA_INCOMPLETE",
            "公式计分、有效集合、量尺及取整均须有完整原文依据",
            422,
            {"missing_fields": sorted(required - fields)},
        )
    text = "\n".join(quote["quote"] for quote in value["evidence_quotes"])
    # Conservative refusal, not general natural-language extraction or a claim of semantic proof.
    if re.search(
        r"随机|抽签|抽取系数|去极值|去掉.{0,8}(最高|最低)|剔除.{0,8}(最高|最低)|分段|阶梯|基准价.{0,15}(四舍五入|取整|保留[一二三四五六0-9]位)",
        text,
    ):
        raise DomainError(
            "PRICING_FORMULA_UNSUPPORTED",
            "原文含当前未支持的随机、去极值、分段或基准价取整条件，不能近似计分",
            422,
        )
    marker = r"最低" if value["formula"]["method"] == "lowest_valid_ratio" else r"算术平均|算术均值"
    if not re.search(marker, text):
        raise DomainError(
            "PRICING_FORMULA_UNSUPPORTED", "原文未明确支持选择的基准价方法，请核对完整计分条款", 422
        )
    numbers = {
        calculator.number(token).normalize()
        for token in re.findall(r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])", text)
        if calculator.NUMBER.fullmatch(token)
    }
    for field in (
        "maximum",
        "minimum_price",
        "maximum_price",
        "above_coefficient",
        "below_coefficient",
    ):
        number = value["formula"][field]
        if (
            number is not None
            and calculator.number(number) != 0
            and calculator.number(number).normalize() not in numbers
        ):
            raise DomainError(
                "PRICING_PARAMETER_WITHOUT_EVIDENCE",
                "计算参数未在完整出处中找到对应数值，请核对而非猜测",
                422,
                {"field": field},
            )
    if str(value["formula"]["minimum_valid_bids"]) not in re.findall(r"[0-9]+", text):
        raise DomainError(
            "PRICING_PARAMETER_WITHOUT_EVIDENCE",
            "有效报价数量要求须有明确原文依据（包含本方）",
            422,
            {"field": "minimum_valid_bids"},
        )
    terms.validate_quotes(value)


def prepare_formula(c, *, bid, actor, value, condition):
    _work(c, bid)
    sources.enabled_bid(c, bid)
    validate_terms(value)
    capsule = sources.exact_sources(c, bid=bid, quotes=value["evidence_quotes"])
    sources.validate_measures(c, bid=bid, value=value)
    context, tag = sources.context(c, bid)
    exact_match(condition, tag)
    manifest = {
        "schema": "pricing-formula/1",
        "formula_version_id": str(uid()),
        "bid_id": str(bid),
        "material_snapshot_id": context["material_snapshot_id"],
        "material_revision": context["material_revision"],
        "applicability_scope": capsule["scope"],
        **value,
        "confirmed_by": str(actor),
        "confirmed_at": now().isoformat(),
        "calculator": calculator_identity(),
        "source_authority": capsule,
    }
    return manifest


def stage_formula(manifest):
    identity = UUID(manifest["formula_version_id"])
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_FORMULA_BYTES:
        raise DomainError(
            "PRICING_FORMULA_TOO_LARGE", "公式完整依据超过当前受控大小，请选择最小完整条款集合", 422
        )
    key = f"pricing/formulas/{identity}.json"
    storage.put(key, raw, FORMULA_MEDIA)
    return {
        "id": identity,
        "digest": sha(raw),
        "object_key": key,
        "byte_size": len(raw),
        "media_type": FORMULA_MEDIA,
    }


def publish_formula(c, *, bid, actor, manifest, staged, condition, command):
    _work(c, bid)
    sources.enabled_bid(c, bid, lock=True)
    capsule = sources.exact_sources(c, bid=bid, quotes=manifest["evidence_quotes"])
    sources.validate_measures(c, bid=bid, value=manifest)
    _, current_tag = sources.context(c, bid)
    exact_match(condition, current_tag)
    if sha(capsule) != sha(manifest["source_authority"]):
        raise DomainError(
            "PRICING_SOURCE_CHANGED", "登记期间公式依据已变化，请核对原页后重新登记", 409
        )
    identity = UUID(manifest["formula_version_id"])
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s)",
        tuple(staged[k] for k in ("id", "digest", "object_key", "byte_size", "media_type")),
    )
    first_doc = manifest["evidence_quotes"][0]["document_version_id"]
    c.execute(
        "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'feature','prebid_content',%s,%s)",
        (identity, first_doc, manifest["confirmed_at"], staged["digest"]),
    )
    c.execute(
        "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'published',%s)",
        (identity, manifest["confirmed_at"]),
    )
    for source in sorted({q["source_id"] for q in manifest["evidence_quotes"]}):
        c.execute(
            "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
            (identity, source),
        )
    for quote in manifest["evidence_quotes"]:
        c.execute(
            "INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id) VALUES(%s,%s,%s,%s,'compliance',%s,%s)",
            (
                uid(),
                bid,
                quote["source_id"],
                quote["source_revision"],
                quote["evidence_id"],
                identity,
            ),
        )
    summary = {
        "formula_version_id": str(identity),
        "formula_digest": staged["digest"],
        "title": manifest["title"],
        "method": manifest["formula"]["method"],
        "maximum": manifest["formula"]["maximum"],
        "amount_unit": manifest["amount_unit"],
        "currency": manifest["currency"],
        "rounding_policy": manifest["rounding_policy"],
        "confirmed_at": manifest["confirmed_at"],
        "confirmed_by": str(actor),
    }
    c.execute(
        "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'pricing_formula',%s,1,'confirmed',%s,%s,%s)",
        (
            uid(),
            bid,
            identity,
            json.dumps({"reason": manifest["reason"], "summary": summary}, ensure_ascii=False),
            [q["evidence_id"] for q in manifest["evidence_quotes"]],
            actor,
        ),
    )
    audit(
        c,
        actor,
        bid,
        "pricing.formula_registered",
        {"formula_version_id": identity, "formula_digest": staged["digest"]},
        command,
    )
    return formula_view(c, bid=bid, manifest=manifest, digest=staged["digest"])


def formula_blob(c, bid, identity):
    sources.enabled_bid(c, bid)
    return fetch(
        c,
        "SELECT b.* FROM blobs b JOIN decisions d ON d.object_id=b.id AND d.object_kind='pricing_formula' AND d.decision='confirmed' JOIN retrieval.source_nodes n ON n.id=b.id AND n.kind='feature' WHERE b.id=%s AND d.bid_id=%s AND b.media_type=%s",
        (identity, bid, FORMULA_MEDIA),
    )


def read_formula(blob, *, bid):
    if blob["byte_size"] > MAX_FORMULA_BYTES:
        raise DomainError("PRICING_FORMULA_CORRUPTED", "报价公式超过受控大小", 409)
    raw = storage.get(blob["object_key"])
    if len(raw) != blob["byte_size"] or sha(raw) != blob["digest"]:
        raise DomainError("PRICING_FORMULA_CORRUPTED", "公式实际字节与不可变摘要不符", 409)
    try:
        manifest = json.loads(raw)
        if (
            manifest["schema"] != "pricing-formula/1"
            or manifest["bid_id"] != str(bid)
            or manifest["formula_version_id"] != str(blob["id"])
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise DomainError("PRICING_FORMULA_CORRUPTED", "公式用途与项目身份不匹配", 409) from None
    return manifest


def applicability(c, *, bid, manifest, digest):
    sources.enabled_bid(c, bid)
    if (
        c.execute("SELECT current_setting('transaction_isolation') AS value").fetchone()["value"]
        != "read committed"
    ):
        raise DomainError("PRICING_FRESH_TRANSACTION_REQUIRED", "报价来源核验需要新鲜的短事务", 409)
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    node = fetch(
        c,
        "SELECT n.digest,n.kind,n.policy_class,n.known_at,h.* FROM retrieval.source_nodes n LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE n.id=%s",
        (manifest["formula_version_id"],),
    )
    reasons = []
    parents = {
        str(row["parent_id"])
        for row in c.execute(
            "SELECT parent_id FROM retrieval.lineage_edges WHERE child_id=%s",
            (manifest["formula_version_id"],),
        ).fetchall()
    }
    if parents != {q["source_id"] for q in manifest["evidence_quotes"]}:
        reasons.append("公式来源血缘与已确认版本不符")
    if (
        node["digest"] != digest
        or node["kind"] != "feature"
        or node["policy_class"] != "prebid_content"
        or node["status"] != "published"
        or node["known_at"] > now()
        or node["valid_from"] is None
        or node["valid_from"] > now()
        or (node["valid_until"] is not None and node["valid_until"] <= now())
    ):
        reasons.append("公式版本已撤回、过期或用途不符")
    if manifest["calculator"] != calculator_identity():
        reasons.append("计算方法实现已变化，历史记录仍可还原，新试算须使用核对后的版本")
    try:
        validate_terms(manifest)
        current = sources.exact_sources(c, bid=bid, quotes=manifest["evidence_quotes"])
        sources.validate_measures(c, bid=bid, value=manifest)
        if sha(current) != sha(manifest["source_authority"]):
            reasons.append("公式依据或生效招标补遗已更新，请核对后重新试算")
    except DomainError as error:
        if error.code == "PRICING_DISABLED":
            raise
        reasons.append(error.message)
    return {"applicable": not reasons, "reasons": reasons}


def formula_view(c, *, bid, manifest, digest):
    state = applicability(c, bid=bid, manifest=manifest, digest=digest)
    return {
        "formula_version_id": manifest["formula_version_id"],
        "formula_digest": digest,
        "formula": manifest,
        "current_applicability": state,
    }


def formula_etag(view):
    return etag("pricing-formula", view["formula_version_id"], sha(view))


def require_current(c, *, bid, manifest, digest, condition=None):
    sources.enabled_bid(c, bid, lock=True)
    view = formula_view(c, bid=bid, manifest=manifest, digest=digest)
    if not view["current_applicability"]["applicable"]:
        raise DomainError(
            "PRICING_SOURCE_CHANGED",
            "公式依据已更新，请核对后重新试算",
            409,
            view["current_applicability"],
        )
    if condition is not None:
        exact_match(condition, formula_etag(view))
    return view


def list_formula_records(c, bid, *, limit, cursor):
    sources.enabled_bid(c, bid)
    rows = c.execute(
        "SELECT object_id,reason FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula' AND decision='confirmed' AND (%s::uuid IS NULL OR object_id>%s) ORDER BY object_id LIMIT %s",
        (bid, cursor, cursor, limit + 1),
    ).fetchall()
    return {
        "items": [json.loads(row["reason"])["summary"] for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["object_id"]) if len(rows) > limit else None,
    }


def simulate(manifest, value):
    if (
        value["rounding_policy"] != manifest["rounding_policy"]
        or value["amount_unit"] != manifest["amount_unit"]
        or value["currency"] != manifest["currency"]
    ):
        raise DomainError(
            "PRICING_MEASURE_MISMATCH",
            "输入金额单位、币种或取整策略与原文公式不一致；不会自动换算",
            422,
        )
    try:
        result = calculator.simulate(manifest["formula"], value)
    except ValueError as error:
        raise DomainError("PRICING_INPUT_INVALID", str(error), 422) from None
    main = result.pop("main")
    return {
        "price_score": main["score"],
        "eligible": main["eligible"],
        "notes": main["notes"],
        "candidate_price": main["price"],
        "maximum": manifest["formula"]["maximum"],
        "baseline_price": main["baseline_price"],
        "valid_price_count": main["valid_price_count"],
        **result,
    }


def input_digest(value, formula_digest):
    return sha({"formula_digest": formula_digest, "request": value})


def publish_simulation(c, *, bid, actor, manifest, digest, value, result, condition, command):
    _work(c, bid)
    require_current(c, bid=bid, manifest=manifest, digest=digest)
    exact_match(condition, formula_etag(formula_view(c, bid=bid, manifest=manifest, digest=digest)))
    identity = uid()
    snapshot = {
        "schema": "pricing-input/1",
        "formula": manifest,
        "formula_digest": digest,
        "request": value,
        "input_digest": input_digest(value, digest),
        "created_by": str(actor),
    }
    result = {
        "id": str(identity),
        "formula_version_id": manifest["formula_version_id"],
        "formula_digest": digest,
        "input_digest": snapshot["input_digest"],
        "formula_evidence_ids": [q["evidence_id"] for q in manifest["evidence_quotes"]],
        **result,
    }
    c.execute(
        "INSERT INTO pricing_simulations(id,bid_id,formula_digest,input_manifest,result) VALUES(%s,%s,%s,%s,%s)",
        (identity, bid, digest, Jsonb(snapshot), Jsonb(result)),
    )
    audit(
        c,
        actor,
        bid,
        "pricing.simulated",
        {"simulation_id": identity, "input_digest": snapshot["input_digest"]},
        command,
    )
    return simulation_view(c, bid=bid, identity=identity)


def simulation_view(c, *, bid, identity):
    sources.enabled_bid(c, bid)
    row = fetch(c, "SELECT * FROM pricing_simulations WHERE id=%s AND bid_id=%s", (identity, bid))
    snapshot = row["input_manifest"]
    return clean(
        {
            **row["result"],
            "input": snapshot["request"],
            "formula_snapshot": snapshot["formula"],
            "created_by": snapshot["created_by"],
            "created_at": row["created_at"],
            "current_applicability": applicability(
                c, bid=bid, manifest=snapshot["formula"], digest=row["formula_digest"]
            ),
        }
    )


def simulation_etag(view):
    return etag("pricing-simulation", view["id"], sha(view))


def save_plan(c, *, bid, actor, value, condition, command):
    _work(c, bid)
    sources.enabled_bid(c, bid, lock=True)
    row = fetch(
        c,
        "SELECT * FROM pricing_simulations WHERE id=%s AND bid_id=%s",
        (value["simulation_id"], bid),
    )
    snapshot = row["input_manifest"]
    require_current(c, bid=bid, manifest=snapshot["formula"], digest=row["formula_digest"])
    current = simulation_view(c, bid=bid, identity=row["id"])
    exact_match(condition, simulation_etag(current))
    identity = uid()
    saved_input = {**snapshot, "saved_reason": value["reason"]}
    c.execute(
        "INSERT INTO pricing_plans(id,bid_id,formula_digest,input_manifest,result,created_by,simulation_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            bid,
            row["formula_digest"],
            Jsonb(saved_input),
            Jsonb(row["result"]),
            actor,
            row["id"],
        ),
    )
    audit(
        c,
        actor,
        bid,
        "pricing.plan_saved",
        {"plan_id": identity, "simulation_id": row["id"]},
        command,
    )
    return plan_view(c, bid=bid, identity=identity)


def plan_view(c, *, bid, identity):
    sources.enabled_bid(c, bid)
    row = fetch(c, "SELECT * FROM pricing_plans WHERE id=%s AND bid_id=%s", (identity, bid))
    snapshot = row["input_manifest"]
    return clean(
        {
            "id": row["id"],
            "simulation_id": row["simulation_id"],
            "formula_version_id": snapshot["formula"]["formula_version_id"],
            "formula_digest": row["formula_digest"],
            "input_digest": snapshot["input_digest"],
            "input": snapshot["request"],
            "formula_snapshot": snapshot["formula"],
            "result": row["result"],
            "reason": snapshot["saved_reason"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "current_applicability": applicability(
                c, bid=bid, manifest=snapshot["formula"], digest=row["formula_digest"]
            ),
        }
    )


def list_plans(c, *, bid, limit, cursor):
    sources.enabled_bid(c, bid)
    rows = c.execute(
        "SELECT id FROM pricing_plans WHERE bid_id=%s AND (%s::uuid IS NULL OR id>%s) ORDER BY id LIMIT %s",
        (bid, cursor, cursor, limit + 1),
    ).fetchall()
    return {
        "items": [plan_view(c, bid=bid, identity=row["id"]) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }
