"""Validate isolated local deployment boundaries without printing expanded secrets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--profile", choices=("local", "external"), default="local")
    arguments = parser.parse_args()
    external = arguments.profile == "external"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(ROOT / "deploy" / ("compose.external.yaml" if external else "compose.yaml")),
            "--env-file",
            str(arguments.env_file.resolve()),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    configuration = json.loads(result.stdout)
    services = configuration["services"]
    expected = {"postgres", "kafka"} if external else {"postgres", "kafka", "object-store"}
    if set(services) != expected:
        raise SystemExit("Unexpected development service set")
    ports = []
    for name, service in services.items():
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", service["image"]):
            raise SystemExit(f"Unpinned image: {name}")
        if service.get("privileged") or service.get("network_mode") == "host":
            raise SystemExit(f"Unexpected host privileges: {name}")
        if not service.get("mem_limit"):
            raise SystemExit(f"Missing memory limit: {name}")
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1":
                raise SystemExit(f"Nonlocal development port: {name}")
            ports.append(f"{name}:{port['published']}")
        for volume in service.get("volumes", []):
            if "docker.sock" in str(volume):
                raise SystemExit("Docker socket must not be mounted")
    if not external:
        store_mounts = services["object-store"].get("volumes", [])
        private_mount = [mount for mount in store_mounts if mount["type"] == "bind"]
        if len(private_mount) != 1 or not private_mount[0].get("read_only"):
            raise SystemExit("Storage identity file must be a single read-only bind")
    print(
        json.dumps(
            {
                "passed": True,
                "project": configuration["name"],
                "services": len(services),
                "local_ports": ports,
                "scope": "local infrastructure configuration; no business acceptance",
            }
        )
    )


if __name__ == "__main__":
    main()
