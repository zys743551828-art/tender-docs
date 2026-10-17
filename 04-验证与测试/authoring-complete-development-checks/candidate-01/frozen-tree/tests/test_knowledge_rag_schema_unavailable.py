"""Read-only preexisting-schema probe; never creates/grants/repairs tables."""

import os

import pytest

from packages.domain.runtime import DomainError, assert_role, connection
from packages.knowledge.rag_repository import assert_schema

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_RAG_SCHEMA_ABSENT") != "1",
    reason="只读连接明确缺少知识RAG表的受限API库",
)


def test_missing_schema_reports_unavailable_without_business_sql_or_owner_fallback():
    assert_role()
    with connection() as c:
        c.execute("SET TRANSACTION READ ONLY")
        assert (
            c.execute("SELECT to_regclass('retrieval.knowledge_indexes') AS relation").fetchone()[
                "relation"
            ]
            is None
        )
        with pytest.raises(DomainError) as error:
            assert_schema(c)
        assert error.value.code == "KNOWLEDGE_RAG_NOT_READY" and error.value.status == 503
