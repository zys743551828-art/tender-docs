"""真实OSS已写后并发命令胜出：临时API屏障＋实际PG/SDK/HTTP，零模型。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from packages.domain import storage
from packages.domain.runtime import connection
from qa.helpers import Client, ensure, members, pdf_bytes, write_result
from qa.provider_proxy_probe import bytes_call


def main():
    out = Path(os.environ["QA_PUBLICATION_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    result = dict(
        scope="受控QA临时API8803：实际OSS写后屏障＋8802并发命令＋真实PG短锁公布校验；非未插桩时序",
        passed=False,
        checks=[],
        model_requests=0,
        races=[],
    )
    process = None
    stage = "初始化"
    targets = []
    try:
        ensure(
            Path(os.environ["WORKBENCH_ENV_FILE"]).name == "external-release.env", "只允许release"
        )
        client = Client(members()[0])
        bid = client.bid()
        result["bid_id"] = bid
        data = pdf_bytes("Controlled external publication race after actual OSS store.")
        digest = hashlib.sha256(data).hexdigest()
        for command in ["authorizations", "complete", "abort"]:
            batch = client.batch(bid)
            status, _, grant = client.call(
                "POST",
                f"/bids/{bid}/uploads",
                dict(
                    batch_id=batch,
                    role="body",
                    filename="publication-race.pdf",
                    byte_size=len(data),
                    declared_sha256=digest,
                    target={"kind": "new_file"},
                ),
            )
            ensure(status == 200, "创建合法竞态授权")
            targets.append(
                dict(command=command, upload=grant["upload_id"], url=grant["upload_url"])
            )
        environment = dict(
            os.environ,
            QA_TRANSFER_TARGETS=json.dumps([x["upload"] for x in targets]),
            QA_TRANSFER_BARRIERS=str(out.resolve()),
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "qa.provider_proxy_fault_api"],
            env=environment,
            stdout=(out / "fault-api.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        result["controlled_api_pid"] = process.pid
        until = time.monotonic() + 20
        while True:
            ensure(process.poll() is None, "受控API应保持运行")
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8803/health/ready", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            ensure(time.monotonic() < until, "受控API未启动")
            time.sleep(0.1)
        with urllib.request.urlopen("http://127.0.0.1:8803/openapi.json", timeout=5) as response:
            (out / "controlled-api-openapi.json").write_bytes(response.read())
        with ThreadPoolExecutor(max_workers=1) as pool:
            for target in targets:
                command = target["command"]
                upload = target["upload"]
                stage = command + "实际云写后窗口"
                parsed = urlsplit(target["url"])
                controlled_url = urlunsplit(
                    (parsed.scheme, "127.0.0.1:8803", parsed.path, parsed.query, "")
                )
                with connection() as c:
                    before = c.execute(
                        "SELECT staging_key FROM uploads WHERE id=%s", (upload,)
                    ).fetchone()["staging_key"]
                future = pool.submit(bytes_call, client, controlled_url, data)
                marker = out / (upload + ".stored.json")
                until = time.monotonic() + 35
                while not marker.exists():
                    ensure(not future.done(), "屏障到达前不应完成HTTP传输")
                    ensure(time.monotonic() < until, "真实云写未到达屏障")
                    time.sleep(0.02)
                stored = json.loads(marker.read_text())
                candidate = stored["candidate_key"]
                ensure(stored["actual_oss_write_completed"], "必须真实云写成功后才竞争")
                _, headers, _ = client.call("GET", f"/uploads/{upload}")
                payload = (
                    {"part_numbers": [1]}
                    if command == "authorizations"
                    else (
                        {"parts": [], "declared_sha256": digest} if command == "complete" else None
                    )
                )
                status, _, _ = client.call(
                    "POST",
                    f"/uploads/{upload}/{command}",
                    payload,
                    headers={"If-Match": headers["ETag"]},
                )
                ensure(status == (202 if command == "complete" else 200), "原API并发命令真实成功")
                (out / (upload + ".continue")).touch()
                stream_status, _, _ = future.result(timeout=20)
                ensure(stream_status in [403, 409], "已写云对象的旧传输仍须被短锁复核拒绝")
                with connection() as c:
                    row = c.execute(
                        "SELECT state,staging_key,verified_version_id FROM uploads WHERE id=%s",
                        (upload,),
                    ).fetchone()
                    references = c.execute(
                        "SELECT (SELECT count(*) FROM uploads WHERE staging_key=%s)+(SELECT count(*) FROM blobs WHERE object_key=%s) AS n",
                        (candidate, candidate),
                    ).fetchone()["n"]
                ensure(
                    row["staging_key"] == before
                    and row["verified_version_id"] is None
                    and references == 0,
                    "旧候选不能换定位或被任何有效记录引用",
                )
                # 仅删除本次明确候选，先查无uploads/blobs引用，不按bucket或注册表批量清理。
                native = storage.client()
                removed = native.native.delete_object(native.prefix + candidate)
                ensure(removed.status == 204, "本次无引用孤立候选已定点清理")
                result["races"].append(
                    dict(
                        command=command,
                        upload_id=upload,
                        command_status=status,
                        old_stream_status=stream_status,
                        actual_oss_write=True,
                        staging_key_unchanged=True,
                        valid_document_versions=0,
                        candidate_references=references,
                        cleanup_status=removed.status,
                    )
                )
                result["checks"].append(
                    command + "：真实OSS写后竞争胜出，旧公布拒绝、原key不变、无引用候选清理"
                )
        result["passed"] = True
    except Exception as error:
        result["failure"] = dict(
            stage=stage,
            type=type(error).__name__,
            message=str(error)
            if isinstance(error, AssertionError)
            else "受控发布竞争未完成，无URL/秘密回显",
        )
    finally:
        for item in targets:
            (out / (item["upload"] + ".continue")).touch()
        if process and process.poll() is None:
            command_line = Path(f"/proc/{process.pid}/cmdline").read_bytes()
            ensure(
                b"qa.provider_proxy_fault_api" in command_line, "只允许停止本次创建的故障API进程"
            )
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        result["sources"] = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                Path("qa/provider_proxy_fault_api.py"),
                Path("apps/api/object_transfer.py"),
            ]
        }
        write_result(out / "result.json", result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
