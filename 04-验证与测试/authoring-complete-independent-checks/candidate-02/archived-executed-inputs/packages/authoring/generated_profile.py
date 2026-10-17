"""Operator-installed generated lane profile loader. No request chooses a path/profile."""

import json
import os
from pathlib import Path

from packages.authoring.generated_renderer import profile_for
from packages.domain.runtime import DomainError


def configured():
    filename = os.environ.get("AUTHORING_GENERATED_RENDER_PROFILE_PATH")
    if not filename:
        return None
    try:
        path = Path(filename)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("profile size")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("identity"), dict):
            raise ValueError("profile shape")
        base = value["identity"].get("base_profile")
        if value != profile_for(base):
            raise ValueError("profile identity")
        return value
    except (OSError, ValueError, KeyError, DomainError):
        raise DomainError(
            "AUTHORING_GENERATED_PROFILE_UNAVAILABLE",
            "生成正文隔离配置不可用，请保留Word并稍后重试",
            503,
        ) from None
