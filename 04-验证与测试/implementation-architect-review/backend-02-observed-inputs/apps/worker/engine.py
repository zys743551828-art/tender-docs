"""PG阶段收据与Kafka传输，远程字节操作在事务外。"""

import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from confluent_kafka import Consumer, Producer, TopicPartition
from psycopg.types.json import Jsonb

from apps.worker.handlers import compute, publish
from packages.domain.runtime import (
    DomainError,
    assert_role,
    audit,
    clean,
    connection,
    fetch,
    now,
    required,
    uid,
    validate,
)

STOP = threading.Event()


def producer():
    return Producer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "enable.idempotence": True,
            "acks": "all",
            "delivery.timeout.ms": 30000,
        }
    )


def relay(once=False):
    assert_role(True)
    p = producer()
    while not STOP.is_set():
        token = uid()
        with connection(True) as c:
            rows = c.execute("SELECT * FROM design.claim_outbox(20,%s)", (token,)).fetchall()
        for row in rows:
            completed = threading.Event()
            error: list[str] = []

            def delivered(err, msg, errors=error, event=completed):
                if err:
                    errors.append(str(err))
                event.set()

            payload = row["payload"]
            validate("TaskEnvelope", payload)
            p.produce(
                row["topic"],
                key=f"{payload['operation_id']}:{payload['step_key']}",
                value=json.dumps(payload),
                on_delivery=delivered,
            )
            deadline = time.monotonic() + 40
            renew_at = time.monotonic() + 10
            while not completed.is_set() and time.monotonic() < deadline:
                p.poll(0.1)
                if time.monotonic() >= renew_at:
                    with connection(True) as c:
                        c.execute("SELECT design.renew_outbox(%s,%s)", (row["id"], token))
                    renew_at = time.monotonic() + 10
            if completed.is_set() and not error:
                with connection(True) as c:
                    c.execute("SELECT design.ack_outbox(%s,%s)", (row["id"], token))
        if once:
            return
        STOP.wait(0.3)


def fail_attempt_budget(c, op, message):
    """网络失败与失联接管共用阶段尝试上限；终态及死信意图原子提交。"""
    output = {"error_code": "WORKER_ATTEMPTS_EXHAUSTED", "result_ref": None}
    c.execute(
        "UPDATE step_receipts SET state='failed',fence=fence+1,owner_token=NULL,lease_until=NULL,failure_code='WORKER_ATTEMPTS_EXHAUSTED',output=%s WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
        (Jsonb(output), op["id"], message["step_key"], message["input_digest"]),
    )
    c.execute(
        "INSERT INTO effect_receipts(operation_id,effect_key,result) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
        (op["id"], message["step_key"], Jsonb(output)),
    )
    c.execute("UPDATE operations SET state='failed',finished_at=now() WHERE id=%s", (op["id"],))
    if message["step_key"] == "verify-upload":
        c.execute(
            "UPDATE uploads SET state='quarantined',error_code='WORKER_ATTEMPTS_EXHAUSTED' WHERE id=%s AND state='verifying'",
            (message["payload_ref"],),
        )
    dead = message | {"event_id": str(uid()), "created_at": now().isoformat()}
    c.execute(
        "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(deduplication_key) DO NOTHING",
        (
            dead["event_id"],
            op["id"],
            "tender.task.dead-letter.v1",
            f"dead:{op['id']}:budget",
            Jsonb(dead),
        ),
    )
    audit(c, None, op["bid_id"], "operation.failed", {"operation_id": op["id"], "output": output})


def acquire(message):
    validate("TaskEnvelope", message)
    with connection(True) as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
        op = fetch(
            c,
            "SELECT * FROM operations WHERE id=%s AND bid_id=%s FOR UPDATE",
            (message["operation_id"], message["bid_id"]),
        )
        if op["state"] in ["succeeded", "failed", "cancelled", "cancel_requested"]:
            return None
        if (
            op["input_digest"] != message["input_digest"]
            or op["kind"] != message["step_key"]
            or op["handler_version"] != message["handler_version"]
        ):
            raise DomainError("TASK_IDENTITY_MISMATCH", "任务身份不匹配")
        plan = fetch(
            c,
            "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (op["id"], message["step_key"], op["input_digest"]),
        )
        if message["generation"] < plan["dispatch_generation"]:
            return None
        if message["generation"] > plan["dispatch_generation"]:
            raise DomainError("TASK_GENERATION_FUTURE", "未知任务代次")
        old = c.execute(
            "SELECT * FROM step_receipts WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (op["id"], message["step_key"], op["input_digest"]),
        ).fetchone()
        if old and (
            old["state"] == "succeeded" or old["state"] == "running" and old["lease_until"] > now()
        ):
            return None
        if old and old["attempt_count"] >= 3:
            fail_attempt_budget(c, op, message)
            return None
        owner = uid()
        fence = old["fence"] + 1 if old else 1
        c.execute(
            "INSERT INTO step_receipts(operation_id,step_key,input_digest,fence,attempt_count,state,heartbeat_at,lease_until,owner_token) VALUES(%s,%s,%s,%s,1,'running',now(),now()+interval '120 seconds',%s) ON CONFLICT(operation_id,step_key,input_digest) DO UPDATE SET fence=excluded.fence,attempt_count=step_receipts.attempt_count+1,state='running',heartbeat_at=now(),lease_until=excluded.lease_until,owner_token=excluded.owner_token",
            (op["id"], message["step_key"], op["input_digest"], fence, owner),
        )
        c.execute("UPDATE operations SET state='running' WHERE id=%s", (op["id"],))
        return {"owner": owner, "fence": fence}


def finish(message, lease, outcome, error=None):
    with connection(True) as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
        op = fetch(c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (message["operation_id"],))
        plan = fetch(
            c,
            "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (op["id"], message["step_key"], message["input_digest"]),
        )
        receipt = fetch(
            c,
            "SELECT * FROM step_receipts WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (op["id"], message["step_key"], message["input_digest"]),
        )
        if (
            op["state"] != "running"
            or receipt["state"] != "running"
            or receipt["fence"] != lease["fence"]
            or receipt["owner_token"] != lease["owner"]
            or receipt["lease_until"] is None
            or receipt["lease_until"] <= now()
            or plan["dispatch_generation"] != message["generation"]
        ):
            return False
        if c.execute(
            "SELECT 1 FROM effect_receipts WHERE operation_id=%s AND effect_key=%s",
            (op["id"], message["step_key"]),
        ).fetchone():
            return True
        if error:
            code = error.code if isinstance(error, DomainError) else "DEPENDENCY_FAILURE"
            retryable = not isinstance(error, DomainError) or error.retryable
            if retryable and receipt["attempt_count"] < 3:
                generation = plan["dispatch_generation"] + 1
                retry_message = message | {
                    "event_id": str(uid()),
                    "generation": generation,
                    "created_at": now().isoformat(),
                }
                delay = [2, 8, 30][receipt["attempt_count"] - 1]
                c.execute(
                    "UPDATE operation_steps SET dispatch_generation=%s WHERE operation_id=%s AND step_key=%s",
                    (generation, op["id"], message["step_key"]),
                )
                c.execute(
                    "UPDATE step_receipts SET state='failed',lease_until=NULL,output=%s WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
                    (
                        Jsonb({"error_code": code}),
                        op["id"],
                        message["step_key"],
                        message["input_digest"],
                    ),
                )
                c.execute(
                    "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload,available_at) VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        retry_message["event_id"],
                        op["id"],
                        "tender.task.document.v1",
                        f"{op['id']}:{message['step_key']}:{generation}",
                        Jsonb(retry_message),
                        now() + timedelta(seconds=delay),
                    ),
                )
                audit(
                    c,
                    None,
                    op["bid_id"],
                    "operation.retry_scheduled",
                    {"operation_id": op["id"], "generation": generation, "error_code": code},
                )
                return True
            output = {"error_code": code, "result_ref": None}
            if message["step_key"] == "verify-upload":
                c.execute(
                    "UPDATE uploads SET state='quarantined',error_code=%s WHERE id=%s",
                    (code, message["payload_ref"]),
                )
            state = "failed"
            dead = message | {"event_id": str(uid()), "created_at": now().isoformat()}
            c.execute(
                "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload) VALUES(%s,%s,%s,%s,%s)",
                (
                    dead["event_id"],
                    op["id"],
                    "tender.task.dead-letter.v1",
                    f"dead:{op['id']}:{lease['fence']}",
                    Jsonb(dead),
                ),
            )
        else:
            output = publish(c, message, outcome)
            state = "succeeded"
        c.execute(
            "INSERT INTO effect_receipts(operation_id,effect_key,result) VALUES(%s,%s,%s)",
            (op["id"], message["step_key"], Jsonb(clean(output))),
        )
        c.execute(
            "UPDATE step_receipts SET state=%s,output=%s,lease_until=NULL WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
            (state, Jsonb(clean(output)), op["id"], message["step_key"], message["input_digest"]),
        )
        c.execute("UPDATE operations SET state=%s,finished_at=now() WHERE id=%s", (state, op["id"]))
        audit(
            c,
            None,
            op["bid_id"],
            "operation." + state,
            {"operation_id": op["id"], "output": output},
        )
        return True


def renew(message, lease):
    """续租与当前计划同锁序校验；过期执行者只能等待接管，不能自行复活。"""
    with connection(True) as c:
        c.execute("SELECT design.lock_worker_bid(%s)", (message["bid_id"],))
        op = fetch(c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (message["operation_id"],))
        if op["state"] != "running":
            return False
        plan = fetch(
            c,
            "SELECT * FROM operation_steps WHERE operation_id=%s AND step_key=%s AND input_digest=%s FOR UPDATE",
            (op["id"], message["step_key"], message["input_digest"]),
        )
        if plan["dispatch_generation"] != message["generation"]:
            return False
        return (
            c.execute(
                "UPDATE step_receipts SET heartbeat_at=clock_timestamp(),lease_until=clock_timestamp()+interval '120 seconds' WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND state='running' AND fence=%s AND owner_token=%s AND lease_until>clock_timestamp() RETURNING 1",
                (
                    op["id"],
                    message["step_key"],
                    message["input_digest"],
                    lease["fence"],
                    lease["owner"],
                ),
            ).fetchone()
            is not None
        )


def handle(message):
    lease = acquire(message)
    if lease is None:
        return
    done = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not done.wait(30):
            try:
                if not renew(message, lease):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        try:
            outcome = compute(message)
        except Exception as error:
            if not lost.is_set():
                finish(message, lease, None, error)
        else:
            if not lost.is_set():
                finish(message, lease, outcome)
    finally:
        done.set()
        thread.join(timeout=1)


def consume():
    assert_role(True)
    consumer = Consumer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "group.id": "tender-document-worker-v1",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 300000,
        }
    )

    def on_revoke(client, partitions):
        # 已撤销分区的远程计算仍按PG收据/fence收尾，但旧assignment不得提交位点。
        for part in partitions:
            pending.pop((part.topic, part.partition), None)

    consumer.subscribe(["tender.task.document.v1"], on_revoke=on_revoke)
    pool = ThreadPoolExecutor(max_workers=2)
    pending: dict[tuple[str, int], tuple[Any, Future[Any]]] = {}
    try:
        while not STOP.is_set():
            for key, (message, future) in list(pending.items()):
                if future.done():
                    try:
                        future.result()
                    except Exception:
                        # 不提交失败处理器的offset；失效租约交PG对账，分区从原消息重试。
                        consumer.seek(
                            TopicPartition(message.topic(), message.partition(), message.offset())
                        )
                    else:
                        consumer.commit(message=message, asynchronous=False)
                    consumer.resume([TopicPartition(*key)])
                    del pending[key]
            msg = consumer.poll(0.2)
            if msg is None or msg.error():
                continue
            topic, partition = msg.topic(), msg.partition()
            if topic is None or partition is None or msg.value() is None:
                raise RuntimeError("缺少消息身份或内容，保留消费位点")
            key = (topic, partition)
            if key in pending:
                continue
            consumer.pause([TopicPartition(*key)])
            try:
                payload = json.loads(msg.value() or b"")
            except (ValueError, TypeError):
                raise RuntimeError("不合法任务消息，停止消费者并保留位点") from None
            pending[key] = (msg, pool.submit(handle, payload))
    finally:
        pool.shutdown(wait=True)
        consumer.close()


def reconcile(once=False):
    assert_role(True)
    while not STOP.is_set():
        with connection(True) as c:
            ids = c.execute(
                "SELECT id,bid_id FROM operations WHERE state IN ('cancel_requested','queued','running') ORDER BY created_at LIMIT 100"
            ).fetchall()
        for item in ids:
            with connection(True) as c:
                c.execute("SELECT design.lock_worker_bid(%s)", (item["bid_id"],))
                op = fetch(c, "SELECT * FROM operations WHERE id=%s FOR UPDATE", (item["id"],))
                if op["state"] == "cancel_requested":
                    if op["kind"] == "verify-upload":
                        original = fetch(
                            c,
                            "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at LIMIT 1",
                            (op["id"],),
                        )["payload"]
                        c.execute(
                            "UPDATE uploads SET state='authorized',error_code='VERIFICATION_CANCELLED' WHERE id=%s AND state='verifying'",
                            (original["payload_ref"],),
                        )
                        c.execute(
                            "UPDATE upload_transfers SET revision=revision+1 WHERE upload_id=%s",
                            (original["payload_ref"],),
                        )
                    c.execute(
                        "UPDATE step_receipts SET state='cancelled',fence=fence+1,lease_until=NULL WHERE operation_id=%s AND state='running'",
                        (op["id"],),
                    )
                    c.execute(
                        "UPDATE operations SET state='cancelled',finished_at=now() WHERE id=%s",
                        (op["id"],),
                    )
                    audit(
                        c, None, item["bid_id"], "operation.cancelled", {"operation_id": op["id"]}
                    )
                    continue
                if op["state"] not in ["queued", "running"]:
                    continue
                step = c.execute(
                    "SELECT p.*,s.lease_until,s.state,s.attempt_count FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s FOR UPDATE OF p",
                    (op["id"],),
                ).fetchone()
                if (
                    not step
                    or step["state"] == "succeeded"
                    or step["lease_until"]
                    and step["lease_until"] > now()
                ):
                    continue
                dispatch = fetch(
                    c,
                    "SELECT * FROM outbox WHERE operation_id=%s AND topic='tender.task.document.v1' AND (payload->>'generation')::bigint=%s ORDER BY available_at DESC LIMIT 1",
                    (op["id"], step["dispatch_generation"]),
                )
                if step["attempt_count"] is not None and step["attempt_count"] >= 3:
                    fail_attempt_budget(c, op, dispatch["payload"])
                    continue
                # 当前派发意图已经持久化，relay负责等待和重投，不能对账绕过退避。
                if dispatch["published_at"] is None or dispatch["available_at"] > now():
                    continue
                # 尚未领取的已发布代次保留派发宽限，旧receipt不代表新代次已经失联。
                if step["state"] != "running" and dispatch["published_at"] > now() - timedelta(
                    seconds=120
                ):
                    continue
                prior = dispatch["payload"]
                # 先永久废除旧执行权，再提升投递代次；二者在同一bid锁事务。
                c.execute(
                    "UPDATE step_receipts SET fence=fence+1,owner_token=NULL,lease_until=NULL,state='failed',failure_code='WORKER_LEASE_EXPIRED' WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND state='running'",
                    (op["id"], step["step_key"], step["input_digest"]),
                )
                generation = step["dispatch_generation"] + 1
                c.execute(
                    "UPDATE operation_steps SET dispatch_generation=%s WHERE operation_id=%s",
                    (generation, op["id"]),
                )
                prior = prior | {
                    "event_id": str(uid()),
                    "generation": generation,
                    "created_at": now().isoformat(),
                }
                c.execute(
                    "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload) VALUES(%s,%s,%s,%s,%s)",
                    (
                        prior["event_id"],
                        op["id"],
                        "tender.task.document.v1",
                        f"{op['id']}:{op['kind']}:{generation}",
                        Jsonb(prior),
                    ),
                )
        if once:
            return
        STOP.wait(5)
