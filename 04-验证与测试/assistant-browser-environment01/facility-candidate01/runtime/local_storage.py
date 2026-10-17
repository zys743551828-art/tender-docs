"""Content keyed synthetic fixture object adapter. Never reaches S3/OSS."""

from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path
from uuid import uuid4


class LocalObjects:
    def __init__(self, root: Path):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or root.stat().st_mode & 0o077:
            raise ValueError("Private local fixture directory required")
        self.root = root.resolve()

    def path(self, key):
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 4096
            or any(
                part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", part)
                for part in key.split("/")
            )
        ):
            raise ValueError("Invalid fixture object key")
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    def get(self, key):
        descriptor = os.open(self.path(key), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()

    def put(self, key, data, media):
        if not isinstance(data, bytes):
            raise TypeError("Fixture objects require bytes")
        path = self.path(key)
        temporary = self.root / ("tmp-" + uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
        return {"ETag": '"' + hashlib.sha256(data).hexdigest() + '"'}

    def get_object(self, *, Bucket, Key):
        data = self.get(Key)
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.get(Key))}

    def install(self):
        from packages.domain import storage

        storage.get, storage.put = self.get, self.put
        storage.client = lambda: self

        def no_sign(*args, **kwargs):
            raise RuntimeError(
                "Fixture has no cloud signed URL capability; use same-origin transfer"
            )

        storage.sign = no_sign
