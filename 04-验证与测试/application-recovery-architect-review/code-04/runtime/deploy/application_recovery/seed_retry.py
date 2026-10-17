"""Retry only an empty, own initialized synthetic source; no DDL or new Docker resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from deploy.application_recovery import archive, binding, cluster, fixture, probes
from deploy.application_recovery import security as s


def require_empty(c, source):
    archive.reader_guard(c, source)
    tables = archive.generic.inventory(c)
    for table in tables:
        expected = 1 if (table["schema"], table["name"]) == ("recovery_control", "identity") else 0
        if table["rows"] != expected:
            raise s.RecoveryError("SEED_RETRY_REQUIRES_EMPTY_SYNTHETIC_SOURCE")
    if not source.get("temporary_ddl_disabled_and_nonowner"):
        raise s.RecoveryError("SEED_RETRY_REQUIRES_FINALIZED_SOURCE_INITIALIZATION")
    return tables


def retry(original, directory):
    original = s.private_path(original / "plan.json").parent
    plan = s.load(original / "plan.json")
    if s.file_sha(plan["baseline"]) != plan["baseline_sha256"]:
        raise s.RecoveryError("PROTOCOL_BASELINE_CHANGED")
    cluster.validate_inputs(plan["baseline"])
    binding.install(plan["backend"])
    source = s.load(original / "source-initialized.json")
    values = s.load(original / "source-objects.json")
    failure = s.load(original / "source-failure01.json")
    if failure.get("stage") != "source" or failure.get("sqlstate") != "23502":
        raise s.RecoveryError("ONLY_RECORDED_SYNTHETIC_FIXTURE_23502_RETRY_SUPPORTED")
    cluster.assert_resources(source)
    with psycopg.connect(s.connection_dsn(source, "reader")) as c:
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        require_empty(c, source)
    directory = s.private_path(directory, exists=False)
    directory.mkdir(mode=0o700, exist_ok=False)
    for name in ("plan.json", "source-initialized.json", "source-objects.json"):
        s.save(directory / name, s.load(original / name))
    s.save(
        directory / "source-origin.json",
        {
            "source_attempt": str(original),
            "source_initialization_sha256": s.file_sha(original / "source-initialized.json"),
            "failure_sha256": s.file_sha(original / "source-failure01.json"),
            "reuses_only_own_empty_initialized_source": True,
            "new_resources": 0,
            "DDL": False,
        },
    )
    s.save(directory / "source-permission-probes.json", probes.verify(source))
    fixture.create(source, values, directory)
    with psycopg.connect(s.connection_dsn(source, "reader")) as c:
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        archive.reader_guard(c, source)
        s.save(directory / "source-definitions.json", archive.detail(c, source))
        s.save(directory / "source-before-snapshot.json", {"tables": archive.generic.inventory(c)})
    return {
        "synthetic_source_seed_retry_succeeded": True,
        "new_resources": 0,
        "DDL": False,
        "model_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(retry(args.original, args.directory)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(error)
                    if isinstance(error, s.RecoveryError)
                    else type(error).__name__,
                }
            )
        )
        raise SystemExit(1) from None
