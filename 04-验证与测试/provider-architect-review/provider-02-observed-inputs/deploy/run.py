"""Run one local command with private environment values, without shell evaluation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from initialize_database import ROOT, file_configuration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
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
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
