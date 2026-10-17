"""Purpose-only profile launcher. Never load a general application env into a CPU child."""

import shlex
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path
from uuid import UUID

from packages.model_governance.common import GovernanceError
from packages.model_governance.purpose_runtime import ROLES, child_environment


def read_profile(role, profile):
    if role not in ROLES:
        raise GovernanceError("PURPOSE_INVALID", "Unsupported CPU purpose")
    path = Path(profile)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > 16384
    ):
        raise GovernanceError(
            "PURPOSE_PROFILE_INVALID", "A bounded regular private 600 profile is required"
        )
    expected = ROLES[role][0]
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if not separator or key.strip() != expected or key.strip() in values:
            raise GovernanceError(
                "PURPOSE_PROFILE_NOT_ISOLATED",
                "Profile must contain exactly the selected purpose DSN",
            )
        try:
            parsed = shlex.split(raw, comments=True)
        except ValueError:
            raise GovernanceError(
                "PURPOSE_PROFILE_INVALID", "Invalid purpose profile encoding"
            ) from None
        if len(parsed) != 1 or not parsed[0]:
            raise GovernanceError(
                "PURPOSE_PROFILE_INVALID", "A single nonempty purpose DSN is required"
            )
        values[expected] = parsed[0]
    return child_environment(role, values)


def launch(role, job_id, *, profile, timeout=300):
    job = str(UUID(str(job_id)))
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = 'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("packages.model_governance.purpose_runtime",run_name="__main__")'
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-c", bootstrap, root, "--role", role, "--job-id", job],
        cwd=root,
        env=read_profile(role, profile),
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )
