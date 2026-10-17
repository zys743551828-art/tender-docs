"""确定性比较计算；读对象和差异运算不持PG连接。"""

from packages.domain.comparisons import method_digest, pair_metadata
from packages.domain.review_comparison import compare
from packages.domain.review_data import read_json_blob, restore_documents, save_json, verify_bytes
from packages.domain.runtime import DomainError, audit, connection, fetch, sha


def compute(message, lease):
    with connection(True) as c:
        blob = fetch(c, "SELECT * FROM blobs WHERE id=%s", (message["payload_ref"],))
    plan = read_json_blob(blob)
    if plan.get("comparison_method_digest") != method_digest():
        raise DomainError(
            "COMPARISON_METHOD_CHANGED", "比较方法已变化，旧任务未换用新方法计算，请重新生成", 409
        )
    documents = []
    with connection(True) as c:
        for side in ("left", "right"):
            documents.extend(restore_documents(c, plan[side]["root"]["documents"]))
    verify_bytes(documents)
    result = compare(plan)
    stored = save_json(result, worker=True)
    return {"plan": plan, "blob": stored}


def publish(c, message, outcome):
    plan, blob = outcome["plan"], outcome["blob"]
    # engine已取bid锁；再取图锁，拒绝计算中来源/检查结果变化而冒称仍是当前资格。
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    actual = pair_metadata(
        c, message["bid_id"], plan["left"]["review_id"], plan["right"]["review_id"]
    )
    if sha(actual) != plan["context_digest"] or plan["comparison_method_digest"] != method_digest():
        raise DomainError(
            "COMPARISON_CONTEXT_CHANGED",
            "比较期间来源或核对结果已变化，请重新生成；历史输入保留",
            409,
        )
    audit(
        c,
        None,
        message["bid_id"],
        "comparison.completed",
        {
            "comparison_id": message["operation_id"],
            "result_blob_id": blob["id"],
            "result_digest": blob["digest"],
        },
    )
    return {
        "result_ref": message["operation_id"],
        "result_blob_id": str(blob["id"]),
        "result_digest": blob["digest"],
    }
