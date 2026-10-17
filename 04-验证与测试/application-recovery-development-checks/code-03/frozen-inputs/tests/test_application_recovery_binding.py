"""Module origin and process-boundary regressions; no new resources, SQL or object calls."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from deploy.application_recovery import binding, cluster, security

BASELINE = Path(
    "/workspace/tender-docs/04-验证与测试/application-recovery-development-checks/protocol-02/baseline-inputs.json"
)


def backend():
    return cluster.validate_inputs(BASELINE)[0]["backend"]


def test_preloaded_live_provider_cannot_be_rebound_by_path_order():
    selected = binding.FrozenBackend(backend())
    fake = ModuleType("packages.domain.providers.oss")
    fake.__file__ = str(security.ROOT / "packages/domain/providers/oss.py")
    with pytest.raises(security.RecoveryError, match="PRELOADED_MODULE"):
        selected.verify_module(fake.__name__, fake)


def test_namespace_cannot_append_a_second_search_root():
    selected = binding.FrozenBackend(backend())
    fake = ModuleType("packages")
    fake.__path__ = [str(selected.root / "packages"), str(security.ROOT / "packages")]
    with pytest.raises(security.RecoveryError, match="NAMESPACE_SEARCH_PATH"):
        selected.verify_module("packages", fake)


def test_missing_frozen_module_never_falls_through_to_live():
    selected = binding.FrozenBackend(backend())
    with pytest.raises(security.RecoveryError, match="NOT_IN_FIXED_MANIFEST"):
        selected.find_spec("packages.unreviewed_live_provider")


def test_source_loader_compiles_verified_bytes_without_pyc(tmp_path):
    source = tmp_path / "sample.py"
    source.write_bytes(b"IDENTITY='reviewed'\n")
    loader = binding.ReviewedLoader(source, security.file_sha(source))
    namespace = {}
    exec(loader.get_code("sample"), namespace)
    assert namespace["IDENTITY"] == "reviewed"
    source.write_bytes(b"IDENTITY='changed'\n")
    with pytest.raises(security.RecoveryError, match="IMPORTED_BACKEND_BYTES_CHANGED"):
        loader.get_code("sample")


@pytest.mark.parametrize(
    "name",
    [
        "OWNER_DATABASE_DSN",
        "BACKUP_DATABASE_DSN",
        "WORKER_DATABASE_DSN",
        "MODEL_TRAIN_DATABASE_DSN",
        "MODEL_EVALUATE_DATABASE_DSN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "OBJECT_STORE_ACCESS_KEY",
        "OBJECT_STORE_SECRET_KEY",
        "DEEPSEEK_API_KEY",
        "PYTHONPATH",
        "DOCKER_CONFIG",
        "HOME",
    ],
)
def test_api_child_env_positive_allowlist_excludes_each_ambient_credential(name, monkeypatch):
    monkeypatch.setenv(name, "private-sentinel")
    result = security.api_environment(
        "host=127.0.0.1 port=55521 dbname=synthetic user=only_api password=fake"
    )
    assert name not in result
    assert "private-sentinel" not in result.values()
    assert {key for key in result if key.endswith("DATABASE_DSN")} == {"API_DATABASE_DSN"}


@pytest.mark.parametrize(
    "name",
    ["BACKUP_DATABASE_DSN", "OBJECT_STORE_SECRET_KEY", "WORKER_DATABASE_DSN", "OWNER_DATABASE_DSN"],
)
def test_child_rejects_injected_second_purpose(name):
    values = security.api_environment("host=127.0.0.1 dbname=synthetic user=only_api password=fake")
    values[name] = "private-sentinel"
    with pytest.raises(security.RecoveryError, match="NOT_SINGLE_PURPOSE"):
        security.validate_api_environment(values)


def test_real_fixed_main_knowledge_and_provider_import_in_clean_child():
    # Imports actual 07 source through the verified loader, without app lifespan or DB access.
    script = """import json,sys
sys.path.insert(0,sys.argv[1])
from deploy.application_recovery import binding
selected=binding.install(json.load(sys.stdin))
import apps.api.main
import packages.knowledge.repository
import packages.domain.providers.oss
origins=selected.origins()
print(json.dumps({"count":len(origins),"origins":origins}))
"""
    env = security.api_environment(
        "host=127.0.0.1 port=1 dbname=synthetic user=only_api password=fake"
    )
    # Poisoned paths/cloud/owner in ambient parent must not reach -I child.
    previous = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(security.ROOT)
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(security.ROOT)],
            input=json.dumps(backend()).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=30,
            check=False,
        )
    finally:
        if previous is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous
    assert result.returncode == 0, result.stderr.decode()
    evidence = json.loads(result.stdout)
    assert evidence["count"] > 50
    imported = {item["module"]: item for item in evidence["origins"]}
    root = backend()["source_root"]
    for name in ("apps.api.main", "packages.knowledge.repository", "packages.domain.providers.oss"):
        assert imported[name]["origin"].startswith(root + "/")
    for item in imported.values():
        assert item["origin"] is None or item["origin"].startswith(root + "/")
        assert all(value.startswith(root + "/") for value in item["namespace_paths"])


@pytest.mark.parametrize("entry", ["object_port", "snapshot", "restore", "fixture", "cluster"])
def test_unbound_production_entry_refuses_before_any_resource_or_network(
    entry, tmp_path, monkeypatch
):
    from deploy.application_recovery import archive, fixture

    monkeypatch.setattr(binding, "_ACTIVE", None)

    def forbidden(*args, **kwargs):
        pytest.fail("resource or object access before fixed binding")

    monkeypatch.setattr(security, "docker", forbidden)
    monkeypatch.setattr(security, "budget", forbidden)
    values = {
        "OBJECT_STORE_BACKEND": "oss",
        "OBJECT_STORE_PREFIX": "synthetic/",
        "OBJECT_STORE_ENDPOINT": "https://oss-cn-hangzhou.aliyuncs.com",
    }
    with pytest.raises(security.RecoveryError, match="FIXED_BACKEND_BINDING_REQUIRED"):
        if entry == "object_port":
            archive.object_port(values)
        elif entry == "snapshot":
            archive.snapshot({}, values, tmp_path / "archive")
        elif entry == "restore":
            archive.restore({}, {}, tmp_path / "archive", values)
        elif entry == "fixture":
            fixture.create({}, values, tmp_path)
        else:
            cluster.create(tmp_path, {}, "source")


def test_disk_budget_refuses_before_resource_creation(monkeypatch):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(security.shutil, "disk_usage", lambda _: usage(3000, 2000, 1000))
    with pytest.raises(security.RecoveryError, match="INSUFFICIENT_FREE_DISK"):
        security.budget()


def test_child_cannot_self_attest_an_arbitrary_backend_manifest():
    with pytest.raises(security.RecoveryError, match="UNREVIEWED_BACKEND_IDENTITY"):
        binding.FrozenBackend(
            {"sha256": "0" * 64, "file_count": 308, "source_root": "/nonexistent"}
        )
