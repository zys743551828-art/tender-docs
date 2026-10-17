"""Kafka engine hooks: model computation outside transactions, fenced publication inside."""

from __future__ import annotations

import json
from uuid import UUID, uuid5

from packages.domain.runtime import DomainError, connection, sha
from packages.rag.http_port import DenseHttp, LexicalHttp, RuntimeUnavailable
from packages.rag.indexing import INDEX_NAMESPACE, compute_index, prepare_index, publish_index


def task_payload(parse_id, dense: DenseHttp) -> dict:
    profile_id = uuid5(INDEX_NAMESPACE, json.dumps(dense.identity, sort_keys=True))
    return {
        "id": str(parse_id),
        "embedding_profile_id": str(profile_id),
        "lexical_dictionary_digest": dense.lexical_dictionary_digest,
        "chunking": "evidence_block_offsets:800:1200:120:v1",
    }


def compute(message, lease=None):
    try:
        dense = DenseHttp()
        parse_id = UUID(message["payload_ref"])
        if (
            message["step_key"] != "index-parse"
            or sha(task_payload(parse_id, dense)) != message["input_digest"]
        ):
            raise DomainError("RAG_TASK_PROFILE_CHANGED", "索引任务模型或方法身份已变化", 409)
        with connection(True) as c:
            prepared = prepare_index(
                c, UUID(message["bid_id"]), parse_id, dense, LexicalHttp(dense)
            )
        return compute_index(prepared, dense)
    except RuntimeUnavailable:
        raise DomainError(
            "RAG_RUNTIME_UNAVAILABLE", "本地检索模型暂不可用", 503, retryable=True
        ) from None
    except ValueError:
        raise DomainError("RAG_INDEX_INPUT_INVALID", "索引资料或模型身份不符合要求", 409) from None


def publish(connection, message, outcome):
    try:
        # No HTTP/CPU inference occurs while the engine holds its fenced transaction.
        result = publish_index(connection, outcome)
        return {
            key: str(value) if isinstance(value, UUID) else value for key, value in result.items()
        } | {"result_ref": f"/api/v1/bids/{message['bid_id']}/materials"}
    except ValueError:
        raise DomainError(
            "RAG_INDEX_PUBLICATION_REJECTED", "索引来源已变化或撤回，未发布", 409
        ) from None
