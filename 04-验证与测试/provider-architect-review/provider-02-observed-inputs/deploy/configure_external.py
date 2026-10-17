"""Create an isolated, empty-database profile using private provider credentials."""

from __future__ import annotations

import argparse
import os
import secrets
import shlex
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from initialize_database import ROOT, file_configuration


def configure(provider_file: Path, destination: Path) -> None:
    provider = file_configuration(provider_file)
    required = (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "OBJECT_STORE_ENDPOINT",
        "OBJECT_STORE_BUCKET",
        "OBJECT_STORE_BUCKET_URL",
        "OBJECT_STORE_REGION",
        "OBJECT_STORE_ACCESS_KEY",
        "OBJECT_STORE_SECRET_KEY",
    )
    if any(not provider.get(key) for key in required):
        raise ValueError("Incomplete private provider configuration")
    for key in ("DEEPSEEK_BASE_URL", "OBJECT_STORE_ENDPOINT", "OBJECT_STORE_BUCKET_URL"):
        endpoint = urlsplit(provider[key])
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
            raise ValueError("External provider endpoints require HTTPS without userinfo")
    if provider.get("OBJECT_STORE_BACKEND") != "oss":
        raise ValueError("External profile requires the native OSS adapter")
    identity = uuid4().hex
    secret = secrets.token_urlsafe
    owner_password = secret(32)
    values = {key: provider[key] for key in required}
    values.update(
        {
            "COMPOSE_PROJECT_NAME": "tender-workbench-external",
            "POSTGRES_PORT": "55441",
            "KAFKA_EXTERNAL_PORT": "19095",
            "POSTGRES_PASSWORD": owner_password,
            "OWNER_DATABASE_DSN": (
                f"postgresql://tender_owner:{owner_password}@127.0.0.1:55441/tender"
            ),
            "APP_ORIGIN": "http://127.0.0.1:5175",
            "SESSION_SECRET": secret(48),
            "CSRF_SECRET": secret(48),
            "KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:19095",
            "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT",
            "OBJECT_STORE_BACKEND": "oss",
            "OBJECT_STORE_PREFIX": f"workbench-environments/{identity}/",
            "WORKBENCH_ENVIRONMENT_ID": identity,
        }
    )
    for role in ("api", "worker", "research_worker", "score_read"):
        password = secret(32)
        values[f"{role.upper()}_DATABASE_DSN"] = (
            f"postgresql://twb_{role}:{password}@127.0.0.1:55441/tender"
        )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
    print("Created private external profile for a new database and independent Kafka broker.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", type=Path, default=ROOT / ".local/providers.env")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/external.env")
    arguments = parser.parse_args()
    configure(arguments.providers, arguments.output)


if __name__ == "__main__":
    main()
