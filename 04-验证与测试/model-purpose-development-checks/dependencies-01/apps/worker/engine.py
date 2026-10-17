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
    dispatch_step,
    fetch,
    now,
    required,
    uid,
    validate,
)

STOP = threading.Event()
ORDINARY_HANDLERS = (
    "document/2",
    "document/1",
    "prepare/1",
    "review/1",
    "export/1",
    "report/1",
    "rag-index/1",
    "knowledge-index/1",
)


def task_topic(handler):
    if handler in ("export/1", "report/1"):
        return "tender.task.export.v1"
    return (
        "tender.task.review.v1"
        if handler in ("review/1", "prepare/1")
        else "tender.task.document.v1"
    )


def dependencies_ready(c, operation, dependencies):
    if not dependencies:
        return True
    rows = c.execute(
        "SELECT p.step_key,p.required,s.state FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s AND p.step_key=ANY(%s)",
        (operation, dependencies),
    ).fetchall()
    return len(rows) == len(dependencies) and all(
        row["state"] == "succeeded"
        or not row["required"]
        and row["state"] in ("failed", "cancelled")
        for row in rows
    )


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


def advance_plan(c, op, message):
    """终态可选步骤不阻止必需汇总，但不能绕过仍在运行的阶段。"""
    plans = c.execute(
        "SELECT p.*,s.state FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s ORDER BY p.step_key",
        (op["id"],),
    ).fetchall()
    for pending in plans:
        if pending["state"] is None and dependencies_ready(c, op["id"], pending["depends_on"]):
            dispatch_step(
                c,
                op["id"],
                op["bid_id"],
                pending["step_key"],
                pending["input_digest"],
                pending["handler_version"],
                message["payload_ref"],
                pending["dispatch_generation"],
                task_topic(pending["handler_version"]),
            )
    complete = all(p["state"] in ("succeeded", "failed", "cancelled") for p in plans)
    required_ok = all(p["state"] == "succeeded" for p in plans if p["required"])
    return "succeeded" if complete and required_ok else "running"


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
    plan = fetch(
        c,
        "SELECT required FROM operation_steps WHERE operation_id=%s AND step_key=%s",
        (op["id"], message["step_key"]),
    )
    operation_state = "failed" if plan["required"] else advance_plan(c, op, message)
    c.execute(
        "UPDATE operations SET state=%s,finished_at=CASE WHEN %s IN ('failed','succeeded') THEN now() ELSE NULL END WHERE id=%s",
        (operation_state, operation_state, op["id"]),
    )
    c.execute(
        "UPDATE model_calls SET state='unknown',usage=coalesce(usage,'{}'::jsonb)||%s WHERE operation_id=%s AND state='reserved' AND (%s OR usage->'reservation'->>'step_key'=%s)",
        (
            Jsonb({"billing_state": "unknown", "failure_code": "WORKER_ATTEMPTS_EXHAUSTED"}),
            op["id"],
            plan["required"],
            message["step_key"],
        ),
    )
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
            f"dead:{op['id']}:{message['step_key']}:budget",
            Jsonb(dead),
        ),
    )
    audit(
        c,
        None,
        op["bid_id"],
        "operation." + operation_state,
        {
            "operation_id": op["id"],
            "step_key": message["step_key"],
            "step_state": "failed",
            "output": output,
        },
    )


def acquire(message):
    validate("TaskEnvelope", message)
    if message["handler_version"] not in ORDINARY_HANDLERS:
        raise DomainError("TASK_HANDLER_NOT_ALLOWED", "普通执行器不受理此处理器", 409)
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
        if plan["handler_version"] != message["handler_version"]:
            raise DomainError("TASK_IDENTITY_MISMATCH", "阶段处理器身份不匹配")
        if c.execute(
            "SELECT 1 FROM effect_receipts WHERE operation_id=%s AND effect_key=%s",
            (op["id"], message["step_key"]),
        ).fetchone():
            return None
        if not dependencies_ready(c, op["id"], plan["depends_on"]):
            generation = plan["dispatch_generation"] + 1
            c.execute(
                "UPDATE operation_steps SET dispatch_generation=%s WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
                (generation, op["id"], message["step_key"], message["input_digest"]),
            )
            dispatch_step(
                c,
                op["id"],
                op["bid_id"],
                message["step_key"],
                message["input_digest"],
                message["handler_version"],
                message["payload_ref"],
                generation,
                task_topic(message["handler_version"]),
                available_at=now() + timedelta(seconds=2),
            )
            return None
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
                        task_topic(message["handler_version"]),
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
            if message["handler_version"] == "review/1":
                from apps.worker.review_handlers import publish_failure

                publish_failure(c, message, code)
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
                    f"dead:{op['id']}:{message['step_key']}:{lease['fence']}",
                    Jsonb(dead),
                ),
            )
        else:
            if message["handler_version"] == "knowledge-index/1":
                from packages.knowledge.rag_tasks import publish as publish_knowledge

                output = publish_knowledge(c, message, outcome)
            elif message["handler_version"] == "report/1":
                from apps.worker.report_handlers import publish as publish_report

                output = publish_report(c, message, outcome)
            elif message["handler_version"] == "rag-index/1":
                from packages.rag.tasks import publish as publish_index

                output = publish_index(c, message, outcome)
            elif message["handler_version"] == "export/1":
                from apps.worker.export_handlers import publish as publish_export

                output = publish_export(c, message, outcome)
            elif message["handler_version"] in ("review/1", "prepare/1"):
                from apps.worker.review_handlers import publish as publish_review

                output = publish_review(c, message, outcome)
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
        operation_state = state
        if state == "succeeded" or state == "failed" and not plan["required"]:
            operation_state = advance_plan(c, op, message)
        c.execute(
            "UPDATE operations SET state=%s,finished_at=CASE WHEN %s IN ('succeeded','failed') THEN now() ELSE NULL END WHERE id=%s",
            (operation_state, operation_state, op["id"]),
        )
        if operation_state == "succeeded" and message["handler_version"] == "review/1":
            from packages.metrics.snapshots import observe_review_risks

            review = fetch(c, "SELECT id FROM review_runs WHERE operation_id=%s", (op["id"],))
            observe_review_risks(
                c,
                bid=op["bid_id"],
                actor=None,
                command=op["command_id"],
                review_id=review["id"],
            )
        audit(
            c,
            None,
            op["bid_id"],
            "operation." + operation_state,
            {
                "operation_id": op["id"],
                "step_key": message["step_key"],
                "step_state": state,
                "output": output,
            },
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


def run_compute(message, lease):
    if message["handler_version"] == "knowledge-index/1":
        from packages.knowledge.rag_tasks import compute as compute_knowledge

        return compute_knowledge(message, lease)
    if message["handler_version"] == "report/1":
        from apps.worker.report_handlers import compute as compute_report

        return compute_report(message, lease)
    if message["handler_version"] == "rag-index/1":
        from packages.rag.tasks import compute as compute_index

        return compute_index(message, lease)
    if message["handler_version"] == "export/1":
        from apps.worker.export_handlers import compute as compute_export

        return compute_export(message, lease)
    if message["handler_version"] in ("review/1", "prepare/1"):
        from apps.worker.review_handlers import compute as compute_review

        return compute_review(message, lease)
    return compute(message)


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
            outcome = run_compute(message, lease)
        except Exception as error:
            if not lost.is_set():
                finish(message, lease, None, error)
        else:
            if not lost.is_set():
                try:
                    finish(message, lease, outcome)
                except DomainError as error:
                    # 结果发布校验失败也持久化失败；不让已知无效结果悬挂到租约过期。
                    finish(message, lease, None, error)
    finally:
        done.set()
        thread.join(timeout=1)


def consume(kind="document"):
    assert_role(True)
    consumer = Consumer(
        {
            "bootstrap.servers": required("KAFKA_BOOTSTRAP_SERVERS"),
            "group.id": f"tender-{kind}-worker-v1",
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

    if kind not in ("document", "review", "export"):
        raise DomainError("WORKER_KIND_INVALID", "未配置该消费者职责", 422)
    consumer.subscribe([f"tender.task.{kind}.v1"], on_revoke=on_revoke)
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
                "SELECT id,bid_id FROM operations WHERE state IN ('cancel_requested','queued','running') AND handler_version=ANY(%s) ORDER BY created_at LIMIT 100",
                (list(ORDINARY_HANDLERS),),
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
                        "UPDATE model_calls SET state='unknown',usage=coalesce(usage,'{}'::jsonb)||%s WHERE operation_id=%s AND state='reserved'",
                        (
                            Jsonb(
                                {"billing_state": "unknown", "failure_code": "OPERATION_CANCELLED"}
                            ),
                            op["id"],
                        ),
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
                steps = c.execute(
                    "SELECT p.*,s.lease_until,s.state,s.attempt_count FROM operation_steps p LEFT JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE p.operation_id=%s ORDER BY p.step_key FOR UPDATE OF p",
                    (op["id"],),
                ).fetchall()
                for step in steps:
                    if (
                        step["state"] == "succeeded"
                        or step["lease_until"]
                        and step["lease_until"] > now()
                    ):
                        continue
                    if c.execute(
                        "SELECT 1 FROM effect_receipts WHERE operation_id=%s AND effect_key=%s",
                        (op["id"], step["step_key"]),
                    ).fetchone():
                        continue
                    topic = task_topic(step["handler_version"])
                    dispatch = c.execute(
                        "SELECT * FROM outbox WHERE operation_id=%s AND topic=%s AND payload->>'step_key'=%s AND (payload->>'generation')::bigint=%s ORDER BY available_at DESC LIMIT 1",
                        (op["id"], topic, step["step_key"], step["dispatch_generation"]),
                    ).fetchone()
                    if dispatch is None:
                        if dependencies_ready(c, op["id"], step["depends_on"]):
                            original = fetch(
                                c,
                                "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at LIMIT 1",
                                (op["id"],),
                            )["payload"]
                            dispatch_step(
                                c,
                                op["id"],
                                op["bid_id"],
                                step["step_key"],
                                step["input_digest"],
                                step["handler_version"],
                                original["payload_ref"],
                                step["dispatch_generation"],
                                topic,
                            )
                        continue
                    if step["attempt_count"] is not None and step["attempt_count"] >= 3:
                        fail_attempt_budget(c, op, dispatch["payload"])
                        break
                    # 当前同代意图由relay等待/重投，不能绕过退避或派发宽限。
                    if dispatch["published_at"] is None or dispatch["available_at"] > now():
                        continue
                    if step["state"] != "running" and dispatch["published_at"] > now() - timedelta(
                        seconds=120
                    ):
                        continue
                    c.execute(
                        "UPDATE step_receipts SET fence=fence+1,owner_token=NULL,lease_until=NULL,state='failed',failure_code='WORKER_LEASE_EXPIRED' WHERE operation_id=%s AND step_key=%s AND input_digest=%s AND state='running'",
                        (op["id"], step["step_key"], step["input_digest"]),
                    )
                    c.execute(
                        "UPDATE model_calls SET state='unknown',usage=coalesce(usage,'{}'::jsonb)||%s WHERE operation_id=%s AND state='reserved' AND usage->'reservation'->>'step_key'=%s",
                        (
                            Jsonb(
                                {"billing_state": "unknown", "failure_code": "WORKER_LEASE_EXPIRED"}
                            ),
                            op["id"],
                            step["step_key"],
                        ),
                    )
                    generation = step["dispatch_generation"] + 1
                    c.execute(
                        "UPDATE operation_steps SET dispatch_generation=%s WHERE operation_id=%s AND step_key=%s AND input_digest=%s",
                        (generation, op["id"], step["step_key"], step["input_digest"]),
                    )
                    dispatch_step(
                        c,
                        op["id"],
                        op["bid_id"],
                        step["step_key"],
                        step["input_digest"],
                        step["handler_version"],
                        dispatch["payload"]["payload_ref"],
                        generation,
                        topic,
                    )
        if once:
            return
        STOP.wait(5)
