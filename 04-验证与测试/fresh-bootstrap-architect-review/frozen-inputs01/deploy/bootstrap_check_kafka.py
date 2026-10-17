"""Start only a fresh check Kafka broker, using a separate project, volume and cluster ID."""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess  # nosec B404
from pathlib import Path

from deploy.bootstrap_fresh import ROOT, BootstrapError, private_configuration


def start(profile):
    values = private_configuration(profile)
    project = values.get("COMPOSE_PROJECT_NAME", "")
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{2,50}", project)
        or values.get("KAFKA_EXTERNAL_PORT") != "19096"
        or values.get("KAFKA_BOOTSTRAP_SERVERS") != "127.0.0.1:19096"
    ):
        raise BootstrapError("DEDICATED_CHECK_BROKER_REQUIRED")
    environment = dict(os.environ)
    environment.update(values)

    executable = shutil.which("docker")
    if executable is None:
        raise BootstrapError("DOCKER_EXECUTABLE_REQUIRED")

    def docker(args):
        return subprocess.run(
            [executable, *args],
            env=environment,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603

    if docker(
        ["ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
    ).stdout.strip():
        raise BootstrapError("REFUSING_EXISTING_COMPOSE_PROJECT")
    if docker(
        ["volume", "ls", "-q", "--filter", "name=^" + project + "_kafka_data$"]
    ).stdout.strip():
        raise BootstrapError("REFUSING_EXISTING_KAFKA_VOLUME")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 19096))
    cluster = values.get("KAFKA_CLUSTER_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", cluster):
        raise BootstrapError("FRESH_CLUSTER_ID_REQUIRED")
    override = ROOT / ".local" / (project + "-kafka.override.json")
    descriptor = os.open(override, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"services": {"kafka": {"environment": {"CLUSTER_ID": cluster}}}}, stream)
    docker(
        [
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(Path(profile).resolve()),
            "-f",
            str(ROOT / "deploy/compose.external.yaml"),
            "-f",
            str(override),
            "up",
            "-d",
            "--no-deps",
            "kafka",
        ]
    )
    return {
        "project": project,
        "container": project + "-kafka-1",
        "volume": project + "_kafka_data",
        "bootstrap": "127.0.0.1:19096",
        "cluster_id": cluster,
        "only_kafka_started": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(start(args.init_file)))
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": str(error)
                    if isinstance(error, BootstrapError)
                    else type(error).__name__,
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
