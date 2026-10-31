"""Check explicit implementation references; never infer acceptance from test presence."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def fingerprint(root, path):
    names = (
        subprocess.check_output(["git", "ls-files", "-z", "--", path], cwd=root)
        .decode()
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(filter(None, names)):
        file = root / name
        if not file.is_file():
            raise ValueError(f"Missing tracked source: {name}")
        digest.update(name.encode() + b"\0" + file.read_bytes() + b"\0")
    if not any(names):
        raise ValueError(f"No tracked source: {path}")
    return digest.hexdigest()


def check(root, docs, manifest):
    rows = manifest["requirements"]
    if {r["id"] for r in rows} != {f"FR-{n:03}" for n in range(1, 41)} or len(
        rows
    ) != 40:
        raise ValueError("Expected unique FR001–040 mapping")
    supporting = manifest.get("supporting_checks", [])
    if len({r["id"] for r in supporting}) != len(supporting) or any(
        not r["id"].startswith("TEST-") for r in supporting
    ):
        raise ValueError("Expected unique supporting TEST mappings")
    for row in rows + supporting:
        for field in ("code", "test"):
            path = row[field]
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("Reference must remain inside workbench")
            if fingerprint(root, "workbench/" + path) != row[field + "_sha256"]:
                raise ValueError(
                    f"{row['id']} {field} changed: review formal docs and refresh mapping"
                )
        if row["acceptance"] != "not_accepted":
            raise ValueError("Acceptance requires a separate reviewed evidence record")
        if row["id"] not in (docs / row["document"]).read_text(encoding="utf-8"):
            raise ValueError(f"Missing documented requirement {row['id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    args = parser.parse_args()
    docs = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (docs / "05-技术与设计/implementation-trace.json").read_text(encoding="utf-8")
    )
    check(args.app.resolve(), docs, manifest)
    print(
        f"PASS: 40 requirement mappings + {len(manifest.get('supporting_checks', []))} supporting checks, tracked fingerprints and document anchors"
    )
