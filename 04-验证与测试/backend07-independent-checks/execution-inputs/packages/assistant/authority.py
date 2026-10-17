"""Worker-safe source-head guard; full project/fact authority remains API-owned."""

from uuid import UUID

from packages.domain.runtime import DomainError
from packages.knowledge.sources import authority_manifest, lock_graph, lock_heads


def assert_source_guards(c, manifest):
    lock_graph(c)
    if manifest["kind"] == "authoring":
        guards = [
            manifest["plan"]["authority_manifest"],
            manifest["project_memory"]["source_authorities"],
        ]
    else:
        guards = [m["source_authorities"] for m in manifest["context"]["projects"]]
        for source in manifest["context"]["sources"]:
            provenance = source["provenance"]
            kind = provenance["kind"]
            if kind == "original_document":
                guards.append(provenance["source_authority"])
            elif kind == "draft_section":
                guards.append(provenance["authority"])
            elif kind == "knowledge_retrieval_unit":
                guards.append(provenance["unit"]["authority_manifest"])
            elif kind == "structured_project_memory":
                guards.append(provenance["memory"]["source_authorities"])
    for expected in guards:
        if not expected:
            continue
        ids = sorted({UUID(row["source_id"]) for row in expected}, key=str)
        lock_heads(c, ids)
        clock = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
        current = authority_manifest(c, ids, clock)
        current = [{k: v for k, v in row.items() if k in expected[0]} for row in current]
        if current != expected:
            raise DomainError(
                "ASSISTANT_SOURCE_CHANGED", "来源及祖先效力已变化，未开始新的模型调用", 409
            )
