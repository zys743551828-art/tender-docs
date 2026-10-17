"""Ordinary Worker hooks: immutable prepare, closed-connection compute, fenced publish."""

from uuid import UUID

from packages.domain.runtime import DomainError, connection, sha
from packages.knowledge.rag_index import compute_index
from packages.knowledge.rag_repository import assert_schema, index_row, prepare_index, publish_index
from packages.rag.http_port import DenseHttp, LexicalHttp, RuntimeUnavailable


def compute(message, lease=None):
    try:
        dense = DenseHttp()
        index_id = UUID(message["payload_ref"])
        with connection(True) as c:
            assert_schema(c, worker=True)
            row = index_row(c, index_id)
            if (
                message["handler_version"] != "knowledge-index/1"
                or message["step_key"] != "index-knowledge"
                or row["origin_bid_id"] != UUID(message["bid_id"])
                or row["operation_id"] != UUID(message["operation_id"])
                or sha({"id": str(index_id), "manifest_digest": row["input_digest"]})
                != message["input_digest"]
            ):
                raise ValueError("KNOWLEDGE_TASK_INPUT_CHANGED")
            prepared = prepare_index(c, index_id)
        return compute_index(
            prepared,
            dense,
            dense,
            LexicalHttp(dense),
            lexical_dictionary_digest=dense.lexical_dictionary_digest,
        )
    except RuntimeUnavailable:
        raise DomainError(
            "KNOWLEDGE_RUNTIME_UNAVAILABLE", "本地知识检索模型暂不可用", 503, retryable=True
        ) from None
    except ValueError:
        raise DomainError(
            "KNOWLEDGE_INDEX_INPUT_INVALID", "知识索引资料或模型身份不符", 409
        ) from None


def publish(c, message, outcome):
    try:
        row = index_row(c, outcome.prepared.index_id)
        if (
            message["handler_version"] != "knowledge-index/1"
            or message["step_key"] != "index-knowledge"
            or sha({"id": str(row["id"]), "manifest_digest": row["input_digest"]})
            != message["input_digest"]
            or row["operation_id"] != UUID(message["operation_id"])
            or row["origin_bid_id"] != UUID(message["bid_id"])
            or str(row["id"]) != message["payload_ref"]
        ):
            raise ValueError("KNOWLEDGE_TASK_SCOPE_CHANGED")
        return publish_index(c, outcome) | {
            "result_ref": "/api/v1/knowledge/" + row["input_manifest"]["knowledge_id"] + "/indexes"
        }
    except ValueError:
        raise DomainError(
            "KNOWLEDGE_INDEX_PUBLICATION_REJECTED", "来源或索引输入已变化，未发布", 409
        ) from None
