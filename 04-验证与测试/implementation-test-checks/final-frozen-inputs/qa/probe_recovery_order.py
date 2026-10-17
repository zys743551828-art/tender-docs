"""真实Kafka领取后控制旧计算，独立核验fence、未来重试及取消恢复顺序。"""

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

from apps.worker import engine
from packages.domain.runtime import DomainError, connection
from qa.helpers import ROOT, Client, ensure, key, members, pdf_bytes, upload, write_result


def wait_for(fn, label, seconds=70):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(label)


def main():
    out = Path(os.environ.get("QA_RECOVERY_OUTPUT", "qa/recovery-results"))
    out.mkdir(parents=True, exist_ok=True)
    result = dict(
        scope="实际非owner Worker/Kafka/S3的领取、控制旧计算与真实恢复；时间过期仅QA夹具注入",
        passed=False,
        checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    child = None
    marker = None
    stage = "初始化"
    try:
        ensure(os.environ["KAFKA_BOOTSTRAP_SERVERS"].endswith(":19094"), "只允许QA Kafka")
        pid = ROOT / ".local/qa-process-consume.pid"
        if pid.exists():
            try:
                os.kill(int(pid.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(0.5)
        client = Client(members()[0])
        bid = client.bid()
        result["bid_id"] = bid
        queued_tags = {}

        def start_held(label, transient=False, mode="hold-compute"):
            data = pdf_bytes(
                "Recovery QA municipal body "
                + label
                + " with original physical evidence and unique content."
            )
            batch = client.batch(bid)
            grant, accepted = upload(client, bid, batch, data)
            op = accepted["operation"]["id"]
            _, queued_headers, _ = client.call("GET", "/operations/" + op)
            queued_tags[op] = queued_headers["ETag"]
            path = out / (label + "-markers.jsonl")
            env = dict(
                os.environ,
                QA_FAULT_MODE=mode,
                QA_FAULT_OPERATION=op,
                QA_FAULT_MARKERS=str(path),
                QA_RELEASE_TRANSIENT="1" if transient else "0",
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "qa.fault_worker"],
                cwd=ROOT,
                env=env,
                stdout=(out / (label + "-worker.log")).open("w"),
                stderr=subprocess.STDOUT,
            )
            wait_for(lambda: path.exists() and path.read_text(), "真实Kafka Worker应进入受控旧计算")
            payload = json.loads(path.read_text().splitlines()[0])["message"]
            with connection(True) as c:
                receipt = c.execute(
                    "SELECT * FROM step_receipts WHERE operation_id=%s", (op,)
                ).fetchone()
            return (
                process,
                path,
                grant,
                op,
                payload,
                dict(owner=receipt["owner_token"], fence=receipt["fence"]),
                data,
            )

        def release_and_stop(process, path):
            path.with_suffix(".continue").touch()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)

        stage = "旧心跳与新代领取"
        child, marker, grant, op, message, old_lease, _ = start_held("old-fence")
        with connection(True) as c:
            c.execute(
                "UPDATE step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id=%s",
                (op,),
            )
        ensure(engine.renew(message, old_lease) is False, "已过期心跳在对账前也不得复活")
        engine.reconcile(once=True)
        ensure(engine.renew(message, old_lease) is False, "新代计划后真实心跳入口仍拒绝旧执行")
        with connection(True) as c:
            renew = c.execute(
                "SELECT design.renew_step(%s,%s,%s,%s,%s) AS allowed",
                (
                    op,
                    message["step_key"],
                    message["input_digest"],
                    old_lease["fence"],
                    old_lease["owner"],
                ),
            ).fetchone()["allowed"]
            plan = c.execute(
                "SELECT dispatch_generation FROM operation_steps WHERE operation_id=%s", (op,)
            ).fetchone()["dispatch_generation"]
        ensure(plan == 2 and renew is False, "新代调度后旧fence心跳必须拒绝，不能恢复旧lease")
        finish = engine.finish(
            message, old_lease, None, DomainError("QA_LATE_ERROR", "controlled old executor")
        )
        ensure(finish is False, "旧fence完成不得发布")
        marker.with_suffix(".continue").touch()
        _, done = client.poll(
            "/operations/" + op,
            lambda x: x["state"] in ["succeeded", "failed"],
            "新代真实完成",
            seconds=70,
        )
        ensure(done["state"] == "succeeded", "新代不能仅ACK后永久停滞")
        with connection(True) as c:
            receipt = c.execute(
                "SELECT fence,attempt_count,(SELECT count(*) FROM effect_receipts WHERE operation_id=%s) AS effects FROM step_receipts WHERE operation_id=%s",
                (op, op),
            ).fetchone()
        ensure(
            receipt["fence"] > old_lease["fence"] and receipt["effects"] == 1,
            "新执行权实际完成且领域效果一次",
        )
        result["old_fence"] = dict(
            operation_id=op,
            old_fence=old_lease["fence"],
            new_fence=receipt["fence"],
            old_renew=renew,
            old_finish=finish,
            effects=receipt["effects"],
        )
        result["checks"].append("真实旧计算过期→reconcile废fence→旧renew/finish拒绝→新代实际完成")
        release_and_stop(child, marker)
        child = None
        stage = "未来重试"
        child, marker, grant, op, message, lease, _ = start_held("future-retry", transient=True)
        marker.with_suffix(".continue").touch()

        def retry_ready():
            with connection(True) as c:
                return c.execute(
                    "SELECT p.dispatch_generation,s.state FROM operation_steps p JOIN step_receipts s USING(operation_id,step_key,input_digest) WHERE operation_id=%s AND s.state='failed'",
                    (op,),
                ).fetchone()

        before = wait_for(retry_ready, "实际瞬时失败应形成持久重试")
        with connection(True) as c:
            due = c.execute(
                "SELECT available_at FROM outbox WHERE operation_id=%s AND payload->>'generation'='2'",
                (op,),
            ).fetchone()["available_at"]
            count_before = c.execute(
                "SELECT count(*) AS n FROM outbox WHERE operation_id=%s", (op,)
            ).fetchone()["n"]
        ensure(due > datetime.now(timezone.utc), "本检查须处于已持久但尚未到期窗口")
        engine.reconcile(once=True)
        with connection(True) as c:
            after = c.execute(
                "SELECT dispatch_generation FROM operation_steps WHERE operation_id=%s", (op,)
            ).fetchone()["dispatch_generation"]
            count_after = c.execute(
                "SELECT count(*) AS n FROM outbox WHERE operation_id=%s", (op,)
            ).fetchone()["n"]
        ensure(
            before["dispatch_generation"] == after == 2 and count_before == count_after,
            "对账不得抢跑未到期重试或新增代次",
        )
        _, done = client.poll(
            "/operations/" + op,
            lambda x: x["state"] in ["succeeded", "failed"],
            "到期后实际重试",
            seconds=70,
        )
        ensure(done["state"] == "succeeded", "按原代次到期执行成功")
        result["future_retry"] = dict(
            operation_id=op,
            generation_before=before["dispatch_generation"],
            generation_after=after,
            outbox_before=count_before,
            outbox_after=count_after,
            due=due,
        )
        result["checks"].append("真实瞬时失败生成未来重试，对账不抢跑，原代次到期实际成功")
        release_and_stop(child, marker)
        child = None
        stage = "取消与无重传恢复"
        child, marker, grant, op, message, lease, data = start_held("cancel")
        cancel_path = "/operations/" + op + "/cancel"
        cancel_body = dict(reason="QA取消旧执行", cancel_pending_same_input=False)
        _, running_headers, running = client.call("GET", "/operations/" + op)
        running_tag = running_headers["ETag"]
        ensure(
            running["state"] == "running" and running_tag != queued_tags[op],
            "真实Kafka领取后Operation强ETag变化",
        )
        ensure(engine.renew(message, lease), "合法当前执行允许心跳")
        _, heartbeat_headers, _ = client.call("GET", "/operations/" + op)
        ensure(heartbeat_headers["ETag"] == running_tag, "合法心跳不导致无意义条件冲突")
        status, _, _ = client.call("POST", cancel_path, cancel_body)
        ensure(status == 428, "取消缺条件头明确拒绝")
        status, _, _ = client.call(
            "POST", cancel_path, cancel_body, headers={"If-Match": queued_tags[op]}
        )
        ensure(status == 412, "真实领取后的旧条件头明确拒绝")
        cancel_key = key()
        status, accepted_headers, cancel_accepted = client.call(
            "POST",
            cancel_path,
            cancel_body,
            headers={"If-Match": running_tag},
            action_key=cancel_key,
        )
        ensure(
            status == 200 and cancel_accepted["state"] == "cancel_requested",
            "取消先持久请求状态，不能瞬间假完成",
        )
        engine.reconcile(once=True)
        _, cancelled = client.poll(
            "/operations/" + op, lambda x: x["state"] == "cancelled", "实际取消终态"
        )
        status, replay_headers, replay = client.call(
            "POST",
            cancel_path,
            cancel_body,
            headers={"If-Match": running_tag},
            action_key=cancel_key,
        )
        ensure(
            status == 200
            and replay == cancel_accepted
            and replay_headers["ETag"] == accepted_headers["ETag"]
            and replay_headers["Idempotent-Replayed"] == "true",
            "终态后原键原条件重放原响应而不重新CAS",
        )
        _, terminal_headers, _ = client.call("GET", "/operations/" + op)
        status, _, _ = client.call(
            "POST",
            cancel_path,
            cancel_body,
            headers={"If-Match": terminal_headers["ETag"]},
            action_key=cancel_key,
        )
        ensure(status == 409, "同键更换条件身份拒绝")
        with connection() as c:
            audits = c.execute(
                "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='operation.cancel_requested' AND payload->>'operation_id'=%s",
                (bid, op),
            ).fetchone()["n"]
        ensure(audits == 1, "取消审计只写一次")
        result["checks"].append("真实HTTP条件取消428/412、心跳稳定ETag、原键原响应重放与一次审计")
        _, headers, u = client.call("GET", "/uploads/" + grant["upload_id"])
        ensure(
            u["state"] == "authorized" and u["document_version_id"] is None,
            "取消后保留原S3字节但没有有效原件",
        )
        with connection(True) as c:
            renew = c.execute(
                "SELECT design.renew_step(%s,%s,%s,%s,%s) AS allowed",
                (op, message["step_key"], message["input_digest"], lease["fence"], lease["owner"]),
            ).fetchone()["allowed"]
        ensure(
            renew is False
            and engine.finish(
                message, lease, None, DomainError("QA_CANCELLED_LATE", "controlled late result")
            )
            is False,
            "取消后旧心跳与旧完成均拒绝",
        )
        marker.with_suffix(".continue").touch()
        status, _, accepted = client.call(
            "POST",
            "/uploads/" + grant["upload_id"] + "/complete",
            dict(parts=[], declared_sha256=hashlib.sha256(data).hexdigest()),
            headers={"If-Match": headers["ETag"]},
        )
        ensure(
            status == 202 and accepted["operation"]["id"] != op, "同上传字节可显式重新核验为新操作"
        )
        _, verified = client.poll(
            "/uploads/" + grant["upload_id"],
            lambda x: x["state"] in ["verified", "quarantined"],
            "取消后无重传恢复",
            seconds=70,
        )
        ensure(verified["state"] == "verified", "恢复不需重新PUT字节")
        with connection(True) as c:
            old_effects = c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s", (op,)
            ).fetchone()["n"]
        ensure(old_effects == 0, "取消旧操作不登记有效领域效果")
        result["checks"].append("真实运行取消、旧发布拒绝、原字节保留及显式新操作恢复")
        release_and_stop(child, marker)
        child = None
        stage = "失联共享执行预算"
        child, marker, grant, op, message, lease, data = start_held(
            "lost-budget", mode="hold-every-generation"
        )
        observed = []
        for generation in [1, 2, 3]:

            def matching(expected_generation=generation):
                rows = [json.loads(line) for line in marker.read_text().splitlines()]
                return next(
                    (row for row in rows if row.get("generation") == expected_generation), None
                )

            held = wait_for(matching, "失联接管应真实领取当前代次")
            current_message = held["message"]
            with connection(True) as c:
                row = c.execute(
                    "SELECT * FROM step_receipts WHERE operation_id=%s", (op,)
                ).fetchone()
                ensure(row["attempt_count"] == generation, "失联接管共用递增attempt_count")
                old = dict(owner=row["owner_token"], fence=row["fence"])
                c.execute(
                    "UPDATE step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id=%s",
                    (op,),
                )
            engine.reconcile(once=True)
            with connection(True) as c:
                allowed = c.execute(
                    "SELECT design.renew_step(%s,%s,%s,%s,%s) AS allowed",
                    (
                        op,
                        current_message["step_key"],
                        current_message["input_digest"],
                        old["fence"],
                        old["owner"],
                    ),
                ).fetchone()["allowed"]
            ensure(allowed is False, "每次失联都废止原fence")
            marker.with_suffix(".generation-" + str(generation) + ".continue").touch()
            observed.append(
                dict(generation=generation, attempt_count=row["attempt_count"], fence=row["fence"])
            )
        _, failed = client.poll(
            "/operations/" + op,
            lambda x: x["state"] in ["failed", "succeeded"],
            "耗尽失联预算终态",
            seconds=15,
        )
        ensure(failed["state"] == "failed", "三次失联后必须失败，不得拿到第四次执行权")
        _, _, unavailable = client.call("GET", "/uploads/" + grant["upload_id"])
        ensure(
            unavailable["state"] in ["quarantined", "authorized"]
            and unavailable["document_version_id"] is None,
            "执行预算耗尽不得显示永久核验中或产生有效原件",
        )
        rows = [json.loads(line) for line in marker.read_text().splitlines()]
        ensure(len(rows) == 3, "真实Kafka不能触发第四次compute")
        result["lost_execution_budget"] = dict(
            operation_id=op,
            attempts=observed,
            final_state=failed["state"],
            upload_state=unavailable["state"],
        )
        result["checks"].append("三次实际失联共享预算，耗尽失败且无第四次远程计算/原件")
        release_and_stop(child, marker)
        child = None
        result["passed"] = True
    except Exception as e:
        result["failure"] = dict(
            stage=stage,
            type=type(e).__name__,
            message=str(e)
            if isinstance(e, AssertionError)
            else "受控恢复执行异常，详见QA日志，无口令/签名URL回显",
        )
    finally:
        if marker:
            marker.with_suffix(".continue").touch()
            for i in range(1, 11):
                marker.with_suffix(".generation-" + str(i) + ".continue").touch()
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write_result(out / "result.json", result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
