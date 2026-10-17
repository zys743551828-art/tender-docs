"""仅QA临时API的真实OSS写后屏障；不改应用代码，不启动Worker。"""

import json
import os
import time
from pathlib import Path

import uvicorn

from apps.api import object_transfer
from apps.api.main import app

if not os.environ.get("WORKBENCH_ENV_FILE", "").endswith("external-release.env"):
    raise SystemExit("只允许external-release测试环境")
targets = set(json.loads(os.environ["QA_TRANSFER_TARGETS"]))
directory = Path(os.environ["QA_TRANSFER_BARRIERS"])
directory.mkdir(parents=True, exist_ok=True)
original = object_transfer._store_file


def controlled(file, key):
    result = original(file, key)
    upload = key.split("/")[2]
    if upload in targets:
        marker = directory / (upload + ".stored.json")
        marker.write_text(
            json.dumps(
                {"upload_id": upload, "candidate_key": key, "actual_oss_write_completed": True}
            )
        )
        until = time.monotonic() + 45
        while not (directory / (upload + ".continue")).exists():
            if time.monotonic() > until:
                raise RuntimeError("QA publication barrier expired")
            time.sleep(0.02)
    return result


object_transfer._store_file = controlled
uvicorn.run(app, host="127.0.0.1", port=8803, access_log=False, log_level="warning")
