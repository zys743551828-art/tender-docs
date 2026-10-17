"""启动并标识独立QA进程，控制测试所属进程而非共享开发环境。"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("mode", choices=["api", "relay", "consume", "reconcile"])
a = p.parse_args()
if not os.environ.get("WORKBENCH_ENV_FILE", "").endswith("qa.env"):
    raise SystemExit("需要独立qa.env")
path = ROOT / ".local" / f"qa-process-{a.mode}.pid"
path.write_text(str(os.getpid()))
path.chmod(0o600)
print("QA process", a.mode, "PID", os.getpid(), flush=True)
command = (
    [
        sys.executable,
        "-m",
        "uvicorn",
        "apps.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8801",
        "--no-access-log",
    ]
    if a.mode == "api"
    else [sys.executable, "-m", "apps.worker", a.mode]
)
os.execvpe(command[0], command, os.environ)
