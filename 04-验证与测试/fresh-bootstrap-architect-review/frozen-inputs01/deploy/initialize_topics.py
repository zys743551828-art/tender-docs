"""Create and verify the development Kafka topics from the reviewed policy."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic
from initialize_database import local_configuration

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap")
    arguments = parser.parse_args()
    policy = json.loads((ROOT / "packages/contracts/policies/execution-policy.json").read_text())
    names = [entry["topic"] for entry in policy["topic_bindings"].values()]
    names.extend([policy["domain_event_topic"], policy["dead_letter_topic"]])
    admin = AdminClient(
        {
            "bootstrap.servers": arguments.bootstrap
            or local_configuration()["KAFKA_BOOTSTRAP_SERVERS"]
        }
    )
    existing = admin.list_topics(timeout=20).topics
    resources = []
    for name in names:
        days = (
            30
            if name == policy["dead_letter_topic"]
            else 14
            if name == policy["domain_event_topic"]
            else 7
        )
        configuration = {
            "cleanup.policy": "delete",
            "retention.ms": str(days * 86400000),
            "min.insync.replicas": "1",
            "max.message.bytes": "262144",
        }
        if name not in existing:
            admin.create_topics(
                [NewTopic(name, num_partitions=6, replication_factor=1, config=configuration)]
            )[name].result(timeout=30)
        else:
            metadata = existing[name]
            if len(metadata.partitions) != 6 or any(
                len(p.replicas) != 1 for p in metadata.partitions.values()
            ):
                raise RuntimeError(f"Development topic topology mismatch: {name}")
        resources.append((ConfigResource(ConfigResource.Type.TOPIC, name), configuration))
    for resource, wanted in resources:
        deadline = time.monotonic() + 20
        while True:
            try:
                actual = admin.describe_configs([resource])[resource].result(timeout=10)
                break
            except KafkaException as error:
                if (
                    error.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.2)
        for key, value in wanted.items():
            if actual[key].value != value:
                raise RuntimeError(
                    f"Development topic configuration mismatch: {resource.name}:{key}"
                )
    print(
        json.dumps({"topics_verified": len(names), "scope": "development single broker metadata"})
    )


if __name__ == "__main__":
    main()
