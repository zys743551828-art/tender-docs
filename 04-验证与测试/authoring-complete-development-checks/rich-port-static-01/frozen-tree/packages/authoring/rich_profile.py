"""Explicit operator-installed RICH profile, never caller-selected paths."""

import json
import os
from pathlib import Path

from packages.authoring.rich_renderer import profile_for
from packages.domain.runtime import DomainError


def configured():
    path = os.environ.get("AUTHORING_RICH_RENDER_PROFILE_PATH")
    if not path:
        return None
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("identity"), dict)
            or "base_profile" not in value["identity"]
        ):
            raise ValueError()
        if value != profile_for(value["identity"]["base_profile"]):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise DomainError(
            "AUTHORING_RICH_PROFILE_INVALID", "表图转换安装配置无效，请重新核对已审运行根", 503
        ) from None
