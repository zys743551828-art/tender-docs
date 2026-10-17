"""Explicit phases only. Every receipt is private and exclusive; no automatic old-work resume."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg

from deploy.application_recovery import archive, assets, cluster, probes
from deploy.application_recovery import security as s
from deploy.backups import run as safe


def bound(directory):
    directory = s.private_path(directory / "plan.json").parent
    plan = s.load(directory / "plan.json")
    if s.file_sha(plan["baseline"]) != plan["baseline_sha256"]:
        raise s.RecoveryError("PROTOCOL_BASELINE_CHANGED")
    cluster.validate_inputs(plan["baseline"])
    return directory, plan


def object_config(profile, suffix):
    path = Path(profile)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise s.RecoveryError("PRIVATE_OSS_CONFIGURATION_REQUIRED")
    # Select only object fields; never propagate its owner/model/worker/provider values.
    result = {k: v for k, v in safe.configuration(path).items() if k in s.OBJECT_ENV}
    if not result.get("OBJECT_STORE_PREFIX"):
        raise s.RecoveryError("SOURCE_OSS_NAMESPACE_REQUIRED")
    result["OBJECT_STORE_PREFIX"] = (
        result["OBJECT_STORE_PREFIX"].rstrip("/") + "/application-recovery/" + suffix + "/"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("plan", "source", "snapshot", "target", "application-check", "assets")
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--object-profile", type=Path)
    parser.add_argument("--source-port", type=int, default=55521)
    parser.add_argument("--target-port", type=int, default=55522)
    args = parser.parse_args()
    if args.action == "plan":
        if args.baseline is None:
            raise s.RecoveryError("BASELINE_REQUIRED")
        plan = cluster.plan(args.directory, args.baseline, args.source_port, args.target_port)
        print(json.dumps({"state": plan["state"], "plan_created": True}))
        return
    directory, plan = bound(args.directory)
    if args.action == "assets":
        if (directory / "runtime-assets.json").exists():
            result = assets.verify(s.load(directory / "runtime-assets.json"))
        else:
            result = assets.collect()
            s.save(directory / "runtime-assets.json", result)
        print(
            json.dumps({"runtime_asset_binding": True, "capabilities": result.get("capabilities")})
        )
        return
    if args.action == "source":
        if args.object_profile is None:
            raise s.RecoveryError("OBJECT_PROFILE_REQUIRED")
        _, sql_bytes = cluster.validate_inputs(plan["baseline"])
        source = cluster.create(directory, plan, "source")
        with psycopg.connect(s.connection_dsn(source), autocommit=True) as c:
            cluster.initialize(c, source, sql_bytes)
            cluster.accounts(c, source, source=True)
        s.save(directory / "source-initialized.json", source)
        values = object_config(args.object_profile, plan["nonce"] + "-source")
        s.save(directory / "source-objects.json", values)
        sys.path.insert(0, plan["backend"]["source_root"])
        from deploy.application_recovery import fixture

        s.save(directory / "source-permission-probes.json", probes.verify(source))
        fixture.create(source, values, directory)
        with psycopg.connect(s.connection_dsn(source, "reader")) as c:
            archive.reader_guard(c, source)
            s.save(directory / "source-definitions.json", archive.detail(c))
            s.save(
                directory / "source-before-snapshot.json", {"tables": archive.generic.inventory(c)}
            )
        print(
            json.dumps(
                {
                    "source_initialized": True,
                    "database": source["database"],
                    "fixture_synthetic_only": True,
                }
            )
        )
        return
    source = s.load(directory / "source-initialized.json")
    if args.action == "snapshot":
        source_values = s.load(directory / "source-objects.json")

        def late_commit():
            # UUID-based self-owned fixture: commit after snapshot, no object listing or deletion.
            with psycopg.connect(s.connection_dsn(source, "api")) as c:
                c.execute(
                    "INSERT INTO design.audit_entries(id,actor_id,bid_id,event_type,payload) VALUES(%s,%s,%s,'recovery.snapshot_late_commit','{}')",
                    (
                        uuid4(),
                        s.load(directory / "fixture.json")["ids"]["actor"],
                        s.load(directory / "fixture.json")["ids"]["bid"],
                    ),
                )

        manifest = archive.snapshot(
            source,
            source_values,
            directory / "archive",
            after_snapshot=late_commit,
            expected_definitions=s.load(directory / "source-definitions.json"),
        )
        if manifest["tables"] != s.load(directory / "source-before-snapshot.json")["tables"]:
            raise s.RecoveryError("SNAPSHOT_LATE_COMMIT_WAS_NOT_EXCLUDED")
        with psycopg.connect(s.connection_dsn(source, "reader")) as c:
            s.save(
                directory / "source-after-snapshot.json", {"tables": archive.generic.inventory(c)}
            )
        print(
            json.dumps(
                {
                    "complete_snapshot": True,
                    "tables": len(manifest["tables"]),
                    "objects": len(manifest["objects"]),
                }
            )
        )
        return
    if args.action == "target":
        manifest = archive.validate(directory / "archive")
        target = cluster.create(
            directory,
            plan,
            "target",
            owner_label=manifest["owner_label"],
            backup_group=manifest["backup_group"],
        )
        source_values = s.load(directory / "source-objects.json")
        values = {
            **source_values,
            "OBJECT_STORE_PREFIX": source_values["OBJECT_STORE_PREFIX"].removesuffix(
                plan["nonce"] + "-source/"
            )
            + plan["nonce"]
            + "-target/",
        }
        s.save(directory / "target-objects.json", values)
        result = archive.restore(target, manifest, directory / "archive", values)
        s.save(directory / "target-initialized.json", target)
        s.save(directory / "target-permission-probes.json", probes.verify(target))
        print(json.dumps(result))
        return
    target = s.load(directory / "target-initialized.json")
    if (
        not (directory / "restore-verified.json").exists()
        or (directory / "application-verified.json").exists()
    ):
        raise s.RecoveryError("APPLICATION_CHECK_REQUIRES_UNCONSUMED_RESTORE_RECEIPT")
    asset_result = assets.verify(s.load(directory / "runtime-assets.json"))
    from deploy.application_recovery.application import check

    result = check(
        target,
        plan,
        s.load(directory / "fixture.json"),
        s.load(directory / "member-credential.json"),
    )
    with psycopg.connect(s.connection_dsn(source, "reader")) as c:
        if (
            archive.generic.inventory(c)
            != s.load(directory / "source-after-snapshot.json")["tables"]
        ):
            raise s.RecoveryError("SOURCE_CHANGED_DURING_TARGET_APPLICATION_CHECK")
    original_port = archive.object_port(s.load(directory / "source-objects.json"))
    source_values = s.load(directory / "source-objects.json")
    for obj in archive.validate(directory / "archive")["objects"]:
        safe.read_object(
            original_port,
            source_values["OBJECT_STORE_BUCKET"],
            obj["key"],
            obj["bytes"],
            obj["sha256"],
        )
    result["source_tables_and_objects_unchanged"] = True
    result["runtime_assets"] = asset_result
    s.save(directory / "application-verified.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Private SQL/DSN/SDK errors may contain secrets. Preserve only a fixed code/type.
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
