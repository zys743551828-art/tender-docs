"""Create private development configuration without printing credentials."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(destination: Path, profile: str) -> None:
    password = secrets.token_urlsafe
    owner = password(32)
    roles = {name: password(32) for name in ("api", "worker", "research_worker", "score_read")}
    store_secret = password(32)
    qa = profile == "qa"
    pg_port, kafka_port, s3_port, web_port = (
        (55440, 19094, 19002, 5174) if qa else (55439, 19093, 19000, 5173)
    )
    identity_file = ROOT / ".local" / ("qa-s3-identities.json" if qa else "s3-identities.json")
    values = {
        "COMPOSE_PROJECT_NAME": "tender-workbench-qa" if qa else "tender-workbench-dev",
        "POSTGRES_PORT": str(pg_port),
        "KAFKA_EXTERNAL_PORT": str(kafka_port),
        "S3_PORT": str(s3_port),
        "S3_IDENTITIES_FILE": str(identity_file),
        "POSTGRES_PASSWORD": owner,
        "OWNER_DATABASE_DSN": f"postgresql://tender_owner:{owner}@127.0.0.1:{pg_port}/tender",
        "APP_ORIGIN": f"http://127.0.0.1:{web_port}",
        "SESSION_SECRET": password(48),
        "CSRF_SECRET": password(48),
        "KAFKA_BOOTSTRAP_SERVERS": f"127.0.0.1:{kafka_port}",
        "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT",
        "OBJECT_STORE_BACKEND": "s3",
        "OBJECT_STORE_ENDPOINT": f"http://127.0.0.1:{s3_port}",
        "OBJECT_STORE_BUCKET": "tender-private",
        "OBJECT_STORE_REGION": "us-east-1",
        "OBJECT_STORE_ACCESS_KEY": "twb-local-app",
        "OBJECT_STORE_SECRET_KEY": store_secret,
    }
    for role, secret in roles.items():
        values[f"{role.upper()}_DATABASE_DSN"] = (
            f"postgresql://twb_{role}:{secret}@127.0.0.1:{pg_port}/tender"
        )
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("".join(f"{key}={value}\n" for key, value in values.items()))
    local_dir = ROOT / ".local"
    local_dir.mkdir(mode=0o700, exist_ok=True)
    configuration = {
        "identities": [
            {
                "name": "workbench-private-bucket",
                "credentials": [{"accessKey": "twb-local-app", "secretKey": store_secret}],
                "actions": ["Read:tender-private", "Write:tender-private", "List:tender-private"],
            }
        ]
    }
    store_file = identity_file
    descriptor = os.open(store_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(configuration, handle)
    print("Private local configuration created. Credentials were not printed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["dev", "qa"], default="dev")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    (ROOT / ".local").mkdir(mode=0o700, exist_ok=True)
    destination = arguments.output or (
        ROOT / ".local/qa.env" if arguments.profile == "qa" else ROOT / ".env"
    )
    configure(destination, arguments.profile)


if __name__ == "__main__":
    main()
