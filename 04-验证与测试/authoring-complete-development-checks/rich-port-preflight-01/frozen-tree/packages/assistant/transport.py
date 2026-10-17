"""Dedicated Kafka transport for real null-scope global runs; opt-in process entry point.

Never subscribes to project/document/research topics. No process starts on import.
"""

import argparse
import json
import signal
import threading
import time

from confluent_kafka import Consumer, Producer

from packages.assistant import global_runtime, jobs
from packages.domain.runtime import DomainError, assert_role, connection, required, sha, uid

TOPIC = "tender.task.assistant-global.v1"
STOP = threading.Event()


def recover():
    """Redelivery only; the ledger/fence decides whether remote work can start."""
    with connection(True) as c:
        rows = c.execute("""UPDATE assistant_global_outbox o SET published_at=NULL,claim_token=NULL,lease_until=NULL
          FROM assistant_global_runs r WHERE o.run_id=r.id AND r.state IN ('queued','running')
          AND (r.state='queued' OR r.lease_until<=clock_timestamp())
          AND o.published_at<clock_timestamp()-interval '120 seconds'
          AND (o.lease_until IS NULL OR o.lease_until<=clock_timestamp()) RETURNING o.id""").fetchall()
        return len(rows)


def relay_once(producer, *, run_ids=None):
    token = uid()
    with connection(True) as c:
        jobs.require_schema(c, worker=True)
        rows = c.execute(
            """WITH picked AS (SELECT o.id FROM assistant_global_outbox o
          JOIN assistant_global_runs r ON r.id=o.run_id
          WHERE o.published_at IS NULL AND o.available_at<=clock_timestamp()
          AND (%s::uuid[] IS NULL OR o.run_id=ANY(%s))
          AND (o.lease_until IS NULL OR o.lease_until<=clock_timestamp())
          AND r.state IN ('queued','running') ORDER BY o.available_at,o.id LIMIT 20 FOR UPDATE OF o SKIP LOCKED)
          UPDATE assistant_global_outbox o SET claim_token=%s,lease_until=clock_timestamp()+interval '60 seconds',
          publish_attempts=publish_attempts+1 FROM picked WHERE o.id=picked.id RETURNING o.*""",
            (run_ids, run_ids, token),
        ).fetchall()
    acknowledged = 0
    for row in rows:
        if row["topic"] != TOPIC or sha(row["payload"]) != row["payload_digest"]:
            raise DomainError("ASSISTANT_TASK_INVALID", "全局Outbox协议或摘要不一致", 409)
        completed = threading.Event()
        errors: list[bool] = []

        def delivered(error, message, event=completed, failures=errors):
            if error:
                failures.append(True)
            event.set()

        producer.produce(
            TOPIC, key=str(row["run_id"]), value=json.dumps(row["payload"]), on_delivery=delivered
        )
        until = time.monotonic() + 40
        while not completed.is_set() and time.monotonic() < until and not STOP.is_set():
            producer.poll(0.1)
        if completed.is_set() and not errors:
            with connection(True) as c:
                changed = c.execute(
                    """UPDATE assistant_global_outbox SET published_at=clock_timestamp(),lease_until=NULL
                  WHERE id=%s AND claim_token=%s AND lease_until>clock_timestamp() AND published_at IS NULL RETURNING id""",
                    (row["id"], token),
                ).fetchone()
                acknowledged += int(changed is not None)
    return acknowledged


def consume_one(consumer, *, timeout=1):
    record = consumer.poll(timeout)
    if record is None:
        return False
    if record.error():
        raise RuntimeError("Global assistant Kafka transport failed")
    if record.topic() != TOPIC:
        raise DomainError("ASSISTANT_TASK_INVALID", "拒绝其他任务topic", 409)
    try:
        message = json.loads(record.value())
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise DomainError("ASSISTANT_TASK_INVALID", "全局任务不是合法JSON", 409) from error
    if not isinstance(message, dict):
        raise DomainError("ASSISTANT_TASK_INVALID", "全局任务必须是对象", 409)
    global_runtime.handle(message)  # Commit only after actual effect/failure/duplicate handling.
    consumer.commit(message=record, asynchronous=False)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["relay", "worker", "recover"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    assert_role(True)
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *args: STOP.set())
    if args.mode == "recover":
        recover()
        return
    if args.mode == "relay":
        p = Producer(
            {
                "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
                "enable.idempotence": True,
                "acks": "all",
                "delivery.timeout.ms": 30000,
            }
        )
        while not STOP.is_set():
            recover()
            relay_once(p)
            if args.once:
                break
            STOP.wait(0.3)
        p.flush(5)
        return
    consumer = Consumer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "group.id": required("ASSISTANT_GLOBAL_CONSUMER_GROUP"),
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 900000,
        }
    )
    consumer.subscribe([TOPIC])
    try:
        while not STOP.is_set():
            consume_one(consumer)
            if args.once:
                break
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
