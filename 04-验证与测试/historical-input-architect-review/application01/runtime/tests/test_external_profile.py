"""A new external profile must construct real adapters without touching old databases."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
spec = importlib.util.spec_from_file_location(
    "configure_external", ROOT / "deploy/configure_external.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_external_profile_is_complete_private_isolated_and_refuses_overwrite(tmp_path, monkeypatch):
    from initialize_database import file_configuration

    from packages.domain.providers.oss import OssClient

    values = {
        "DEEPSEEK_API_KEY": "synthetic-not-live",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "DEEPSEEK_MODEL": "deepseek-flash",
        "OBJECT_STORE_BACKEND": "oss",
        "OBJECT_STORE_ENDPOINT": "https://oss-cn-hangzhou.aliyuncs.com",
        "OBJECT_STORE_BUCKET": "tender-base",
        "OBJECT_STORE_BUCKET_URL": "https://tender-base.oss-cn-hangzhou.aliyuncs.com",
        "OBJECT_STORE_REGION": "cn-hangzhou",
        "OBJECT_STORE_ACCESS_KEY": "synthetic-access",
        "OBJECT_STORE_SECRET_KEY": "synthetic-secret",
    }
    provider = tmp_path / "provider.env"
    provider.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    output = tmp_path / "external.env"
    module.configure(provider, output)
    configured = file_configuration(output)
    assert output.stat().st_mode & 0o777 == 0o600
    assert ":55441/" in configured["API_DATABASE_DSN"]
    assert configured["KAFKA_BOOTSTRAP_SERVERS"] == "127.0.0.1:19095"
    assert configured["OBJECT_STORE_PREFIX"].startswith("workbench-environments/")
    for key, value in configured.items():
        monkeypatch.setenv(key, value)
    OssClient.from_env()
    with pytest.raises(FileExistsError):
        module.configure(provider, output)
