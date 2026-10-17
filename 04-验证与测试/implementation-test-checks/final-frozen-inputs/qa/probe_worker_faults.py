"""真实Kafka Worker的PG提交/offset间崩溃、重复、退避和取消恢复验收。"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition

from packages.domain.runtime import connection
from qa.helpers import ROOT, Client, ensure, members, pdf_bytes, upload, write_result


def wait_until(fn, label, seconds=35):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError(label)


def main():
    out = Path(os.environ.get("QA_FAULT_OUTPUT", "qa/fault-results"))
    out.mkdir(parents=True, exist_ok=True)
    result = dict(
        scope="真实PG/Kafka/S3与实际Worker；故障注入仅在测试进程，非模型/生产多节点验收",
        passed=False,
        checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    active = None
    stage = "初始化"
    try:
        ensure(os.environ["KAFKA_BOOTSTRAP_SERVERS"].endswith(":19094"), "仅可使用独立QA Kafka")
        pid_file = ROOT / ".local/qa-process-consume.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(1)
        client = Client(members()[0])
        bid = client.bid()
        result["bid_id"] = bid

        def pending_upload(text):
            batch = client.batch(bid)
            grant, accepted = upload(client, bid, batch, pdf_bytes(text))
            return grant, accepted["operation"]["id"]

        def worker(mode, op, name):
            marker = out / (name + "-markers.jsonl")
            env = dict(
                os.environ, QA_FAULT_MODE=mode, QA_FAULT_OPERATION=op, QA_FAULT_MARKERS=str(marker)
            )
            log = (out / (name + "-worker.log")).open("w")
            return subprocess.Popen(
                [sys.executable, "-m", "qa.fault_worker"], cwd=ROOT, env=env, stdout=log, stderr=log
            ), marker

        stage = "PG提交后offset前崩溃"
        grant, op = pending_upload(
            "Offset recovery synthetic municipal body with immutable original evidence."
        )
        active, marker = worker("before-offset-exit", op, "offset")
        code = active.wait(timeout=70)
        active = None
        ensure(code == 73, "Worker确在PG提交后/offset提交前退出")
        point = json.loads(marker.read_text().splitlines()[-1])
        part, offset = point["partition"], point["offset"]
        with connection() as c:
            before = c.execute(
                "SELECT state,(SELECT count(*) FROM effect_receipts WHERE operation_id=operations.id) AS effects FROM operations WHERE id=%s",
                (op,),
            ).fetchone()
            doc = c.execute(
                "SELECT verified_version_id FROM uploads WHERE id=%s", (grant["upload_id"],)
            ).fetchone()["verified_version_id"]
        ensure(
            before["state"] == "succeeded" and before["effects"] == 1 and doc,
            "崩溃前PG领域效果已提交一次",
        )
        observer = Consumer(
            {
                "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
                "group.id": "tender-document-worker-v1",
                "enable.auto.commit": False,
            }
        )
        position = observer.committed(
            [TopicPartition("tender.task.document.v1", part)], timeout=10
        )[0].offset
        ensure(position <= offset, "崩溃时原消息offset尚未提交")
        active = subprocess.Popen(
            [sys.executable, "-m", "apps.worker", "consume"],
            cwd=ROOT,
            stdout=(out / "replay-worker.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        wait_until(
            lambda: observer.committed(
                [TopicPartition("tender.task.document.v1", part)], timeout=5
            )[0].offset
            >= offset + 1,
            "重启后真实offset推进",
            seconds=70,
        )
        observer.close()
        with connection() as c:
            after = c.execute(
                "SELECT (SELECT count(*) FROM effect_receipts WHERE operation_id=%s) AS effects,(SELECT verified_version_id FROM uploads WHERE id=%s) AS version",
                (op, grant["upload_id"]),
            ).fetchone()
        ensure(after["effects"] == 1 and after["version"] == doc, "重放没有第二份effect/原件版本")
        result["offset_recovery"] = dict(
            operation_id=op,
            partition=part,
            message_offset=offset,
            committed_before=position,
            effects_after=after["effects"],
        )
        result["checks"].append("实际PG提交后offset前进程退出，重启重放且领域效果一次")
        active.terminate()
        active.wait(timeout=10)
        active = None
        stage = "持久重试与退避"
        _, op = pending_upload(
            "Transient dependency retry municipal body with actual S3 original bytes."
        )
        active, marker = worker("transient-two", op, "retry")
        client.poll(
            "/operations/" + op,
            lambda x: x["state"] in ["succeeded", "failed"],
            "暂时错误恢复",
            seconds=35,
        )
        active.terminate()
        active.wait(timeout=10)
        active = None
        attempts = [json.loads(line) for line in marker.read_text().splitlines()]
        with connection() as c:
            state = c.execute("SELECT state FROM operations WHERE id=%s", (op,)).fetchone()["state"]
            retries = c.execute(
                "SELECT payload->>'generation' AS generation,available_at,payload->>'created_at' AS created_at FROM outbox WHERE operation_id=%s ORDER BY available_at",
                (op,),
            ).fetchall()
            counts = c.execute(
                "SELECT attempt_count,(SELECT count(*) FROM effect_receipts WHERE operation_id=%s) AS effects FROM step_receipts WHERE operation_id=%s",
                (op, op),
            ).fetchone()
        ensure(
            state == "succeeded" and len(attempts) == 3 and counts["effects"] == 1,
            "两次暂时错误后三次内成功且effect一次",
        )
        planned = {int(row["generation"]): row["available_at"] for row in retries}
        early = [x for x in attempts if datetime.fromisoformat(x["at"]) < planned[x["generation"]]]
        result["retry_observations"] = dict(
            attempts=attempts, outbox=retries, started_before_own_outbox_due=early
        )
        # 还须检查下一代不能由reconcile绕过已经持久的退避意图。
        ensure(not early, "执行不能早于本代持久available_at")
        ensure([x["generation"] for x in attempts] == [1, 2, 3], "对账不得跳过未到期的持久重试代次")
        ensure(
            attempts[2]["monotonic"] - attempts[1]["monotonic"] >= 7.8,
            "第三次调用须尊重8秒持久退避",
        )
        result["checks"].append("两次瞬时失败持久退避2/8秒后实际成功，重试与对账不重复派发")
        result["passed"] = True
    except Exception as error:
        result["failure"] = dict(
            stage=stage,
            type=type(error).__name__,
            message=str(error)
            if isinstance(error, AssertionError)
            else "实际故障验收未完成，见该次私有QA日志",
        )
    finally:
        if active and active.poll() is None:
            active.terminate()
            try:
                active.wait(timeout=10)
            except subprocess.TimeoutExpired:
                active.kill()
                active.wait()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write_result(out / "result.json", result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
