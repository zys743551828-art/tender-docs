"""分片控制状态严格封装，真实存储 UploadId 永不对外。"""

from __future__ import annotations

import json
import math
from uuid import UUID

from packages.domain.runtime import DomainError

PREFIX = "twb-multipart/v1:"
PART_SIZE = 8 * 1024 * 1024
MAX_PARTS = 10000
MAX_GRANTS = 128
PHASES = {"init_reserved", "init_uncertain", "ready", "completing", "completed", "aborted"}


def pack(phase, *, key, nonce, provider_id=None):
    value = {"phase": phase, "key": key, "nonce": nonce, "provider_id": provider_id}
    encoded = PREFIX + json.dumps(value, sort_keys=True, separators=(",", ":"))
    unpack(encoded, key)
    return encoded


def unpack(value, key):
    try:
        if not isinstance(value, str) or not value.startswith(PREFIX) or len(value) > 2048:
            raise ValueError
        data = json.loads(value[len(PREFIX) :])
        if not isinstance(data, dict) or set(data) != {"phase", "key", "nonce", "provider_id"}:
            raise ValueError
        if not isinstance(data["phase"], str) or data["phase"] not in PHASES or data["key"] != key:
            raise ValueError
        if not isinstance(data["nonce"], str) or str(UUID(data["nonce"])) != data["nonce"]:
            raise ValueError
        provider = data["provider_id"]
        if provider is not None and (
            not isinstance(provider, str)
            or not provider
            or len(provider) > 1024
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in provider)
        ):
            raise ValueError
        if data["phase"] in {"init_reserved", "init_uncertain"} and provider is not None:
            raise ValueError
        if data["phase"] in {"ready", "completing", "completed"}:
            if not isinstance(provider, str) or not 1 <= len(provider) <= 1024:
                raise ValueError
        elif provider is not None and (not isinstance(provider, str) or len(provider) > 1024):
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError):
        raise DomainError(
            "MULTIPART_STATE_INVALID", "分片会话状态不可用，请联系运行负责人", 409
        ) from None


def layout(size):
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise DomainError("UPLOAD_SIZE_INVALID", "上传大小不合法", 422)
    count = math.ceil(size / PART_SIZE)
    if count > MAX_PARTS:
        raise DomainError("UPLOAD_TOO_LARGE", "分片数量超出上限", 413)
    return PART_SIZE, count


def part_size(row, number):
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not 1 <= number <= row["part_count"]
    ):
        raise DomainError("INVALID_PART", "分片号不合法", 422)
    return min(row["part_size"], row["declared_size"] - (number - 1) * row["part_size"])


def check_parts(row, observed, *, complete=False):
    if len(observed) > MAX_PARTS:
        raise DomainError("MULTIPART_PARTS_INVALID", "存储分片数量不合法", 409)
    seen = set()
    for item in observed:
        number = item["part_number"]
        if number in seen or item["byte_size"] != part_size(row, number) or not item["etag"]:
            raise DomainError("MULTIPART_PARTS_INVALID", "实际存储分片与声明不符", 422)
        seen.add(number)
    if complete and seen != set(range(1, row["part_count"] + 1)):
        raise DomainError("MULTIPART_INCOMPLETE", "仍有缺失分片，请继续上传", 422)
    return sorted(observed, key=lambda item: item["part_number"])
