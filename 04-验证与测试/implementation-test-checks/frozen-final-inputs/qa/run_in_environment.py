"""在已加载的独立QA环境执行验收，绑定脱敏身份，不回显凭据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sources():
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for base in ["apps/api", "apps/worker", "packages/domain", "packages/contracts"]
        for p in (ROOT / base).rglob("*")
        if p.is_file() and p.suffix in [".py", ".json", ".sql"] and "__pycache__" not in p.parts
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["api", "pdf"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    env_file = Path(os.environ.get("WORKBENCH_ENV_FILE", ""))
    if env_file.name != "qa.env":
        raise SystemExit("只允许显式独立qa.env，拒绝使用开发/生产配置")
    manifest = sources()
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    members = json.loads((ROOT / ".local/qa-test-members.json").read_text())
    environment = dict(
        os.environ,
        QA_BASE_URL="http://127.0.0.1:8801/api/v1",
        QA_ORIGIN=os.environ["APP_ORIGIN"],
        QA_LOGIN_NAME=members[0]["login"],
        QA_PASSWORD=members[0]["password"],
        QA_APP_REVISION=revision,
        QA_CONTRACT_SHA256=hashlib.sha256(
            (ROOT / "packages/contracts/schemas/protocol.schema.json").read_bytes()
        ).hexdigest(),
        QA_CONFIG_SHA256=hashlib.sha256(env_file.read_bytes()).hexdigest(),
        QA_ADAPTER_MODE="real_pg_kafka_s3_model_unconfigured",
        QA_RESULT_PATH=str(args.output.resolve()),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = (
        [sys.executable, str(ROOT / "qa/run_api_boundary.py")]
        if args.suite == "api"
        else [sys.executable, "-m", "qa.probe_pdf_slice"]
    )
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if args.output.exists():
        result = json.loads(args.output.read_text())
        result["application_identity"] = revision
        result["configuration_sha256"] = environment["QA_CONFIG_SHA256"]
        result["contract_sha256"] = environment["QA_CONTRACT_SHA256"]
        result["source_stable_during_test"] = manifest == sources()
        result["candidate_status"] = os.environ.get("QA_CANDIDATE_ID", "interim_not_frozen")
        result["application_sources"] = manifest
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
