"""跨实际API进程重启的会话、领域对象和命令幂等持久性。"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from packages.domain.runtime import connection
from qa.helpers import ROOT, Client, ensure, key, members, write_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify"])
    args = parser.parse_args()
    private = ROOT / ".local/qa-persistence.json"
    cookies = ROOT / ".local/qa-persistence.cookies"
    if args.phase == "prepare":
        client = Client(members()[0])
        action_key = key()
        body = dict(title="QA持久化-" + key())
        status, _, bid = client.call("POST", "/bids", body, action_key=action_key)
        ensure(status == 201, "持久化项目创建")
        jar = http.cookiejar.MozillaCookieJar(str(cookies))
        for cookie in client.jar:
            jar.set_cookie(cookie)
        jar.save(ignore_discard=True, ignore_expires=True)
        cookies.chmod(0o600)
        state = dict(
            bid=bid,
            body=body,
            action_key=action_key,
            csrf_token=client.token,
            api_pid=(ROOT / ".local/qa-process-api.pid").read_text(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        private.write_text(json.dumps(state))
        private.chmod(0o600)
        print("已准备真实持久对象和私有会话；待独立重启QA API后verify")
        return 0
    result = dict(
        scope="实际API进程重启后的原会话、项目与命令持久性；非浏览器刷新模拟",
        passed=False,
        checks=[],
    )
    try:
        state = json.loads(private.read_text())
        after = (ROOT / ".local/qa-process-api.pid").read_text()
        ensure(state["api_pid"] != after, "必须实际换API进程，不能仅刷新页面")
        client = Client()
        jar = http.cookiejar.MozillaCookieJar(str(cookies))
        jar.load(ignore_discard=True, ignore_expires=True)
        client.jar = jar
        client.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        client.token = state["csrf_token"]
        status, _, current = client.call("GET", "/bids/" + state["bid"]["id"])
        ensure(status == 200 and current == state["bid"], "旧会话和已保存项目跨进程仍可读")
        result["checks"].append("实际重启后原Cookie会话与PG项目仍有效")
        status, h, replay = client.call(
            "POST", "/bids", state["body"], action_key=state["action_key"]
        )
        ensure(
            status == 201 and replay == state["bid"] and h.get("Idempotent-Replayed") == "true",
            "重启后同key仍返回原命令，不创建新项目",
        )
        with connection() as c:
            count = c.execute(
                "SELECT count(*) AS n FROM commands WHERE idempotency_key=%s",
                (state["action_key"],),
            ).fetchone()["n"]
        ensure(count == 1, "命令占位持久存在且仅一条")
        result["checks"].append("跨进程同key原结果重放且数据库命令只有一条")
        other = Client(members()[1])
        status, _, record = other.call("GET", "/bids/" + state["bid"]["id"])
        ensure(
            status == 200 and record["id"] == state["bid"]["id"], "其他登录成员也读取同一持久项目"
        )
        result["checks"].append("重启后其他成员仍可访问同一项目")
        result.update(
            passed=True,
            bid_id=state["bid"]["id"],
            api_pid_before=int(state["api_pid"]),
            api_pid_after=int(after),
            commands=count,
        )
    except Exception as e:
        result["failure"] = dict(
            type=type(e).__name__,
            message=str(e)
            if isinstance(e, AssertionError)
            else "持久化验收执行异常，不输出Cookie/CSRF",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_result(os.environ.get("QA_RESULT_PATH", "qa/persistence-result.json"), result)
    print(dict(passed=result["passed"], checks=len(result["checks"])))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
