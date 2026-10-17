"""Preview a project-scoped, two-call acceptance window; execution needs explicit user approval.

Uses a separate disposable Kafka group, preserving the ordinary consumer group's offsets.
The ordinary handler, ledger and official provider transport remain in use. This is a
bounded acceptance tool, never the continuously running business worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from packages.domain.runtime import ROOT, DomainError

CALLS = 2
OUTPUT_TOKENS = 8192
INPUT_BYTES = 24576
LOGICAL_KEYS = {"extract-rules", "core-judgment"}


class AcceptanceBoundary:
    def __init__(self, bid_id: UUID, upstream, verify_sources, deadline=float("inf")):
        self.bid_id, self.upstream, self.verify_sources = bid_id, upstream, verify_sources
        self.deadline = deadline
        self.started = 0
        self.logical_started: set[str] = set()
        self.lock = threading.Lock()

    def __call__(self, message, lease, *, logical_key, messages, json_schema, max_tokens=2048):
        self.verify_sources()
        try:
            same_project = UUID(message["bid_id"]) == self.bid_id
        except (KeyError, ValueError, TypeError, AttributeError):
            same_project = False
        encoded = json.dumps(
            {"messages": messages, "schema": json_schema}, ensure_ascii=False
        ).encode()
        if (
            not same_project
            or time.monotonic() >= self.deadline
            or logical_key not in LOGICAL_KEYS
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= OUTPUT_TOKENS
            or len(encoded) > INPUT_BYTES
        ):
            raise DomainError("ACCEPTANCE_SCOPE_REJECTED", "超出已限定的项目、调用或输入范围", 409)
        with self.lock:
            if logical_key in self.logical_started:
                raise DomainError(
                    "ACCEPTANCE_LOGICAL_CALL_REPEATED", "本阶段已进入验收窗口，未重复发送", 409
                )
            if self.started >= CALLS:
                raise DomainError(
                    "ACCEPTANCE_CALL_LIMIT", "本次验收调用窗口已用尽，未继续发送", 409
                )
            # Count an invocation before delegation, including failure/unknown/cached paths.
            # This conservative cap can underuse a window; it cannot add a third request.
            self.started += 1
            self.logical_started.add(logical_key)
        return self.upstream(
            message,
            lease,
            logical_key=logical_key,
            messages=messages,
            json_schema=json_schema,
            max_tokens=max_tokens,
        )


def frozen_sources(candidate: Path):
    data = json.loads(candidate.read_text())
    rows = []
    for item in data["sources"]:
        path = Path(item["path"])
        path = path if path.is_absolute() else ROOT / path
        path = path.resolve()
        if not path.is_relative_to(ROOT) or any(
            part.startswith(".") for part in path.relative_to(ROOT).parts
        ):
            raise ValueError("Acceptance source must be a public project source")
        rows.append((path, item["sha256"]))

    def verify():
        if not rows or any(
            hashlib.sha256(p.read_bytes()).hexdigest() != digest for p, digest in rows
        ):
            raise DomainError("ACCEPTANCE_SOURCE_CHANGED", "候选输入已变化，停止本次验收窗口", 409)

    verify()
    return verify


@contextmanager
def exclusive_window(bid_id):
    import psycopg

    from packages.domain.runtime import required

    # A session lock in autocommit, never an open business transaction over model I/O.
    with psycopg.connect(required("WORKER_DATABASE_DSN"), autocommit=True) as c:
        observed = c.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
            ("acceptance-window:" + str(bid_id),),
        ).fetchone()
        if not observed or not observed[0]:
            raise DomainError("ACCEPTANCE_WINDOW_BUSY", "该项目已有验收窗口，未启动第二执行器", 409)
        try:
            yield
        finally:
            c.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                ("acceptance-window:" + str(bid_id),),
            )


def execute(bid_id, candidate, seconds):
    # Import runtime executors only on an explicitly requested execution path.
    from confluent_kafka import Consumer

    from apps.worker.engine import handle
    from packages.domain import model_calls
    from packages.domain.runtime import assert_role, connection, required

    with exclusive_window(bid_id):
        return _execute_locked(
            bid_id,
            candidate,
            seconds,
            Consumer,
            handle,
            model_calls,
            assert_role,
            connection,
            required,
        )


def _execute_locked(
    bid_id, candidate, seconds, Consumer, handle, model_calls, assert_role, connection, required
):
    verify = frozen_sources(candidate)
    assert_role(True)
    with connection(True) as c:
        row = c.execute("SELECT id FROM bids WHERE id=%s", (bid_id,)).fetchone()
        old = c.execute(
            "SELECT count(*) AS n FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s",
            (bid_id,),
        ).fetchone()["n"]
    if row is None or old:
        raise DomainError(
            "ACCEPTANCE_PROJECT_NOT_FRESH", "验收项目不存在或已有模型调用，窗口未启动", 409
        )
    deadline = time.monotonic() + seconds
    boundary = AcceptanceBoundary(bid_id, model_calls.generate, verify, deadline)
    original = model_calls.generate
    consumer = Consumer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "group.id": "tender-acceptance-" + uuid4().hex,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 300000,
        }
    )
    model_calls.generate = boundary
    seen = 0
    try:
        consumer.subscribe(["tender.task.review.v1"])
        while time.monotonic() < deadline and seen < 10000:
            verify()
            message = consumer.poll(0.2)
            if message is None or message.error():
                continue
            seen += 1
            payload = json.loads(message.value())
            if payload.get("bid_id") != str(bid_id):
                continue  # Disposable group's offsets are never committed or shared.
            if payload.get("handler_version") not in ("prepare/1", "review/1"):
                raise DomainError("ACCEPTANCE_HANDLER_REJECTED", "验收消息处理器不在限定范围", 409)
            handle(payload)
            with connection(True) as c:
                failures = c.execute(
                    "SELECT 1 FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s AND mc.state IN ('failed','unknown') LIMIT 1",
                    (bid_id,),
                ).fetchone()
                terminal = c.execute(
                    "SELECT state FROM operations WHERE bid_id=%s AND handler_version='review/1' ORDER BY created_at DESC LIMIT 1",
                    (bid_id,),
                ).fetchone()
                failed_operation = c.execute(
                    "SELECT 1 FROM operations WHERE bid_id=%s AND handler_version IN ('prepare/1','review/1') AND state IN ('failed','cancelled','cancel_requested') LIMIT 1",
                    (bid_id,),
                ).fetchone()
            if failures:
                return {
                    "status": "stopped_model_failure_or_unknown",
                    "guarded_invocations": boundary.started,
                }
            if failed_operation:
                return {
                    "status": "stopped_business_validation_or_cancel",
                    "guarded_invocations": boundary.started,
                }
            if terminal and terminal["state"] in ("succeeded", "failed", "cancelled"):
                return {"status": terminal["state"], "guarded_invocations": boundary.started}
        return {"status": "window_closed", "guarded_invocations": boundary.started}
    finally:
        model_calls.generate = original
        consumer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bid-id", type=UUID, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument(
        "--execute", action="store_true", help="Only after explicit user authorization"
    )
    options = parser.parse_args()
    if not 1 <= options.seconds <= 180:
        parser.error("window must be at most 180 seconds")
    if not options.execute:
        print(
            json.dumps(
                {
                    "mode": "preview_only_no_external_calls",
                    "bid_id": str(options.bid_id),
                    "max_invocations": CALLS,
                    "max_output_tokens_each": OUTPUT_TOKENS,
                    "max_input_bytes_each": INPUT_BYTES,
                    "seconds": options.seconds,
                    "ordinary_group_offsets_modified": False,
                }
            )
        )
        return
    try:
        result = execute(options.bid_id, options.candidate, options.seconds)
    except (DomainError, ValueError, OSError) as error:
        result = {
            "status": "stopped",
            "code": error.code
            if isinstance(error, DomainError)
            else "ACCEPTANCE_DEPENDENCY_INVALID",
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
