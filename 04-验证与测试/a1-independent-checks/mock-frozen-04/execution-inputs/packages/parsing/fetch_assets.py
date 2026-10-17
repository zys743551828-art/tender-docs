"""Explicit installation step for pinned public OCR dictionaries, never used at request time."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--directory", type=Path, required=True)
    args = arguments.parse_args()
    assets = json.loads(Path(__file__).with_name("assets.json").read_text())
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, record in assets.items():
        target = args.directory / (name + ".traineddata")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == record["sha256"]:
            continue
        # URL is a checked-in public GitHub HTTPS resource at an exact commit.
        if not record["url"].startswith(
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
            + record["revision"]
            + "/"
        ):
            raise ValueError("OCR_ASSET_SOURCE_INVALID")
        with urlopen(record["url"], timeout=60) as response:  # nosec B310
            data = response.read(record["bytes"] + 1)
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("OCR_ASSET_DIGEST_MISMATCH")
        staging = target.with_suffix(".partial")
        staging.write_bytes(data)
        staging.chmod(0o600)
        staging.replace(target)


if __name__ == "__main__":
    main()
