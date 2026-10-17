"""对象I/O与数据库提交分开：先写新私有对象，再短事务登记字节身份。"""

import json
import os

from packages.domain import storage
from packages.domain.runtime import DomainError, clean, sha, uid


def stage_json(value, media):
    raw = json.dumps(
        clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    identity = uid()
    prefix = os.environ.get("RESEARCH_OBJECT_PREFIX", "research").strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise DomainError("RESEARCH_OBJECT_PREFIX_INVALID", "研究产物路径配置不合法", 503)
    key = f"{prefix}/staged/{identity}.json"
    # 调用方不得在connection/图锁/关键行锁作用域调用此函数。
    storage.put(key, raw, media)
    return {
        "id": identity,
        "digest": sha(raw),
        "object_key": key,
        "byte_size": len(raw),
        "media_type": media,
    }


def commit_stage(c, staged):
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
        tuple(staged[key] for key in ("id", "digest", "object_key", "byte_size", "media_type")),
    )
    row = c.execute("SELECT * FROM blobs WHERE digest=%s", (staged["digest"],)).fetchone()
    if (
        row is None
        or row["byte_size"] != staged["byte_size"]
        or row["media_type"] != staged["media_type"]
    ):
        raise DomainError("RESEARCH_ARTIFACT_CONFLICT", "研究产物用途或字节身份冲突", 409)
    return row["id"]
