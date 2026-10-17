"""Run only local embedding-index control planes with the restricted API identity."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("project-rag", "knowledge-rag"))
args = parser.parse_args()
os.environ.pop("DEEPSEEK_API_KEY", None)
assert os.environ.get("WORKBENCH_RUNTIME_ROLE") == "api"
assert not any(key in os.environ for key in (
    "OWNER_DATABASE_DSN", "WORKER_DATABASE_DSN", "RESEARCH_WORKER_DATABASE_DSN",
    "MODEL_TRAIN_DATABASE_DSN", "MODEL_EVALUATE_DATABASE_DSN", "DEEPSEEK_API_KEY",
))
assert os.environ["KAFKA_BOOTSTRAP_SERVERS"] == "127.0.0.1:19097"
candidate = Path("/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-05/pricing-candidate-05.json")
manifest = json.loads(candidate.read_text())
root = Path(manifest["runtime_root"])
assert all(hashlib.sha256((root / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in manifest["sources"])
module = importlib.import_module({"project-rag": "packages.rag.coordinator", "knowledge-rag": "packages.knowledge.rag_coordinator"}[args.mode])
from packages.domain.runtime import assert_role, connection
assert_role()
with connection() as conn:
    identity = conn.execute("SELECT current_database() AS database,current_user AS role").fetchone()
assert identity["database"] == "tender_checks_workbench08"
loaded = {}
for name, imported in list(sys.modules.items()):
    if name == "apps" or name.startswith("apps.") or name == "packages" or name.startswith("packages."):
        filename = getattr(imported, "__file__", None)
        if filename:
            path = Path(filename).resolve()
            assert path.is_relative_to(root), name
            loaded[name] = str(path)
receipt = {"pid": os.getpid(), "uid": os.getuid(), "mode": args.mode,
           "database": identity["database"], "nonowner_role": identity["role"],
           "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
           "sources_verified": len(manifest["sources"]), "loaded_modules": loaded,
           "provider_key_present": False, "paid_consumers_started": False}
with (Path(__file__).resolve().parent / ("coordinator-" + args.mode + ".json")).open("x") as stream:
    json.dump(receipt, stream, indent=2)
sys.argv = [module.__name__]
module.main()
