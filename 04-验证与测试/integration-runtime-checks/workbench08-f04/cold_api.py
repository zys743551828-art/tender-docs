"""Start only the fixed API; does not dispatch background work."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

OUT = Path(__file__).resolve().parent
CANDIDATE = Path("/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-04/pricing-candidate-04.json")
manifest = json.loads(CANDIDATE.read_text())
root = Path(manifest["runtime_root"])
assert all(hashlib.sha256((root / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in manifest["sources"])
assert not any(k in os.environ for k in ("OWNER_DATABASE_DSN", "WORKER_DATABASE_DSN", "MODEL_TRAIN_DATABASE_DSN", "MODEL_EVALUATE_DATABASE_DSN"))
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 8808))
from apps.api.main import app
from packages.domain.runtime import assert_role, connection
assert_role()
actual = app.openapi()
expected = json.loads(Path("/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-04/actual-f04.openapi.json").read_text())
assert actual == expected
loaded = {}
for name, module in list(sys.modules.items()):
    if not (name == "apps" or name.startswith("apps.") or name == "packages" or name.startswith("packages.")):
        continue
    location = getattr(module, "__file__", None)
    if location:
        path = Path(location).resolve()
        assert path.is_relative_to(root), name
        loaded[name] = str(path)
with connection() as c:
    row = c.execute("SELECT current_database() AS database,current_user AS role").fetchone()
    counts = {table: c.execute("SELECT count(*) AS n FROM " + table).fetchone()["n"] for table in ("members", "model_calls", "operations")}
    assert counts == {"members": 0, "model_calls": 0, "operations": 0}
result = {"pid": os.getpid(), "uid": os.getuid(), "candidate_sha256": hashlib.sha256(CANDIDATE.read_bytes()).hexdigest(),
          "source_files_verified": len(manifest["sources"]), "loaded_modules": loaded,
          "actual_openapi_sha256": "3249fec969b04a3bc298bfd77a2f0dda7d1a4f3a1f1213a1a8d6648db6998fe7",
          "paths": len(actual["paths"]), "database": row["database"], "nonowner_role": row["role"],
          "initial_counts": counts, "api_port": 8808, "background_workers_started": False}
(OUT / "cold-api-binding.json").write_text(json.dumps(result, indent=2) + "\n")
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=8808, access_log=False)
