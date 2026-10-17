"""Create a new versioned fixture binding; retain old configuration/evidence and DB state."""

import argparse
import json
import os
from pathlib import Path

from safety import configuration, verify_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    value = configuration(args.previous)
    value["candidate"] = str(args.candidate.resolve())
    value["runtime_root"] = str(args.candidate.resolve().parent / "runtime")
    value["evidence"] = str(args.evidence.resolve())
    binding = verify_candidate(value)
    if Path(value["evidence"]).exists():
        raise ValueError("New versioned evidence directory required")
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle)
    print({"new_version_created": True, "database_preserved": True, **binding})


if __name__ == "__main__":
    main()
