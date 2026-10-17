"""Freeze this explicit test adapter separately from the unmodified product candidate."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Fresh facility snapshot destination required")
    root = Path(__file__).resolve().parent
    runtime = args.output / "runtime"
    runtime.mkdir(parents=True)
    files = sorted([*root.glob("*.py"), root / "PROTOCOL.md"])
    sources = []
    for source in files:
        raw = source.read_bytes()
        target = runtime / source.name
        target.write_bytes(raw)
        sources.append({"path": source.name, "sha256": hashlib.sha256(raw).hexdigest()})
    test = root.parents[1] / "tests/test_assistant_browser_fixture.py"
    shutil.copy2(test, args.output / test.name)
    manifest = {
        "scope": "Explicit MockTransport/direct-handler/local-object browser fixture only",
        "runtime_root": str(runtime.resolve()),
        "sources": sources,
        "test_source_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
        "production_sources_modified": False,
        "provider_or_kafka_acceptance": False,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    (args.output / "candidate.json").write_text(manifest_text)
    print(
        {
            "frozen_fixture_files": len(sources),
            "sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        }
    )


if __name__ == "__main__":
    main()
