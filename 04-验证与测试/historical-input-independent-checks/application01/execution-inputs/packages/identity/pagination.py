"""Signed identity directory cursors bound to the exact collection and filter."""

import base64
import hmac
import json
from uuid import UUID

from packages.domain.runtime import DomainError, secret_hash


def encode(scope, key):
    raw = json.dumps(
        {"scope": scope, "key": key}, ensure_ascii=False, separators=(",", ":")
    ).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token + "." + secret_hash(token)


def decode(cursor, scope, *, rounds=False):
    if not cursor:
        return None
    try:
        if len(cursor) > 4096:
            raise ValueError("length")
        token, signature = cursor.split(".")
        if not hmac.compare_digest(secret_hash(token), signature):
            raise ValueError("signature")
        value = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        key = value["key"]
        if value["scope"] != scope or not isinstance(key, list) or len(key) != (3 if rounds else 2):
            raise ValueError("scope")
        if (
            not isinstance(key[0], str)
            or not isinstance(key[-1], str)
            or (rounds and type(key[1]) is not int)
        ):
            raise ValueError("type")
        return [*key[:-1], UUID(key[-1])]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise DomainError(
            "INVALID_CURSOR", "身份列表游标无效或不属于当前筛选，请重新查询", 422
        ) from None
