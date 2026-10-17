"""Private CPU purpose selection never inherits the application or other purpose secrets."""

import subprocess
from uuid import uuid4

import pytest

from packages.model_governance import purpose_profiles
from packages.model_governance.common import GovernanceError


def profile(tmp_path, text, mode=0o600):
    path = tmp_path / "single-purpose.env"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_launcher_builds_only_selected_purpose_env_from_private_file(tmp_path, monkeypatch):
    path = profile(
        tmp_path, "MODEL_TRAIN_DATABASE_DSN='postgresql://synthetic-only@localhost/database'\n"
    )
    for key in (
        "OWNER_DATABASE_DSN",
        "API_DATABASE_DSN",
        "OBJECT_STORE_SECRET_KEY",
        "DEEPSEEK_API_KEY",
        "MODEL_EVALUATE_DATABASE_DSN",
        "FUTURE_UNKNOWN_SECRET",
    ):
        monkeypatch.setenv(key, "explicit-test-sentinel-never-inherit")
    seen = []

    def run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{"state":"synthetic"}', "")

    monkeypatch.setattr(purpose_profiles.subprocess, "run", run)
    result = purpose_profiles.launch("train", uuid4(), profile=path)
    assert result.returncode == 0
    args, values = seen[0]
    assert "-I" in args and values["capture_output"] and values["check"] is False
    assert set(values["env"]) == {"MODEL_TRAIN_DATABASE_DSN", "LANG", "LC_ALL", "TZ"}
    assert "explicit-test-sentinel-never-inherit" not in repr(values)
    assert "MODEL_TRAIN_DATABASE_DSN" not in repr(args)


@pytest.mark.parametrize(
    "text",
    [
        "MODEL_EVALUATE_DATABASE_DSN=wrong-purpose\n",
        "MODEL_TRAIN_DATABASE_DSN=good\nDEEPSEEK_API_KEY=forbidden\n",
        "MODEL_TRAIN_DATABASE_DSN=good\nMODEL_TRAIN_DATABASE_DSN=duplicate\n",
        "MODEL_TRAIN_DATABASE_DSN=two values\n",
        "MODEL_TRAIN_DATABASE_DSN=''\n",
        "MALFORMED\n",
    ],
)
def test_general_cross_purpose_duplicate_empty_or_malformed_profiles_rejected(tmp_path, text):
    with pytest.raises(GovernanceError):
        purpose_profiles.read_profile("train", profile(tmp_path, text))


def test_public_or_symlink_profile_not_read_and_invalid_role_rejected(tmp_path):
    path = profile(tmp_path, "MODEL_TRAIN_DATABASE_DSN=synthetic\n", 0o644)
    with pytest.raises(GovernanceError):
        purpose_profiles.read_profile("train", path)
    path.chmod(0o600)
    link = tmp_path / "symlink.env"
    link.symlink_to(path)
    with pytest.raises(GovernanceError):
        purpose_profiles.read_profile("train", link)
    with pytest.raises(GovernanceError):
        purpose_profiles.read_profile("owner", path)
