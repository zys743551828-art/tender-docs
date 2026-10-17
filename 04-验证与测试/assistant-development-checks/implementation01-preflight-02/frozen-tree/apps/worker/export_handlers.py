"""构建计算无DB锁，Word/PDF分别在fence事务登记；受控转换关系不靠文件名推定。"""

from packages.authoring.build_repository import compute_prepared_build, prepare_build, publish_build
from packages.domain.runtime import DomainError, connection, fetch


def compute(message, lease=None):
    if message["step_key"] != "build-document":
        raise DomainError("UNKNOWN_EXPORT_STAGE", "文档构建阶段未配置", 409)
    with connection(True) as c:
        prepared = prepare_build(c, message["bid_id"], message["payload_ref"])
    return compute_prepared_build(prepared)


def publish(c, message, outcome):
    build = fetch(
        c,
        "SELECT * FROM builds WHERE bid_id=%s AND input_id=%s",
        (message["bid_id"], message["payload_ref"]),
    )
    result = publish_build(c, message["bid_id"], build["id"], outcome)
    return {
        "result_ref": str(build["id"]),
        "build": result,
        "conversion_chain": outcome.get("conversion_chain"),
    }
