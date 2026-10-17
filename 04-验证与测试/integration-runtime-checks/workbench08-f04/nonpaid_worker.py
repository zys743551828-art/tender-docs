"""Only ordinary non-generation lanes; remove the provider key after private profile load."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

p = argparse.ArgumentParser()
p.add_argument("mode", choices=("relay", "reconcile", "consume", "consume-export", "consume-comparison"))
a = p.parse_args()
os.environ.pop("DEEPSEEK_API_KEY", None)
assert os.environ.get("WORKBENCH_RUNTIME_ROLE") == "worker"
assert not any(k in os.environ for k in ("DEEPSEEK_API_KEY", "API_DATABASE_DSN", "OWNER_DATABASE_DSN", "MODEL_TRAIN_DATABASE_DSN", "MODEL_EVALUATE_DATABASE_DSN"))
assert os.environ["KAFKA_BOOTSTRAP_SERVERS"] == "127.0.0.1:19097"
manifest_path = Path("/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-05/pricing-candidate-05.json")
manifest = json.loads(manifest_path.read_text())
root = Path(manifest["runtime_root"])
assert all(hashlib.sha256((root / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in manifest["sources"])
from apps.worker import engine
from packages.domain.runtime import assert_role, connection
assert_role(True)
with connection(True) as c:
    identity = c.execute("SELECT current_database() AS database,current_user AS role").fetchone()
assert identity["database"] == "tender_checks_workbench08"
loaded = {}
for name, module in list(sys.modules.items()):
    if name == "apps" or name.startswith("apps.") or name == "packages" or name.startswith("packages."):
        location = getattr(module, "__file__", None)
        if location:
            location = Path(location).resolve()
            assert location.is_relative_to(root), name
            loaded[name] = str(location)
result = {"pid": os.getpid(), "uid": os.getuid(), "mode": a.mode, "database": identity["database"],
          "nonowner_role": identity["role"], "broker": "127.0.0.1:19097", "provider_key_present": False,
          "candidate_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
          "sources_verified": len(manifest["sources"]), "loaded_modules": loaded,
          "review_assistant_research_consumers_started": False}
with (Path(__file__).resolve().parent / ("worker-" + a.mode + ".json")).open("x") as stream:
    json.dump(result, stream, indent=2)
sys.argv = ["apps.worker", a.mode]
runpy.run_module("apps.worker", run_name="__main__")
