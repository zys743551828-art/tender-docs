"""external浏览器PDF验收入口；不启动API/Worker，不读取provider密钥或发chat。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=Path, default=ROOT / ".local/external-test-members.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # 此进程仅需要测试成员，不加载external.env或providers.env。
    if args.members.resolve().parent != (ROOT / ".local").resolve():
        raise SystemExit("测试成员文件须在本工程私有.local目录")
    if not args.members.exists() or args.members.stat().st_mode & 0o077:
        raise SystemExit("not_ready：尚无权限600的external测试成员文件")
    members = json.loads(args.members.read_text())
    if (
        not isinstance(members, list)
        or len(members) < 1
        or any(not m.get("login") or not m.get("password") for m in members)
    ):
        raise SystemExit("not_ready：external临时测试成员格式不合法")
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "DEEPSEEK_API_KEY",
            "OBJECT_STORE_ACCESS_KEY",
            "OBJECT_STORE_SECRET_KEY",
            "OWNER_DATABASE_DSN",
            "API_DATABASE_DSN",
            "WORKER_DATABASE_DSN",
            "RESEARCH_WORKER_DATABASE_DSN",
            "SCORE_READ_DATABASE_DSN",
        }
    }
    child_env.update(
        QA_MEMBERS_FILE=str(args.members.resolve()),
        QA_WEB_URL="http://127.0.0.1:5175/",
        QA_BROWSER_OUTPUT=str(args.output.resolve()),
    )
    os.execvpe(sys.executable, [sys.executable, "-m", "qa.probe_browser"], child_env)


if __name__ == "__main__":
    main()
