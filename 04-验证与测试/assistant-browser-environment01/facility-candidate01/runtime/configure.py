"""Prepare a private fixture configuration from PM's explicitly dedicated resources."""

import argparse
import json
import os
import shlex
from pathlib import Path
from uuid import UUID

from safety import MARKER, verify_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.profile, args.members):
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("Private regular PM resource files required")
    profile = {}
    for line in args.profile.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, raw = line.split("=", 1)
            parsed = shlex.split(raw, comments=True)
            if len(parsed) > 1:
                raise ValueError("Malformed private profile")
            profile[key.strip()] = parsed[0] if parsed else ""
    if any(
        profile.get(key)
        for key in (
            "OWNER_DATABASE_DSN",
            "DEEPSEEK_API_KEY",
            "OBJECT_STORE_ACCESS_KEY",
            "OBJECT_STORE_SECRET_KEY",
            "KAFKA_BOOTSTRAP_SERVERS",
        )
    ):
        raise ValueError("PM fixture profile unexpectedly contains external capabilities")
    members = json.loads(args.members.read_text())
    if isinstance(members, dict):
        members = members["members"]
    ids = [str(UUID(member["id"])) for member in members]
    if len(set(ids)) != 2:
        raise ValueError("Exactly two dedicated members required")
    evidence = Path(
        "/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/fixture01"
    )
    objects = Path("/workspace/tender-workbench/.local/assistant_ui01-objects")
    value = {
        "marker": MARKER,
        "candidate": "/workspace/tender-docs/04-验证与测试/task-drafts-development-checks/candidate03/candidate.json",
        "runtime_root": "/workspace/tender-docs/04-验证与测试/task-drafts-development-checks/candidate03/runtime",
        "database_name": "tender_checks_assistant_ui01",
        "origin": "http://127.0.0.1:5184",
        "port": 8816,
        "objects": str(objects),
        "control": "/workspace/tender-workbench/.local/assistant_ui01-control.json",
        "evidence": str(evidence),
        "member_ids": ids,
        "api_dsn": profile["API_DATABASE_DSN"],
        "worker_dsn": profile["WORKER_DATABASE_DSN"],
        "session_secret": profile["SESSION_SECRET"],
        "csrf_secret": profile["CSRF_SECRET"],
    }
    binding = verify_candidate(value)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle)
    descriptor = os.open(value["control"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"marker": MARKER, "mode": "hold", "scopes": ["global"]}, handle)
    print({"configured": True, "member_count": len(ids), "default": "hold", **binding})


if __name__ == "__main__":
    main()
