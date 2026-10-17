"""CPU child receives only its purpose DSN; no generic env or model/bucket client import."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from packages.model_governance.common import GovernanceError, digest
from packages.model_governance.purpose_runtime import child_environment, evaluate_bundle
from tests.test_model_governance_statistics import bundle


def test_whitelist_removes_all_unrelated_credentials_without_reading_their_values():
    environment = {
        "MODEL_TRAIN_DATABASE_DSN": "synthetic-purpose-value",
        "MODEL_EVALUATE_DATABASE_DSN": "do-not-inherit",
        "API_DATABASE_DSN": "do-not-inherit",
        "OWNER_DATABASE_DSN": "do-not-inherit",
        "S3_SECRET_ACCESS_KEY": "do-not-inherit",
        "DEEPSEEK_API_KEY": "do-not-inherit",
        "WORKBENCH_ENV_FILE": "do-not-inherit",
        "UNKNOWN_FUTURE_SECRET": "do-not-inherit",
        "PYTHONPATH": "do-not-inherit",
    }
    result = child_environment("train", environment)
    assert set(result) == {"MODEL_TRAIN_DATABASE_DSN", "LANG", "LC_ALL", "TZ"}
    with pytest.raises(GovernanceError):
        child_environment("train", {})
    assert os.environ.get("UNKNOWN_FUTURE_SECRET") is None


def test_isolated_module_import_does_not_load_domain_env_or_provider_or_bucket():
    root = str(Path(__file__).resolve().parents[1])
    code = 'import sys,json;sys.path.insert(0,sys.argv[1]);import packages.model_governance.purpose_runtime;print(json.dumps([n for n in sys.modules if n.startswith(("packages.domain", "packages.research", "boto", "httpx"))]))'
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, root],
        env={"LANG": "C.UTF-8"},
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []


def test_synthetic_final_evaluation_cannot_grant_business_qualification():
    labels = {
        "schema": "sealed-final-label-export/1",
        "origin": "synthetic",
        "rows": [
            {
                "id": "final",
                "project_id": "final",
                "group_id": "final",
                "target": 90,
                "maximum": 30,
                "method": "synthetic",
            }
        ],
    }
    value = {
        "protocol": bundle()["protocol"],
        "origin": "synthetic",
        "labels": labels,
        "labels_digest": digest(labels),
        "model_digest": "synthetic-model",
        "predictions": {
            "rows": [{"id": "final", "status": "succeeded", "prediction": 90}],
            "intervals": {},
            "baseline_predictions": {},
        },
    }
    report = evaluate_bundle(value)
    assert report["report"]["project_mae"] == 0
    assert report["research_only"] is True and report["formal_eligibility"] == "unavailable"
    assert "synthetic_permanent_research_only" in report["blocked_reasons"]
    value["predictions"]["rows"] = []
    with pytest.raises(GovernanceError, match="every final member"):
        evaluate_bundle(value)
