"""Explicit local-only BGE adapters; no model downloads or custom remote code at runtime."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

from packages.rag.core import normalized_vector


def verified_manifest(directory: Path, expected_model: str) -> dict:
    manifest = json.loads((directory / "asset-manifest.json").read_text())
    if manifest["model"] != expected_model or len(manifest["revision"]) != 40:
        raise ValueError("Local model identity mismatch")
    expected_weight = (
        "pytorch_model.bin" if expected_model == "BAAI/bge-m3" else "model.safetensors"
    )
    expected_files = {
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        expected_weight,
    }
    files = manifest["files"]
    if set(files) != expected_files or {path.name for path in directory.iterdir()} != (
        expected_files | {"asset-manifest.json"}
    ):
        raise ValueError("Incomplete model manifest")
    for filename, expected in files.items():
        path = (directory / filename).resolve()
        if path.parent != directory.resolve():
            raise ValueError("Model asset path escapes its directory")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        if digest.hexdigest() != expected["sha256"] or size != expected["size"]:
            raise ValueError("Local model asset digest mismatch")
    return manifest


class BgeDense:
    def __init__(self, directory: Path, batch_size: int = 16):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.manifest = verified_manifest(directory, "BAAI/bge-m3")
        self.identity = {
            "model_name": self.manifest["model"],
            "model_revision": self.manifest["revision"],
            "weights_digest": self.manifest["files"]["pytorch_model.bin"]["sha256"],
            "preprocessing_digest": hashlib.sha256(
                json.dumps(
                    {
                        "all_assets": self.manifest,
                        "torch": importlib.metadata.version("torch"),
                        "transformers": importlib.metadata.version("transformers"),
                        "pooling": "cls",
                        "normalization": "L2",
                        "truncation": False,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
        }
        if not 1 <= batch_size <= 16:
            raise ValueError("Invalid embedding batch size")
        self.batch_size = batch_size
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(directory),
            local_files_only=True,
            trust_remote_code=False,
        )
        if not self.tokenizer.is_fast:
            raise ValueError("A tokenizer with source offsets is required")
        self.model = AutoModel.from_pretrained(
            str(directory),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=False,
            weights_only=True,
        ).eval()
        if self.model.config.hidden_size != 1024:
            raise ValueError("Unexpected BGE dense dimensions")

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [
            tuple(pair)
            for pair in self.tokenizer(
                text,
                add_special_tokens=False,
                return_offsets_mapping=True,
            )["offset_mapping"]
            if pair[0] < pair[1]
        ]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if any(not text.strip() or len(self.offsets(text)) > 8190 for text in texts):
            raise ValueError("Embedding input is empty or exceeds the full token limit")
        vectors: list[list[float]] = []
        with self.torch.inference_mode():
            for offset in range(0, len(texts), self.batch_size):
                tokens = self.tokenizer(
                    texts[offset : offset + self.batch_size],
                    padding=True,
                    truncation=False,
                    return_tensors="pt",
                )
                pooled = self.model(**tokens).last_hidden_state[:, 0]
                vectors.extend(normalized_vector(row) for row in pooled.float().tolist())
        return vectors


class BgeReranker:
    def __init__(self, directory: Path):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.manifest = verified_manifest(directory, "BAAI/bge-reranker-v2-m3")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(directory),
            local_files_only=True,
            trust_remote_code=False,
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(directory),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval()

    def rank(self, query: str, texts: list[str]) -> list[float]:
        scores = []
        with self.torch.inference_mode():
            for text in texts:
                tokens = self.tokenizer(
                    query,
                    text,
                    truncation=False,
                    return_tensors="pt",
                )
                if tokens["input_ids"].shape[1] > 8192:
                    raise ValueError("Reranker pair exceeds its full token limit")
                scores.append(float(self.model(**tokens).logits.reshape(-1)[0]))
        return scores
