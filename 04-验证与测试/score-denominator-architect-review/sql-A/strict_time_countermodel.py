import json
from datetime import datetime,timezone
def t(s): return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
version=t("2026-10-01T09:00:00")
submitted=t("2026-10-02T09:00:00")
confirmed=t("2026-10-03T09:00:00")
# Hold all other current_version/source/identity/scope predicates true.
# SQL A line96 checks only version<=submission for strict mode.
a_accepts=version<=submitted
strict_authority_existed=version<=submitted and confirmed<=submitted
assert a_accepts and not strict_authority_existed
print(json.dumps({"version_created_at":version.isoformat(),"submitted_at":submitted.isoformat(),"current_confirmed_decision_at":confirmed.isoformat(),"sql_A_strict_time_predicate":a_accepts,"required_strict_time_predicate":strict_authority_existed,"scope":"offline time countermodel; no PG execution; other predicates held true"},indent=2))
