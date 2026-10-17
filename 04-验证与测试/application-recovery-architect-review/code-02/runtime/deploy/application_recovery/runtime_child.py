"""Isolated API acceptance child. Reads no infrastructure profile or backup credential."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# -I ignores cwd/PYTHONPATH. The application-recovery owner path is explicit and trusted.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from deploy.application_recovery import application
    from deploy.application_recovery import security as s

    s.validate_api_environment(__import__("os").environ)
    raw = sys.stdin.buffer.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise s.RecoveryError("API_CHILD_INPUT_LIMIT")
    payload = json.loads(raw)
    if set(payload) != {"backend", "fixture", "credential"} or set(payload["credential"]) != {
        "login_name",
        "password",
        "old_cookie",
    }:
        raise s.RecoveryError("API_CHILD_INPUT_NOT_SINGLE_PURPOSE")
    result = application.runtime_check(
        {"backend": payload["backend"]}, payload["fixture"], payload["credential"]
    )
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Error text, SQL, responses and traceback may contain private member/provider data.
        print('{"status":"failed","error":"ISOLATED_API_CHECK_FAILED"}')
        raise SystemExit(1) from None
