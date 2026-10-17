"""跨HTTP命令的未知计费保护；执行nonce本身不构成再次收费授权。"""

from packages.domain.runtime import DomainError


def assert_preparation_reconciled(c, bid, input_digest):
    rows = c.execute(
        "SELECT m.id FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s AND o.handler_version='prepare/1' AND o.input_digest=%s AND m.state IN('reserved','unknown') ORDER BY m.id",
        (bid, input_digest),
    ).fetchall()
    _require_reconciliation(rows)


def assert_review_reconciled(c, bid, content_digest):
    rows = c.execute(
        "SELECT m.id FROM model_calls m JOIN operations o ON o.id=m.operation_id JOIN review_runs r ON r.operation_id=o.id JOIN review_inputs i ON i.id=r.input_id WHERE r.bid_id=%s AND i.input_digest=%s AND m.state IN('reserved','unknown') ORDER BY m.id",
        (bid, content_digest),
    ).fetchall()
    _require_reconciliation(rows)


def _require_reconciliation(rows):
    if rows:
        raise DomainError(
            "MODEL_CALL_REQUIRES_RECONCILIATION",
            "此前同一输入的模型调用是否计费或完成尚未核清，未再次发起收费请求",
            409,
            {"call_ids": [str(row["id"]) for row in rows], "new_attempt_authorized": False},
            retryable=False,
        )
