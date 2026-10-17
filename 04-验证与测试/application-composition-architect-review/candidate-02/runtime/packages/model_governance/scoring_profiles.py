"""Strict single-score-DSN profile; no general env/provider/bucket credentials inheritance."""

import shlex
import stat
from pathlib import Path

from packages.model_governance.common import GovernanceError


def environment(profile):
    path = Path(profile)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > 16384
    ):
        raise GovernanceError(
            "SCORE_PROFILE_INVALID", "A bounded private regular 600 profile is required"
        )
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if not separator or key.strip() != "MODEL_SCORE_DATABASE_DSN" or values:
            raise GovernanceError(
                "SCORE_PROFILE_NOT_ISOLATED", "Exactly one dedicated score DSN is allowed"
            )
        try:
            parts = shlex.split(raw, comments=True)
        except ValueError:
            raise GovernanceError(
                "SCORE_PROFILE_INVALID", "Invalid score profile encoding"
            ) from None
        if len(parts) != 1 or not parts[0]:
            raise GovernanceError("SCORE_PROFILE_INVALID", "One nonempty score DSN required")
        values[key.strip()] = parts[0]
    if not values:
        raise GovernanceError("SCORE_PROFILE_INVALID", "Missing dedicated score DSN")
    # Existing shared runtime otherwise attempts its local .env at import time. Setting
    # this fixed inert sentinel BEFORE child interpreter startup prevents that fallback.
    return values | {
        "WORKBENCH_ENV_FILE": "/dev/null",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
