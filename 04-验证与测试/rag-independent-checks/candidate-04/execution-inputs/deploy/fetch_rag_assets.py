"""Download pinned official public RAG assets and verify content before local use."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "dense": (
        "BAAI/bge-m3",
        "5617a9f61b028005a4858fdac845db406aefb181",
        "pytorch_model.bin",
        "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",
    ),
    "reranker": (
        "BAAI/bge-reranker-v2-m3",
        "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "model.safetensors",
        "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286",
    ),
}
SMALL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
)
WEIGHT_SIZES = {"dense": 2271145830, "reranker": 2271071852}


def resume_weight(url: str, partial: Path, expected_size: int) -> None:
    """Resume a verified pinned public artifact through bounded parallel HTTPS ranges."""
    start = partial.stat().st_size
    if start > expected_size:
        raise ValueError("Partial asset exceeds the pinned size")
    segment_size = 16 * 1024 * 1024
    ranges = [
        (offset, min(offset + segment_size, expected_size) - 1)
        for offset in range(start, expected_size, segment_size)
    ]

    def segment(bounds):
        lower, upper = bounds
        destination = partial.parent / f".resume-{lower}-{upper}"
        request = urllib.request.Request(url, headers={"Range": f"bytes={lower}-{upper}"})
        # Same fixed official HTTPS URL as the ordinary pinned download below.
        with urllib.request.urlopen(request, timeout=45) as response:  # nosec B310
            if (
                response.status != 206
                or response.headers.get("Content-Range") != f"bytes {lower}-{upper}/{expected_size}"
            ):
                raise ValueError("Server did not honor the exact pinned asset range")
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            size = 0
            with os.fdopen(fd, "wb") as output:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                    if size > upper - lower + 1:
                        raise ValueError("Range response too large")
                    output.write(block)
            if size != upper - lower + 1:
                raise ValueError("Incomplete range response")
        return destination

    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(segment, ranges))
    with partial.open("ab") as output:
        for path in parts:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(block)
            path.unlink()


def fetch(name: str, root: Path) -> dict:
    model, revision, weight_name, weight_digest = MODELS[name]
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest: dict = {"model": model, "revision": revision, "files": {}}
    for filename in (*SMALL_FILES, weight_name):
        destination = directory / filename
        partial = directory / (filename + ".part")
        if destination.exists():
            with destination.open("rb") as existing:
                digest = hashlib.file_digest(existing, "sha256").hexdigest()
            if filename == weight_name and digest != weight_digest:
                raise ValueError("Existing pinned model weight digest mismatch")
        else:
            url = f"https://huggingface.co/{model}/resolve/{revision}/{filename}"
            if partial.exists() and filename == weight_name:
                resume_weight(url, partial, WEIGHT_SIZES[name])
                with partial.open("rb") as resumed:
                    digest = hashlib.file_digest(resumed, "sha256").hexdigest()
                if digest != weight_digest:
                    raise ValueError("Resumed pinned model digest mismatch")
                partial.replace(destination)
                manifest["files"][filename] = {"sha256": digest, "size": destination.stat().st_size}
                continue
            # urllib preserves the configured proxy/CA trust. Never execute remote model code.
            # URL is formed only from the fixed official HTTPS host and pinned identities.
            with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
                descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                digest_state = hashlib.sha256()
                with os.fdopen(descriptor, "wb") as output:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(block)
                        digest_state.update(block)
                digest = digest_state.hexdigest()
            if filename == weight_name and digest != weight_digest:
                raise ValueError("Downloaded pinned model weight digest mismatch")
            partial.replace(destination)
        manifest["files"][filename] = {"sha256": digest, "size": destination.stat().st_size}
        print(json.dumps({"model": name, "file": filename, "verified": True}), flush=True)
    path = directory / "asset-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    return {"model": model, "revision": revision, "files": len(manifest["files"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/models")
    arguments = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {name: pool.submit(fetch, name, arguments.directory) for name in MODELS}
        failed = False
        for name, job in jobs.items():
            try:
                print(json.dumps(job.result()), flush=True)
            except Exception as error:
                # CDN redirects may contain signed queries. Print only the exception type.
                print(json.dumps({"model": name, "failed": type(error).__name__}), flush=True)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
