"""Probe actual isolated infrastructure with synthetic bytes and capability LOGINs."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import boto3
import psycopg
from botocore.config import Config
from confluent_kafka.admin import AdminClient
from initialize_database import ROOT, local_configuration


def main() -> None:
    values = local_configuration()
    checks = []
    for role in ("api", "worker", "research_worker", "score_read"):
        with psycopg.connect(values[f"{role.upper()}_DATABASE_DSN"]) as connection:
            row = connection.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls, "
                "pg_has_role(current_user,%s,'MEMBER') FROM pg_roles WHERE rolname=current_user",
                (f"tender_{role}",),
            ).fetchone()
            if not row or any(row[:4]) or not row[4]:
                raise RuntimeError(f"Unexpected runtime LOGIN privileges: {role}")
            owned = connection.execute(
                "SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname IN ('design','retrieval') "
                "AND r.rolname=current_user)"
            ).fetchone()
            if not owned or owned[0]:
                raise RuntimeError(f"Runtime LOGIN owns domain tables: {role}")
            checks.append(f"{role}_nonowner_capability")
    with psycopg.connect(values["SCORE_READ_DATABASE_DSN"]) as connection:
        row = connection.execute(
            "SELECT has_table_privilege(current_user,'design.outcomes','SELECT')"
        ).fetchone()
        if not row or row[0]:
            raise RuntimeError("Formal scoring LOGIN can access official labels")
        checks.append("score_labels_forbidden")
    policy = json.loads((ROOT / "packages/contracts/policies/execution-policy.json").read_text())
    expected = {item["topic"] for item in policy["topic_bindings"].values()}
    expected.update([policy["domain_event_topic"], policy["dead_letter_topic"]])
    metadata = AdminClient({"bootstrap.servers": values["KAFKA_BOOTSTRAP_SERVERS"]}).list_topics(
        timeout=20
    )
    if any(
        name not in metadata.topics or len(metadata.topics[name].partitions) != 6
        for name in expected
    ):
        raise RuntimeError("Missing development Kafka topic partitions")
    checks.append("six_kafka_topics_with_six_partitions")
    client = boto3.client(
        "s3",
        endpoint_url=values["OBJECT_STORE_ENDPOINT"],
        aws_access_key_id=values["OBJECT_STORE_ACCESS_KEY"],
        aws_secret_access_key=values["OBJECT_STORE_SECRET_KEY"],
        region_name=values["OBJECT_STORE_REGION"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 1},
            request_checksum_calculation="when_required",
        ),
    )
    bucket = values["OBJECT_STORE_BUCKET"]
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise RuntimeError("Development bucket versioning not enabled")
    content = b"workbench synthetic readiness probe"
    key = f"readiness/{uuid4()}"
    version = None
    try:
        result = client.put_object(Bucket=bucket, Key=key, Body=content)
        version = result.get("VersionId")
        if not version or version == "null":
            raise RuntimeError("Versioned put returned no immutable object identity")
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version)
        if hashlib.sha256(response["Body"].read()).digest() != hashlib.sha256(content).digest():
            raise RuntimeError("Real object round trip byte digest mismatch")
        checks.append("s3_versioned_synthetic_byte_roundtrip")
    finally:
        if version:
            client.delete_object(Bucket=bucket, Key=key, VersionId=version)
    print(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "scope": "real local infrastructure probes; not application or production acceptance",
            }
        )
    )


if __name__ == "__main__":
    main()
