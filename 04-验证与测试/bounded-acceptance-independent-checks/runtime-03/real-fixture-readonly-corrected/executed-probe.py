"""Read-only identity preparation for a future separately authorized two-call window."""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from packages.domain.review_data import frozen_documents, read_bundle
from packages.domain.runtime import assert_role, clean, connection


def main():
    root = Path(os.environ["QA_GUARD_ROOT"])
    out = root / os.environ.get("QA_GUARD_EXECUTION", "real-fixture-readonly-preflight")
    out.mkdir(exist_ok=False)
    fixture = Path("/workspace/tender-docs/04-验证与测试/bounded-review-execution-checks/fixture-90-120.json")
    expected = "17099eecfabad42a1fecf75ca393502bbec004cbcd987c0ac41cdd1b227153f7"
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == expected
    approved = json.loads(fixture.read_text())
    bid = UUID(approved["bid_id"])
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "external-release.env"
    assert_role(True)
    with connection(True) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        heads = c.execute("SELECT p.current_body_id,p.current_material_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s", (bid,)).fetchone()
        documents = frozen_documents(read_bundle(c, bid, heads["current_material_id"]))
        fact = c.execute("SELECT fact_set_id FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
        calls = c.execute("SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s", (bid,)).fetchone()["n"]
        operations = c.execute("SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version IN ('prepare/1','review/1')", (bid,)).fetchone()["n"]
        settings = c.execute("SELECT settings->'auto_review' AS auto_review FROM bids WHERE id=%s", (bid,)).fetchone()
    checks = dict(exact_approved_heads=clean(heads) == approved["heads"], exact_all_frozen_documents=documents == approved["documents"], no_fact_head=not fact or fact["fact_set_id"] is None, zero_model_calls=calls == 0, zero_prepare_review_operations=operations == 0, automatic_review_disabled=settings["auto_review"] is False)
    result = dict(passed=all(checks.values()), checks=checks, readonly=True, execute_entry_invocations=0, real_kafka_consumers_started=0, paid_generation_calls=0, fixture_sha256=expected, bid_id=str(bid), source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(dict(passed=result["passed"], checks=checks))


if __name__ == "__main__":
    main()
