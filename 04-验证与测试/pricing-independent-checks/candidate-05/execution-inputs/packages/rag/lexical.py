"""Pinned offline Chinese segmentation for PostgreSQL simple FTS."""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
from pathlib import Path


class ChineseLexical:
    def __init__(self):
        import jieba

        if importlib.metadata.version("jieba") != "0.42.1":
            raise ValueError("Unqualified Chinese tokenizer version")
        self.tokenizer = jieba.Tokenizer()
        dictionary = Path(jieba.__file__).parent / "dict.txt"
        self.dictionary_digest = hashlib.sha256(dictionary.read_bytes()).hexdigest()
        self.tokenizer.initialize()

    def lexemes(self, text: str) -> str:
        words = self.tokenizer.cut(text.casefold(), HMM=False)
        return " ".join(word for word in words if re.search(r"[\w]", word))
