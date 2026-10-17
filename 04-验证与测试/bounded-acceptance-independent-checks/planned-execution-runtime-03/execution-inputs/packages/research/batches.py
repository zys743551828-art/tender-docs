"""受控资料manifest、离线预览与冻结研究批次。不会发送模型请求。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from packages.domain import storage
from packages.domain.runtime import DomainError, audit, clean, fetch, match, now, sha, uid
from packages.research.core import amount, validate_split
from packages.research.outcomes import admissibility, sample_rows

DATASET_MEDIA = "application/vnd.tender.research-dataset+json"
SPLIT_MEDIA = "application/vnd.tender.research-split+json"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
TOPIC = "tender.task.research.v1"


def chapter_refs(c, submission_id):
    from packages.research.authority import require

    require(c)
    return clean(
        c.execute(
            "SELECT DISTINCT ON(cp.rule_id) cp.id,cp.rule_id,cp.manifest_blob_id,cp.digest FROM submission_records s JOIN review_runs r ON r.id=s.review_id JOIN review_inputs i ON i.id=r.input_id JOIN chapter_packages cp ON cp.review_id=r.id JOIN research_chapter_authorities a ON a.chapter_package_id=cp.id JOIN parse_runs p ON p.id=cp.parse_id WHERE s.id=%s AND p.document_version_id=s.body_version_id ORDER BY cp.rule_id,a.created_at DESC,cp.id DESC",
            (submission_id,),
        ).fetchall()
    )


def save_manifest(c, manifest, media):
    raw = json.dumps(
        clean(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    digest = sha(raw)
    old = c.execute("SELECT * FROM blobs WHERE digest=%s", (digest,)).fetchone()
    if old:
        if old["media_type"] != media:
            raise DomainError("MANIFEST_TYPE_CONFLICT", "资料清单的摘要与用途类型冲突", 409)
        return old["id"]
    identity, key = uid(), "research/manifests/" + digest + ".json"
    storage.put(key, raw, media)
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s)",
        (identity, digest, key, len(raw), media),
    )
    return identity


def load_manifest(c, identity, media):
    blob = fetch(c, "SELECT * FROM blobs WHERE id=%s AND media_type=%s", (identity, media))
    if blob["byte_size"] > MAX_MANIFEST_BYTES:
        raise DomainError("MANIFEST_TOO_LARGE", "研究清单超过受控大小", 422)
    raw = storage.get(blob["object_key"])
    if len(raw) != blob["byte_size"] or sha(raw) != blob["digest"]:
        raise DomainError("MANIFEST_CORRUPTED", "研究清单的实际字节或摘要核验失败", 409)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise DomainError("MANIFEST_INVALID", "研究清单内容无法解析", 422) from None


def freeze_dataset(c, *, actor, sample_ids, split, cutoff, command):
    rows = sample_rows(c)
    chosen = [row for row in rows if str(row["id"]) in sample_ids]
    if len(chosen) != len(sample_ids):
        raise DomainError("DATASET_SAMPLE_MISMATCH", "样本不存在或重复", 422)
    # 不能只看请求子集；未选同组样本也不能跨最终测试边界成为暗中锚点。
    from packages.research.core import connected_groups

    all_groups = connected_groups(rows)
    for row in chosen:
        if not row.get("grouping_reviewed"):
            raise DomainError(
                "SOURCE_GROUP_UNREVIEWED",
                "模板与近重复来源尚未核对，不能当独立研究样本冻结",
                409,
                {"sample_id": str(row["id"])},
            )
        _, outcome, reasons = admissibility(c, row["bid_id"], row["submission_id"])
        if reasons or row["admission"] != "admitted" or outcome["id"] != row["outcome_id"]:
            raise DomainError(
                "SAMPLE_NOT_ADMITTED",
                "样本已失效或尚未严格准入",
                409,
                {"sample_id": str(row["id"]), "reasons": reasons},
            )
    validate_split(chosen, split, cutoff)
    group_sides: dict[str, str] = {}
    for row in chosen:
        side, group = split[str(row["id"])], all_groups[str(row["id"])]
        if side != "excluded" and group in group_sides and group_sides[group] != side:
            raise DomainError("RELATED_GROUP_LEAK", "经未选样本连接的相关组发生跨集合泄漏", 422)
        if side != "excluded":
            group_sides[group] = side
    # 标签只存在dataset，绝不进入实际章节输入/普通RAG。
    dataset = {
        "schema": "research-dataset/1",
        "created_by": str(actor),
        "samples": [
            {
                "id": str(row["id"]),
                "bid_id": str(row["bid_id"]),
                "submission_id": str(row["submission_id"]),
                "outcome_id": str(row["outcome_id"]),
                "source_group_id": all_groups[str(row["id"])],
                "body_version_id": str(row["body_version_id"]),
                "body_digest": row["body_digest"],
                "known_at": row["known_at"].isoformat(),
                "submitted_at": row["submitted_at"].isoformat(),
                "label": {"technical_total": str(row["value"]), "maximum": str(row["maximum"])},
                "chapter_packages": chapter_refs(c, row["submission_id"]),
            }
            for row in chosen
        ],
    }
    split_manifest = {
        "schema": "research-split/1",
        "dataset_digest": sha(dataset),
        "cutoff": cutoff.isoformat(),
        "assignments": split,
        "source_groups": {str(row["id"]): all_groups[str(row["id"])] for row in chosen},
        "anchors_enabled": False,
    }
    result = {
        "dataset_manifest_blob_id": str(save_manifest(c, dataset, DATASET_MEDIA)),
        "split_manifest_blob_id": str(save_manifest(c, split_manifest, SPLIT_MEDIA)),
        "sample_ids": sample_ids,
        "dataset_digest": sha(dataset),
        "split_digest": sha(split_manifest),
        "formal_model_eligible": False,
    }
    audit(
        c,
        actor,
        None,
        "research.dataset_frozen",
        {"dataset_digest": result["dataset_digest"], "sample_count": len(chosen)},
        command,
    )
    return result


def validate_manifests(c, request):
    dataset = load_manifest(c, request["dataset_manifest_blob_id"], DATASET_MEDIA)
    split = load_manifest(c, request["split_manifest_blob_id"], SPLIT_MEDIA)
    if (
        dataset.get("schema") != "research-dataset/1"
        or split.get("schema") != "research-split/1"
        or split.get("dataset_digest") != sha(dataset)
        or split.get("anchors_enabled") is not False
    ):
        raise DomainError("MANIFEST_IDENTITY_INVALID", "清单用途、训练来源或数据身份不一致", 422)
    ids = {item["id"] for item in dataset["samples"]}
    if ids != set(request["sample_ids"]):
        raise DomainError("MANIFEST_SAMPLE_MISMATCH", "执行样本必须与冻结清单完全一致", 422)
    current = {str(row["id"]): row for row in sample_rows(c)}
    for item in dataset["samples"]:
        row = current.get(item["id"])
        if row is None:
            raise DomainError("SAMPLE_NOT_ADMITTED", "冻结样本不存在", 409)
        if not row.get("grouping_reviewed"):
            raise DomainError(
                "SOURCE_GROUP_UNREVIEWED", "模板与近重复来源核对已撤回，原研究清单失效", 409
            )
        _, outcome, reasons = admissibility(c, row["bid_id"], row["submission_id"])
        fields = {
            "bid_id": str(row["bid_id"]),
            "submission_id": str(row["submission_id"]),
            "outcome_id": str(outcome["id"]),
            "body_version_id": str(row["body_version_id"]),
            "body_digest": row["body_digest"],
            "known_at": row["known_at"].isoformat(),
            "submitted_at": row["submitted_at"].isoformat(),
        }
        if (
            reasons
            or row["admission"] != "admitted"
            or any(item.get(k) != value for k, value in fields.items())
        ):
            raise DomainError(
                "SAMPLE_CHANGED", "样本标签、核验或实交身份已变化，须重新冻结研究清单", 409
            )
        if item.get("label") != {
            "technical_total": str(row["value"]),
            "maximum": str(row["maximum"]),
        } or item.get("chapter_packages") != chapter_refs(c, row["submission_id"]):
            raise DomainError("SAMPLE_CHANGED", "监督标签或完整章节清单已变化，须重新冻结", 409)
    chosen = [current[key] for key in ids]
    validate_split(chosen, split["assignments"], datetime.fromisoformat(split["cutoff"]))
    from packages.research.core import connected_groups

    groups = connected_groups(list(current.values()))
    if any(split["source_groups"].get(key) != groups[key] for key in ids):
        raise DomainError("SOURCE_GROUP_CHANGED", "相关组被新资料连通，原划分需要重新核验", 409)
    from packages.research.authority import assert_current_api

    for sample in dataset["samples"]:
        for package in sample.get("chapter_packages", []):
            assert_current_api(c, package["id"])
    return dataset, split


def price_config():
    from packages.research.profile import descriptor, digest

    # 人工部署的价格快照；不猜测供应商价格。缺失则仅可看能力状态。
    raw = os.environ.get("RESEARCH_PRICE_CONFIG")
    if not raw:
        raise DomainError(
            "RESEARCH_PRICE_UNAVAILABLE", "研究价格快照尚未配置，不能预览付费计划", 503
        )
    try:
        value = json.loads(raw)
        if set(value) != {
            "profile_digest",
            "currency",
            "input_per_million",
            "output_per_million",
            "max_output_tokens",
            "context_tokens",
        }:
            raise ValueError
        profile = descriptor()
        if value["profile_digest"] != digest() or any(
            type(value[key]) is not int or value[key] != profile[key]
            for key in ("max_output_tokens", "context_tokens")
        ):
            raise ValueError
        for key in ("input_per_million", "output_per_million"):
            amount(value[key])
        if (
            not 1 <= value["max_output_tokens"] <= 8192
            or not value["max_output_tokens"] < value["context_tokens"] <= 2_000_000
        ):
            raise ValueError
        if value["currency"] not in {"CNY", "USD"}:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError):
        raise DomainError("RESEARCH_PRICE_INVALID", "研究价格快照配置不合法", 503) from None


def preview(c, *, actor, request, command):
    from packages.research.profile import digest as profile_digest

    if (
        request["profile_digest"] != profile_digest()
        or request["method_digest"] != profile_digest()
    ):
        raise DomainError(
            "RESEARCH_METHOD_UNAVAILABLE", "此运行器只支持已定义的完整章节三次判断研究方法", 409
        )
    dataset, split = validate_manifests(c, request)
    prices = price_config()
    if (
        prices["profile_digest"] != request["profile_digest"]
        or prices["currency"] != request["currency"]
    ):
        raise DomainError("RESEARCH_PRICE_PROFILE_MISMATCH", "模型配置或币种与价格快照不一致", 409)
    # 完整章节尚未冻结时，用上下文上界保守预估，不把字符数冒充token统计。
    count = 0
    for sample in dataset["samples"]:
        rules = c.execute(
            "SELECT count(*) AS n FROM submission_records s JOIN review_runs r ON r.id=s.review_id JOIN review_inputs i ON i.id=r.input_id JOIN rule_items ri ON ri.bid_id=s.bid_id AND ri.rule_set_id=(i.manifest->>'rule_set_id')::uuid WHERE s.id=%s AND ri.kind='scoring'",
            (sample["submission_id"],),
        ).fetchone()["n"]
        if rules < 1:
            raise DomainError(
                "RESEARCH_SCORING_SCOPE_MISSING",
                "样本没有冻结的评分规则与完整章节范围，不能虚构调用量",
                409,
            )
        if (
            len({p["rule_id"] for p in sample.get("chapter_packages", [])}) != rules
            or len(sample.get("chapter_packages", [])) != rules
        ):
            raise DomainError(
                "RESEARCH_FULL_CHAPTERS_MISSING",
                "完整评分章节包尚未冻结或存在重复，不能启动研究调用",
                409,
            )
        count += rules * 3 * request["repeat_count"]
    input_tokens = count * prices["context_tokens"]
    output_tokens = count * prices["max_output_tokens"]
    fee = (
        Decimal(input_tokens) * amount(prices["input_per_million"])
        + Decimal(output_tokens) * amount(prices["output_per_million"])
    ) / Decimal(1_000_000)
    if fee > amount(request["fee_limit"]):
        raise DomainError(
            "RESEARCH_BUDGET_INSUFFICIENT",
            "费用上界超过本次限额，请缩小实验范围",
            409,
            {"estimated_fee": str(fee)},
        )
    experiment, identity = uid(), uid()
    digest = sha(
        {"request": request, "dataset": sha(dataset), "split": sha(split), "prices": prices}
    )
    expiry = now() + timedelta(hours=24)
    c.execute(
        "INSERT INTO experiments(id,protocol_digest,dataset_manifest_blob_id,split_manifest_blob_id,method_digest,seed,state) VALUES(%s,%s,%s,%s,%s,0,'created')",
        (
            experiment,
            request["protocol_digest"],
            request["dataset_manifest_blob_id"],
            request["split_manifest_blob_id"],
            request["method_digest"],
        ),
    )
    c.execute(
        "INSERT INTO experiment_previews(id,experiment_id,revision,manifest_digest,request,price_config_digest,estimated_calls,estimated_tokens,estimated_fee,fee_limit,currency,expires_at,created_by) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            experiment,
            digest,
            Jsonb(request),
            sha(prices),
            count,
            input_tokens + output_tokens,
            fee,
            request["fee_limit"],
            request["currency"],
            expiry,
            actor,
        ),
    )
    for sample in dataset["samples"]:
        for package in sample["chapter_packages"]:
            c.execute(
                "INSERT INTO research_preview_chapters(preview_id,sample_id,chapter_package_id) VALUES(%s,%s,%s)",
                (identity, sample["id"], package["id"]),
            )
    audit(
        c,
        actor,
        None,
        "research.preview_created",
        {"preview_id": identity, "manifest_digest": digest, "estimated_calls": count},
        command,
    )
    return clean(
        {
            "id": identity,
            "experiment_id": experiment,
            "revision": 1,
            "manifest_digest": digest,
            "request": request,
            "price_config_digest": sha(prices),
            "estimated_calls": count,
            "estimated_tokens": input_tokens + output_tokens,
            "estimated_fee": format(fee, "f"),
            "estimate_notes": [
                "以模型上下文上界作保守费用估算，不是实际token或账单；正式执行仍需完整章节包",
                "三次逐项独立判断；整轮重复不增加独立官方样本数",
            ],
            "expires_at": expiry,
        }
    )


def run_view(c, identity):
    run = fetch(c, "SELECT * FROM research_runs WHERE id=%s", (identity,))
    counts = c.execute(
        "SELECT count(*) AS total,count(*) FILTER(WHERE state='succeeded') AS succeeded,count(*) FILTER(WHERE state='failed') AS failed,count(*) FILTER(WHERE state IN ('pending','running')) AS pending FROM research_run_items WHERE run_id=%s",
        (identity,),
    ).fetchone()
    for key in ("fee_limit", "spent_fee", "reserved_fee"):
        run[key] = format(run[key], "f")
    return clean(
        {
            key: run[key]
            for key in (
                "id",
                "experiment_id",
                "preview_id",
                "revision",
                "manifest_digest",
                "state",
                "fee_limit",
                "spent_fee",
                "reserved_fee",
                "currency",
            )
        }
        | counts
        | {"cancel_requested": run["cancel_requested_at"] is not None}
    )


def start(c, *, actor, request, command):
    record = fetch(
        c, "SELECT * FROM experiment_previews WHERE id=%s FOR UPDATE", (request["preview_id"],)
    )
    old = c.execute("SELECT id FROM research_runs WHERE preview_id=%s", (record["id"],)).fetchone()
    if (
        record["manifest_digest"] != request["manifest_digest"]
        or record["price_config_digest"] != request["price_config_digest"]
    ):
        raise DomainError("RESEARCH_PREVIEW_CHANGED", "执行请求与冻结预览不一致", 409)
    if old:
        return run_view(c, old["id"])
    if record["expires_at"] <= now() or sha(price_config()) != record["price_config_digest"]:
        raise DomainError("RESEARCH_PREVIEW_EXPIRED", "预览或价格快照已过期，请重新估算", 409)
    validate_manifests(c, record["request"])
    identity = uid()
    c.execute(
        "INSERT INTO research_runs(id,experiment_id,preview_id,revision,manifest_digest,state,fee_limit,currency) VALUES(%s,%s,%s,1,%s,'queued',%s,%s)",
        (
            identity,
            record["experiment_id"],
            record["id"],
            record["manifest_digest"],
            record["fee_limit"],
            record["currency"],
        ),
    )
    samples = {str(row["id"]): row for row in sample_rows(c)}
    for key in record["request"]["sample_ids"]:
        row = samples[key]
        for repeat in range(1, record["request"]["repeat_count"] + 1):
            c.execute(
                "INSERT INTO research_run_items(run_id,sample_id,bid_id,repeat_index,state) VALUES(%s,%s,%s,%s,'pending')",
                (identity, row["id"], row["bid_id"], repeat),
            )
    audit(
        c,
        actor,
        None,
        "research.run_created",
        {"run_id": identity, "manifest_digest": record["manifest_digest"]},
        command,
    )
    return run_view(c, identity)


def mutate_run(c, *, actor, identity, condition, kind, body, command):
    run = fetch(c, "SELECT * FROM research_runs WHERE id=%s FOR UPDATE", (identity,))
    match(condition, "research-run", identity, run["revision"])
    if kind == "cancel":
        if run["state"] not in {"succeeded", "failed", "cancelled"}:
            c.execute(
                "UPDATE research_runs SET cancel_requested_at=now(),revision=revision+1 WHERE id=%s",
                (identity,),
            )
            c.execute(
                "UPDATE research_run_items SET state='cancelled' WHERE run_id=%s AND state='pending'",
                (identity,),
            )
            c.execute(
                "UPDATE research_runs r SET state='cancelled' WHERE id=%s AND NOT EXISTS(SELECT 1 FROM research_run_items i WHERE i.run_id=r.id AND i.state='running')",
                (identity,),
            )
    else:
        if run["state"] == "cancelled":
            raise DomainError("RESEARCH_CANCELLED", "取消后的批次不可恢复，请新建预览", 409)
        unknown = c.execute(
            "SELECT 1 FROM model_calls WHERE research_run_id=%s AND state IN ('reserved','unknown') LIMIT 1",
            (identity,),
        ).fetchone()
        if unknown or run["reserved_fee"] > 0:
            raise DomainError(
                "RESEARCH_BILLING_UNKNOWN", "仍有未对账请求；保留预留金额且不自动重发", 409
            )
        if run["spent_fee"] >= run["fee_limit"]:
            raise DomainError(
                "RESEARCH_BUDGET_EXHAUSTED", "批次费用额度已用完，不能通过恢复增加额度", 409
            )
        if run["state"] not in {"paused", "failed"}:
            raise DomainError("RESEARCH_NOT_RESUMABLE", "当前批次无需恢复", 409)
        record = fetch(
            c, "SELECT request FROM experiment_previews WHERE id=%s", (run["preview_id"],)
        )
        validate_manifests(c, record["request"])
        if body["retry_failed"]:
            c.execute(
                "UPDATE research_run_items i SET state='pending',error_code=NULL WHERE i.run_id=%s AND i.state='failed' AND (SELECT count(*) FROM research_run_attempts a WHERE a.run_id=i.run_id AND a.sample_id=i.sample_id AND a.repeat_index=i.repeat_index)<3",
                (identity,),
            )
        c.execute(
            "UPDATE research_runs SET state='queued',revision=revision+1 WHERE id=%s", (identity,)
        )
    audit(
        c,
        actor,
        None,
        "research.run_" + kind,
        {"run_id": identity, "reason": body["reason"]},
        command,
    )
    return run_view(c, identity)
