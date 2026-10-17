"""Authenticated loopback ports for API/Worker without loading model weights there."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

from packages.rag.core import normalized_vector


class RuntimeUnavailable(Exception):
    pass


class DenseHttp:
    def __init__(self):
        self.url = os.environ.get("RAG_RUNTIME_URL", "http://127.0.0.1:8810").rstrip("/")
        target = urlsplit(self.url)
        if (
            target.scheme != "http"
            or target.hostname != "127.0.0.1"
            or target.path
            or target.username
            or target.query
            or target.fragment
        ):
            raise RuntimeUnavailable("RAG_RUNTIME_CONFIG_INVALID")
        self.token = os.environ.get("RAG_RUNTIME_TOKEN", "")
        if len(self.token) < 32:
            raise RuntimeUnavailable("RAG_RUNTIME_NOT_CONFIGURED")
        metadata = self._call("GET", "/identity")
        self.identity: dict[str, str] = metadata["identity"]
        self.lexical_dictionary_digest = metadata["lexical_dictionary_digest"]

    def _call(self, method, path, body=None):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(120, connect=3), trust_env=True, follow_redirects=False
            ) as client:
                response = client.request(
                    method,
                    self.url + path,
                    json=body,
                    headers={"Authorization": "Bearer " + self.token},
                )
            if response.status_code != 200:
                raise RuntimeUnavailable("RAG_RUNTIME_UNAVAILABLE")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RuntimeUnavailable("RAG_RUNTIME_UNAVAILABLE") from None

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [
            tuple(value) for value in self._call("POST", "/tokenize", {"text": text})["offsets"]
        ]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), 16):
            result = self._call("POST", "/embed", {"texts": texts[offset : offset + 16]})
            if result["identity"] != self.identity:
                raise RuntimeUnavailable("RAG_RUNTIME_PROFILE_CHANGED")
            batch = result["vectors"]
            if len(batch) != len(texts[offset : offset + 16]):
                raise RuntimeUnavailable("RAG_RUNTIME_INCOMPLETE")
            vectors.extend(normalized_vector(row) for row in batch)
        return vectors


class RerankHttp:
    def __init__(self, dense: DenseHttp):
        self.dense = dense

    def rank(self, query: str, texts: list[str]) -> list[float]:
        return self.dense._call("POST", "/rerank", {"query": query, "texts": texts})["scores"]


class LexicalHttp:
    def __init__(self, dense: DenseHttp):
        self.dense = dense

    def lexemes(self, text: str) -> str:
        return self.dense._call("POST", "/lexical", {"text": text})["lexemes"]
