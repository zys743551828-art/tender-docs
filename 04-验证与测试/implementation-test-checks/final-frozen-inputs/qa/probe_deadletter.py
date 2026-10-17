"""独立实际Kafka死信观测，不创建合成内部任务。"""

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition

from apps.worker import engine
from packages.domain.runtime import connection
from qa.helpers import ensure, write_result


def main():
    prior = Path(os.environ["QA_RECOVERY_RESULT"])
    out = Path(os.environ["QA_RESULT_PATH"])
    result = dict(scope="实际失联预算耗尽后的Kafka死信及终态拒绝领取补验", passed=False, checks=[])
    observer = None
    try:
        ensure(os.environ["KAFKA_BOOTSTRAP_SERVERS"].endswith(":19094"), "仅QA Kafka")
        op = json.loads(prior.read_text())["lost_execution_budget"]["operation_id"]
        observer = Consumer(
            {
                "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
                "group.id": "qa-deadletter-observer-" + uuid4().hex,
                "enable.auto.commit": False,
            }
        )
        topic = "tender.task.dead-letter.v1"
        parts = observer.list_topics(topic, timeout=10).topics[topic].partitions
        observer.assign([TopicPartition(topic, i, OFFSET_BEGINNING) for i in parts])
        messages = []
        end = time.monotonic() + 12
        while time.monotonic() < end:
            message = observer.poll(0.2)
            if message is None or message.error():
                continue
            payload = json.loads(message.value())
            if payload.get("operation_id") == op:
                messages.append(payload)
                break
        ensure(len(messages) == 1, "实际Kafka必须可读到该操作死信")
        with connection(True) as c:
            receipt = c.execute(
                "SELECT attempt_count,state FROM step_receipts WHERE operation_id=%s", (op,)
            ).fetchone()
            intents = c.execute(
                "SELECT count(*) AS n,bool_and(published_at IS NOT NULL) AS published FROM outbox WHERE operation_id=%s AND topic=%s",
                (op, topic),
            ).fetchone()
            terminal = c.execute(
                "SELECT result FROM effect_receipts WHERE operation_id=%s", (op,)
            ).fetchall()
            upload = c.execute(
                "SELECT state,verified_version_id FROM uploads WHERE id=%s",
                (messages[0]["payload_ref"],),
            ).fetchone()
        ensure(intents["n"] == 1 and intents["published"], "死信意图恰一次且已真实发布")
        ensure(
            engine.acquire(messages[0]) is None, "耗尽操作即使再次尝试领取也不能取得第四次执行权"
        )
        with connection(True) as c:
            after = c.execute(
                "SELECT attempt_count,state FROM step_receipts WHERE operation_id=%s", (op,)
            ).fetchone()
        ensure(receipt == after and after["attempt_count"] == 3, "拒绝领取不增预算")
        ensure(
            len(terminal) == 1
            and terminal[0]["result"]["error_code"] == "WORKER_ATTEMPTS_EXHAUSTED"
            and terminal[0]["result"]["result_ref"] is None
            and upload["state"] == "quarantined"
            and upload["verified_version_id"] is None,
            "失败终态收据只记录预算耗尽，不产生有效原件",
        )
        result.update(
            passed=True,
            operation_id=op,
            deadletter_intents=intents["n"],
            actual_kafka_message=True,
            attempt_count=3,
            terminal_failure_receipts=len(terminal),
            valid_document_versions=0,
        )
        result["checks"].append(
            "三次失联耗尽的死信真实到达Kafka，意图一次，终态重复领取拒绝且无有效原件"
        )
    except Exception as error:
        result["failure"] = dict(
            type=type(error).__name__,
            message=str(error) if isinstance(error, AssertionError) else "死信独立观测未完成",
        )
    finally:
        if observer:
            observer.close()
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result["recovery_result_sha256"] = hashlib.sha256(prior.read_bytes()).hexdigest()
        write_result(out, result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
