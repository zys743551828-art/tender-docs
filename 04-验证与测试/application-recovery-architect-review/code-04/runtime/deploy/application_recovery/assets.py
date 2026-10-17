"""Read-only binding of already installed assets; never download weights or private profiles."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

from deploy.application_recovery import security as s


def collect(models=None, parsing=None):
    result: dict[str, Any] = {
        "format": "application-runtime-assets/1",
        "model_files": {},
        "parsing_files": {},
        "fonts": {},
        "runtime": {},
        "capabilities": {},
    }
    for package in (
        "torch",
        "transformers",
        "pypdfium2",
        "pdfplumber",
        "Pillow",
        "jieba",
        "psycopg",
        "fastapi",
    ):
        try:
            result["runtime"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["runtime"][package] = "unavailable"
    root = Path(models) if models else s.ROOT / ".local/models"
    for name, model in (("dense", "BAAI/bge-m3"), ("reranker", "BAAI/bge-reranker-v2-m3")):
        manifest_path = root / name / "asset-manifest.json"
        if not manifest_path.is_file():
            result["capabilities"][name] = "unavailable"
            continue
        manifest = json.loads(manifest_path.read_bytes())
        if manifest.get("model") != model or len(manifest.get("revision", "")) != 40:
            raise s.RecoveryError("RUNTIME_MODEL_MANIFEST_INVALID")
        records = {}
        for filename, item in manifest["files"].items():
            if Path(filename).name != filename:
                raise s.RecoveryError("RUNTIME_MODEL_ASSET_ESCAPE")
            path = root / name / filename
            if (
                path.is_symlink()
                or path.stat().st_size != item["size"]
                or s.file_sha(path) != item["sha256"]
            ):
                raise s.RecoveryError("RUNTIME_MODEL_ASSET_CHANGED")
            records[filename] = item
        result["model_files"][name] = {
            "model": model,
            "revision": manifest["revision"],
            "files": records,
            "manifest_sha256": s.file_sha(manifest_path),
        }
        result["capabilities"][name] = "bytes_verified_no_inference"
    parser_root = Path(parsing) if parsing else s.ROOT / ".local/parsing-models"
    manifest = json.loads((s.ROOT / "packages/parsing/assets.json").read_bytes())
    for name, item in manifest.items():
        path = parser_root / (name + ".traineddata")
        if not path.is_file():
            result["capabilities"]["ocr"] = "unavailable"
            continue
        if s.file_sha(path) != item["sha256"]:
            raise s.RecoveryError("OCR_ASSET_CHANGED")
        result["parsing_files"][name] = {"sha256": item["sha256"], "bytes": path.stat().st_size}
    if len(result["parsing_files"]) == len(manifest):
        result["capabilities"]["ocr"] = "bytes_verified_no_inference"
    for root in (Path("/usr/share/fonts"), Path("/etc/fonts"), Path("/usr/share/fontconfig")):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                result["fonts"][str(path)] = {
                    "sha256": s.file_sha(path),
                    "bytes": path.stat().st_size,
                }
    for name in (
        "requirements.lock.txt",
        "requirements-parsing.lock.txt",
        "requirements-rag.lock.txt",
        "packages/parsing/core.py",
        "packages/word_intake/renderer.py",
    ):
        result["runtime"][name] = s.file_sha(s.ROOT / name)
    import shutil

    executable = shutil.which("tesseract")
    result["runtime"]["tesseract_binary"] = s.file_sha(executable) if executable else "unavailable"
    # Private renderer profiles/host mounts are not silently exported or declared recovered.
    result["capabilities"]["word_renderer"] = "pending_separate_pinned_sandbox_verification"
    result["capabilities"]["parser_execution"] = "not_executed"
    result["capabilities"]["rag_inference"] = "not_executed"
    return result


def verify(expected, **kwargs):
    actual = collect(**kwargs)
    if actual != expected:
        raise s.RecoveryError("RUNTIME_ASSET_BINDING_CHANGED")
    return {
        "runtime_bytes_verified": True,
        "capabilities": actual["capabilities"],
        "assets_copied": False,
    }
