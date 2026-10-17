"""Launch exactly one fixture role; the business process receives one role DSN."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from safety import MARKER, MODES, configuration, environment, verify_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("api", "worker", "publisher", "seed", "control"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--response", choices=sorted(MODES), default="hold")
    parser.add_argument("--scope", action="append", choices=("global", "project"))
    arguments = parser.parse_args()
    value = configuration(arguments.config)
    binding = verify_candidate(value)
    evidence = Path(value["evidence"])
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    if arguments.mode == "control":
        control = {
            "marker": MARKER,
            "mode": arguments.response,
            "scopes": arguments.scope or ["global"],
        }
        temporary = Path(value["control"] + ".temporary")
        temporary.write_text(json.dumps(control))
        temporary.chmod(0o600)
        temporary.replace(value["control"])
        print(
            {
                "fixture_mode": arguments.response,
                "scopes": control["scopes"],
                "real_provider_attempts": 0,
            }
        )
        return
    safe = {
        key: item
        for key, item in value.items()
        if key not in {"api_dsn", "worker_dsn", "session_secret", "csrf_secret"}
    }
    safe.update(binding)
    safe["mode"] = arguments.mode
    child_path = evidence / (arguments.mode + "-private-input.json")
    descriptor = os.open(child_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(safe, handle)
    child = Path(__file__).with_name("process.py")
    # Fixed interpreter and child argv, never a shell.
    os.execve(  # nosec B606
        sys.executable,
        [sys.executable, "-I", str(child), str(child_path)],
        environment(value, arguments.mode),
    )


if __name__ == "__main__":
    main()
