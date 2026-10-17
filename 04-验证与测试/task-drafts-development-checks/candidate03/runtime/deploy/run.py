"""Run one local command with private environment values, without shell evaluation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from initialize_database import ROOT, file_configuration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--role", choices=("api", "worker", "research_worker", "score_read", "model")
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    os.environ["WORKBENCH_ENV_FILE"] = str(arguments.env_file.resolve())
    environment = dict(os.environ)
    # An explicitly selected environment file takes precedence over inherited local defaults.
    environment.update(file_configuration(arguments.env_file))
    if arguments.role:
        environment["WORKBENCH_RUNTIME_ROLE"] = arguments.role
        permitted_dsn = f"{arguments.role.upper()}_DATABASE_DSN"
        for key in list(environment):
            if (
                key.endswith("_DATABASE_DSN") and key != permitted_dsn
            ) or key == "POSTGRES_PASSWORD":
                environment.pop(key)
        if arguments.role in ("model", "score_read"):
            for key in ("DEEPSEEK_API_KEY", "OBJECT_STORE_ACCESS_KEY", "OBJECT_STORE_SECRET_KEY"):
                environment.pop(key, None)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
