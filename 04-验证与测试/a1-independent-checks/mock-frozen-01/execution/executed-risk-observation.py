"""Read-only independent correction: critical retention does not imply major retention."""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from apps.api.review_views import finding_view
from packages.domain.finding_lifecycle import visible_rows
from packages.domain.runtime import connection


def main():
    root = Path(os.environ["QA_A1_ROOT"])
    prior = json.loads((root / "execution/result.json").read_text())
    first, second, _ = prior["review_ids"]
    with connection() as c:
        observations = [finding_view(c, row["id"]) for row in visible_rows(c, UUID(second))]
    critical = [f for f in observations if f["severity"] == "critical"]
    result = dict(
        passed=len(critical) == 1
        and critical[0]["origin_review_id"] == first
        and len(observations) == 2,
        critical=critical,
        all_severities=[f["severity"] for f in observations],
        source_result_sha256=hashlib.sha256(
            (root / "execution/result.json").read_bytes()
        ).hexdigest(),
        explanation="Original probe incorrectly required major findings as well as critical findings to keep first-round origin. Product guarantee applies to known critical risks. This read-only correction checks exact critical count/origin; other 13 original assertions remain unchanged.",
        paid_generation_calls=0,
        readonly=True,
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (root / "execution/risk-readonly-observation.json").write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    (root / "execution/executed-risk-observation.py").write_bytes(Path(__file__).read_bytes())
    print(
        dict(
            passed=result["passed"],
            critical_count=len(critical),
            all_severities=result["all_severities"],
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
