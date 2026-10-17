"""独立检测旧误报决定只沿用到同一真实证据事实；不改旧人工决定。"""

from packages.domain.finding_lifecycle import body_digest, origin, rule_key
from packages.domain.runtime import clean, fetch, sha


def assertion_digest(left, right):
    return sha(sorted(sha(item) for item in (left, right)))


def dismissal_basis(c, old, current_review_id, item, *, anchor=None):
    root = anchor if anchor is not None else origin(c, old)
    original = c.execute(
        "SELECT i.manifest,i.method_digest,a.payload->'assertions' AS assertions FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN audit_entries a ON a.bid_id=r.bid_id AND a.event_type='content_check.finding_recorded' AND a.payload->>'finding_id'=%s WHERE r.id=%s ORDER BY a.created_at,a.id LIMIT 1",
        (str(root["id"]), root["review_id"]),
    ).fetchone()
    current = fetch(
        c,
        "SELECT i.manifest,i.method_digest FROM review_runs r JOIN review_inputs i ON i.id=r.input_id WHERE r.id=%s AND r.bid_id=%s",
        (current_review_id, root["bid_id"]),
    )
    if (
        not original
        or not isinstance(original["assertions"], dict)
        or set(original["assertions"]) != {"left", "right"}
    ):
        return None
    old_body, new_body = body_digest(c, original["manifest"]), body_digest(c, current["manifest"])
    old_rule, new_rule = rule_key(c, root["rule_id"]), rule_key(c, item["rule_id"])
    old_assertions = assertion_digest(
        original["assertions"]["left"], original["assertions"]["right"]
    )
    new_assertions = assertion_digest(item["left"], item["right"])
    old_sources = sha(sorted(sha(ref) for ref in original["manifest"]["source_refs"]))
    new_sources = sha(sorted(sha(ref) for ref in current["manifest"]["source_refs"]))
    if (
        old_body != new_body
        or old_rule != new_rule
        or old_assertions != new_assertions
        or old_sources != new_sources
        or original["method_digest"] != current["method_digest"]
    ):
        return None
    return clean(
        {
            "from_root_id": root["id"],
            "body_digest": old_body,
            "rule_digest": old_rule,
            "assertions_digest": old_assertions,
            "source_refs_digest": old_sources,
            "method_digest": original["method_digest"],
        }
    )
