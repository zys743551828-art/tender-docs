"""Kafka专用研究进程；默认禁用收费执行，PM开启有界验证窗口后显式启用。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading

from confluent_kafka import Consumer

from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, fetch, required, sha
from packages.research import batches, execution
from packages.research.authority import qualify
from packages.research.chapters import PACKAGE_MEDIA
from packages.research.core import judge_three
from packages.research.ledger import lock_run_scope, research_connection
from packages.research.model_port import ControlledJudgmentPort
from packages.research.profile import digest

STOP = threading.Event()


def enabled():
    if os.environ.get("RESEARCH_MODEL_EXECUTION_ENABLED") != "1":
        raise DomainError(
            "RESEARCH_EXECUTION_DISABLED", "研究付费执行尚未开启；离线预览和资料核验仍可使用", 503
        )


def heartbeat(message, lease, stop):
    while not stop.wait(15):
        with research_connection() as c:
            changed = c.execute(
                "UPDATE step_receipts s SET heartbeat_at=clock_timestamp(),lease_until=clock_timestamp()+interval '120 seconds' WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND fence=%s AND owner_token=%s AND state='running' AND lease_until>clock_timestamp() AND EXISTS(SELECT 1 FROM operations o WHERE o.id=s.operation_id AND o.state='running' AND o.cancel_requested_at IS NULL)",
                (
                    message["operation_id"],
                    message["step_key"],
                    message["input_digest"],
                    lease["fence"],
                    lease["owner"],
                ),
            ).rowcount
        if not changed:
            return


def packages_for_item(message, lease):
    with research_connection() as c:
        run = fetch(
            c,
            "SELECT r.*,p.request,p.price_config_digest FROM research_runs r JOIN experiment_previews p ON p.id=r.preview_id WHERE r.id=%s",
            (lease["run_id"],),
        )
        if (
            run["request"]["profile_digest"] != digest()
            or run["request"]["method_digest"] != digest()
            or run["price_config_digest"] != sha(batches.price_config())
        ):
            raise DomainError("RESEARCH_PROFILE_CHANGED", "冻结方法或价格快照与执行进程不一致", 409)
        sample = fetch(c, "SELECT * FROM samples WHERE id=%s", (lease["sample_id"],))
        if sample["admission"] != "admitted":
            raise DomainError("SAMPLE_NOT_ADMITTED", "样本准入已变化，未执行新的研究请求", 409)
        dataset_metadata = batches.manifest_metadata(
            c, run["request"]["dataset_manifest_blob_id"], batches.DATASET_MEDIA
        )
    dataset = batches.read_manifest(dataset_metadata)
    selected = next(
        (item for item in dataset["samples"] if item["id"] == str(lease["sample_id"])), None
    )
    if (
        selected is None
        or str(sample["outcome_id"]) != selected["outcome_id"]
        or str(sample["submission_id"]) != selected["submission_id"]
    ):
        raise DomainError("RESEARCH_SAMPLE_CHANGED", "执行样本不属于冻结清单", 409)
    refs = selected.get("chapter_packages", [])
    if not refs:
        raise DomainError(
            "RESEARCH_FULL_CHAPTERS_MISSING", "没有完整相关章节清单，不发送模型请求", 409
        )
    with research_connection() as c:
        metadata = [
            batches.manifest_metadata(c, ref["manifest_blob_id"], PACKAGE_MEDIA) for ref in refs
        ]
    result = []
    for ref, blob in zip(refs, metadata, strict=True):
        package = batches.read_manifest(blob)
        if (
            package["digest"] != ref["digest"]
            or package["body_version_id"] != selected["body_version_id"]
            or package["rule"]["id"] != ref["rule_id"]
        ):
            raise DomainError("RESEARCH_PACKAGE_CHANGED", "章节包的正文、规则或摘要不一致", 409)
        result.append(package)
    with research_connection() as c:
        lock_run_scope(c, lease["run_id"])
        qualify(c, message, lease)
    return result


def process(message, transport):
    """传入显式合成transport可验证工程；CLI才构造已配置真实端口。"""
    lease = execution.acquire(message)
    if lease is None:
        return None
    local_stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(message, lease, local_stop), daemon=True)
    thread.start()
    try:
        port = ControlledJudgmentPort(
            run_id=lease["run_id"], message=message, lease=lease, transport=transport
        )
        results = [judge_three(package, port) for package in packages_for_item(message, lease)]
        return execution.finish(
            message,
            lease,
            result={
                "schema": "research-sample-result/1",
                "sample_id": str(lease["sample_id"]),
                "repeat_index": lease["repeat_index"],
                "items": results,
                "formal_prediction": {
                    "mode": "unavailable",
                    "reasons": ["研究判断可运行不代表真实精度或业务发布验收通过"],
                },
            },
        )
    except DomainError as error:
        return execution.finish(message, lease, error_code=error.code)
    except Exception:
        # 不把供应商正文/URL/堆栈写日志；收据和预算由专用reconcile保守收口。
        return execution.finish(message, lease, error_code="RESEARCH_EXECUTION_FAILED")
    finally:
        local_stop.set()
        thread.join(timeout=2)


def schedule(once=False):
    enabled()
    while not STOP.is_set():
        with research_connection() as c:
            runs = c.execute(
                "SELECT id FROM research_runs WHERE state IN ('queued','running') AND cancel_requested_at IS NULL ORDER BY id LIMIT 100"
            ).fetchall()
        for run in runs:
            execution.schedule_one(run["id"])
        if once:
            return
        STOP.wait(0.5)


def reconcile(once=False, run_id=None):
    while not STOP.is_set():
        with research_connection() as c:
            candidates = c.execute(
                "SELECT o.id,o.bid_id,a.run_id FROM operations o JOIN research_run_attempts a ON a.operation_id=o.id JOIN research_runs r ON r.id=a.run_id LEFT JOIN step_receipts s ON s.operation_id=o.id WHERE o.handler_version='research/1' AND o.state IN ('queued','running','cancel_requested') AND (%s::uuid IS NULL OR r.id=%s) AND (r.cancel_requested_at IS NOT NULL OR o.cancel_requested_at IS NOT NULL OR (s.state='running' AND s.lease_until<=clock_timestamp())) ORDER BY o.id LIMIT 100",
                (run_id, run_id),
            ).fetchall()
        for candidate in candidates:
            with research_connection() as c:
                lock_run_scope(c, candidate["run_id"])
                c.execute("SELECT design.lock_worker_bid(%s)", (candidate["bid_id"],))
                op = fetch(c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (candidate["id"],))
                if op["state"] not in {"queued", "running", "cancel_requested"}:
                    continue
                receipt = c.execute(
                    "SELECT * FROM step_receipts WHERE operation_id=%s FOR UPDATE", (op["id"],)
                ).fetchone()
                attempt = fetch(
                    c, "SELECT * FROM research_run_attempts WHERE operation_id=%s", (op["id"],)
                )
                run = fetch(
                    c, "SELECT * FROM research_runs WHERE id=%s FOR UPDATE", (attempt["run_id"],)
                )
                cancelled = (
                    run["cancel_requested_at"] is not None or op["cancel_requested_at"] is not None
                )
                from packages.domain.runtime import now

                if not cancelled and (
                    receipt is None
                    or receipt["lease_until"] is None
                    or receipt["lease_until"] > now()
                ):
                    continue
                state, code = (
                    ("cancelled", "RESEARCH_CANCELLED")
                    if cancelled
                    else ("failed", "RESEARCH_LEASE_EXPIRED")
                )
                c.execute(
                    "UPDATE step_receipts SET state=%s,fence=fence+1,owner_token=NULL,lease_until=NULL,failure_code=%s WHERE operation_id=%s AND state='running'",
                    (state, code, op["id"]),
                )
                c.execute(
                    "UPDATE operations SET state=%s,finished_at=now(),cancel_requested_at=CASE WHEN %s THEN coalesce(cancel_requested_at,now()) ELSE cancel_requested_at END WHERE id=%s",
                    (state, cancelled, op["id"]),
                )
                c.execute(
                    "UPDATE research_run_items SET state=%s,error_code=%s WHERE run_id=%s AND sample_id=%s AND repeat_index=%s AND operation_id=%s",
                    (
                        state,
                        code,
                        run["id"],
                        attempt["sample_id"],
                        attempt["repeat_index"],
                        op["id"],
                    ),
                )
                c.execute(
                    "UPDATE model_calls SET state='unknown',usage=coalesce(usage,'{}'::jsonb)||'{\"reconciliation_required\":true}'::jsonb WHERE operation_id=%s AND state='reserved'",
                    (op["id"],),
                )
                unknown = c.execute(
                    "SELECT 1 FROM model_calls WHERE research_run_id=%s AND state='unknown' LIMIT 1",
                    (run["id"],),
                ).fetchone()
                active = c.execute(
                    "SELECT count(*) AS n FROM research_run_items WHERE run_id=%s AND state IN ('pending','running')",
                    (run["id"],),
                ).fetchone()["n"]
                c.execute(
                    "UPDATE research_runs SET state=CASE WHEN state='cancelled' THEN state WHEN cancel_requested_at IS NOT NULL AND %s=0 THEN 'cancelled' WHEN %s THEN 'paused' WHEN %s>0 THEN 'running' ELSE 'failed' END,revision=revision+1 WHERE id=%s",
                    (active, unknown is not None, active, run["id"]),
                )
        if once:
            return
        STOP.wait(2)


def consume():
    enabled()
    transport = DeepSeekClient.from_env()
    consumer = Consumer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "group.id": "tender-research-worker-v1",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 3_600_000,
        }
    )
    consumer.subscribe([batches.TOPIC])
    try:
        while not STOP.is_set():
            message = consumer.poll(0.2)
            if message is None or message.error():
                continue
            try:
                payload = json.loads(message.value() or b"")
            except (ValueError, TypeError):
                raise RuntimeError("研究任务消息不合法，保留消费位点") from None
            process(payload, transport)
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["schedule", "consume", "reconcile"])
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        if arguments.mode == "schedule":
            schedule(arguments.once)
        elif arguments.mode == "reconcile":
            reconcile(arguments.once)
        else:
            consume()
    except DomainError as error:
        raise SystemExit(error.code) from None


if __name__ == "__main__":
    main()
