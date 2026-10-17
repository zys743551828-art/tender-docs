"""Read-only cold binding probe; invoke through this candidate's role launcher."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from packages.domain.runtime import assert_role, connection

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = json.loads((BASE / "candidate.json").read_text())
for entry in manifest["sources"]:
    assert digest(RUNTIME / entry["path"]) == entry["sha256"], entry["path"]
assert_role()
with TestClient(app) as client:
    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.status_code
    response = client.get("/openapi.json")
    assert response.status_code == 200
    actual = response.json()
expected = json.loads((BASE / "actual.openapi.json").read_text())
assert actual == expected, "actual contract drift"
bindings = []
for name, module in sorted(sys.modules.items()):
    # Product package names, not a substring such as botocore's vendored /packages/.
    if name.split(".", 1)[0] not in {"apps", "packages", "deploy"}:
        continue
    filename = getattr(module, "__file__", None)
    if filename is None:
        continue
    path = Path(filename).resolve()
    relative = path.relative_to(RUNTIME)
    bindings.append({"module": name, "path": str(relative), "sha256": digest(path)})
with connection() as c:
    role = c.execute(
        "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    counts = {
        table: c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        for table in ("members", "model_calls", "operations")
    }
assert not role["rolsuper"]
assert counts == {"members": 0, "model_calls": 0, "operations": 0}, counts
result = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "candidate_sha256": digest(BASE / "candidate.json"),
    "ready_status": ready.status_code,
    "actual_contract_sha256": digest(BASE / "actual.openapi.json"),
    "paths": len(actual["paths"]),
    "operations": sum(
        method in {"get", "post", "patch", "put", "delete", "head", "options"}
        for path in actual["paths"].values()
        for method in path
    ),
    "source_count": len(manifest["sources"]),
    "product_modules": bindings,
    "role": role,
    "counts_before_account_seed": counts,
    "provider_calls": 0,
    "persistent_services_started": False,
    "scope": "cold binding only; independent feature QA remains separate",
}
(BASE / "cold-api-binding.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({k: v for k, v in result.items() if k != "product_modules"}))
