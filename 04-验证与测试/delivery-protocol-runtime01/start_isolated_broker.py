"""Create the dedicated delivery-protocol broker; refuse any existing target."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
NAME = "tender-workbench-protocol01-kafka"
VOLUME = "tender-workbench-protocol01_kafka_data"
IMAGE = "apache/kafka@sha256:5cc2a2fd93fa2687b44015eee04fb2c3edd9e526bd64bf8bec5ff1e268772e0e"

def main():
    env = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError("Docker executable missing")
    def docker(args):
        return subprocess.run([executable, "--host=unix:///var/run/docker.sock", *args], env=env, capture_output=True, text=True, check=True)
    if docker(["ps", "-aq", "--filter", "name=^/" + NAME + "$"]).stdout.strip():
        raise RuntimeError("Refusing existing container")
    if docker(["volume", "ls", "-q", "--filter", "name=^" + VOLUME + "$"]).stdout.strip():
        raise RuntimeError("Refusing existing volume")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 19097))
    docker(["image", "inspect", IMAGE])  # Local cached image only.
    cluster = __import__("base64").urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")
    docker(["volume", "create", "--label", "tender.scope=delivery-protocol01", VOLUME])
    settings = {
        "CLUSTER_ID": cluster, "KAFKA_NODE_ID": "1", "KAFKA_PROCESS_ROLES": "broker,controller",
        "KAFKA_LISTENERS": "INTERNAL://:9092,EXTERNAL://:19097,CONTROLLER://:9093",
        "KAFKA_ADVERTISED_LISTENERS": "INTERNAL://localhost:9092,EXTERNAL://127.0.0.1:19097",
        "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT",
        "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL", "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
        "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093",
        "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1", "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
        "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1", "KAFKA_LOG_DIRS": "/var/lib/kafka/data",
        "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "false", "KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m"
    }
    args = ["run", "--detach", "--pull=never", "--name", NAME, "--label", "tender.scope=delivery-protocol01",
            "--memory", "1536m", "--cpus", "1", "--publish", "127.0.0.1:19097:19097",
            "--volume", VOLUME + ":/var/lib/kafka/data",
            "--health-cmd", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 >/dev/null 2>&1",
            "--health-interval", "10s", "--health-timeout", "8s", "--health-retries", "20"]
    for key, value in settings.items():
        args.extend(["--env", key + "=" + value])
    args.append(IMAGE)
    container_id = docker(args).stdout.strip()
    receipt = {"container_id": container_id, "container": NAME, "volume": VOLUME, "image": IMAGE,
               "cluster_id": cluster, "bootstrap": "127.0.0.1:19097", "paid_consumers_started": False,
               "existing_environments_changed": False, "purpose": "isolated new protocol integration"}
    with (ROOT / "broker-creation.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt))

if __name__ == "__main__":
    main()
