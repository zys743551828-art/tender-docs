"""Actual synthetic uploaded PDF/Kafka index/current ASGI retrieval evidence; no paid calls."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx

from apps.api.main import app
from packages.domain.runtime import ROOT, connection, required, validate


async def probe(members: Path) -> dict:
    if members.stat().st_mode & 0o077:
        raise RuntimeError("Test members must be private")
    account = json.loads(members.read_text())[0]
    with connection() as c:
        row = c.execute(
            """SELECT DISTINCT p.bid_id FROM parse_runs p
               JOIN retrieval.chunks ch ON ch.parse_id=p.id
               JOIN retrieval.embeddings e ON e.chunk_id=ch.id
               JOIN bid_profiles bp ON bp.bid_id=p.bid_id
               WHERE p.document_version_id=bp.current_body_id LIMIT 1"""
        ).fetchone()
    if not row:
        raise RuntimeError("No actual uploaded and indexed body")
    origin = required("APP_ORIGIN")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"login_name": account["login"], "password": account["password"]},
            headers={"Origin": origin},
        )
        if login.status_code != 200:
            raise RuntimeError("Test login failed")
        headers = {
            "Origin": origin,
            "X-CSRF-Token": login.json()["csrf_token"],
            "Idempotency-Key": str(uuid4()),
        }
        body = {
            "query": "municipal tender construction",
            "context": "body_evidence",
            "bid_id": str(row["bid_id"]),
            "limit": 8,
        }
        response = await client.post("/api/v1/retrievals", json=body, headers=headers)
        if response.status_code != 200:
            raise RuntimeError("Current retrieval endpoint failed")
        result = response.json()
        validate("RetrievalResult", result)
        if not result["hits"]:
            raise RuntimeError("Expected readable synthetic PDF evidence")
        replay = await client.post("/api/v1/retrievals", json=body, headers=headers)
        if (
            replay.status_code != 200
            or replay.json() != result
            or replay.headers.get("Idempotent-Replayed") != "true"
        ):
            raise RuntimeError("Retrieval replay changed")
    with connection() as c:
        for hit in result["hits"]:
            citation = c.execute(
                """SELECT c.id,ch.physical_page,n.document_version_id,ch.text_content
                   FROM retrieval.citations c JOIN retrieval.chunks ch ON ch.id=c.chunk_id
                   JOIN retrieval.source_nodes n ON n.id=c.source_id WHERE c.id=%s""",
                (hit["citation_id"],),
            ).fetchone()
            if (
                not citation
                or str(citation["document_version_id"]) != hit["source_version_id"]
                or citation["physical_page"] != hit["physical_page"]
                or citation["text_content"] != hit["excerpt"]
            ):
                raise RuntimeError("Citation original, page or verbatim excerpt mismatch")
        trace = c.execute(
            "SELECT excluded_reasons FROM retrieval.retrieval_traces WHERE id=%s",
            (result["trace_id"],),
        ).fetchone()["excluded_reasons"]
        operation = c.execute(
            """SELECT count(*) AS count FROM operations o JOIN effect_receipts e ON e.operation_id=o.id
               WHERE o.bid_id=%s AND o.kind='index-parse' AND o.state='succeeded'
               AND e.effect_key='index-parse'""",
            (row["bid_id"],),
        ).fetchone()
        if not operation["count"]:
            raise RuntimeError("No actual Kafka index effect receipt for this project")
    return {
        "passed": True,
        "scope": "Synthetic OSS PDF -> real Worker parse -> API coordinator -> Kafka index -> local BGE/PG -> current ASGI retrieval. Not business quality acceptance.",
        "api_transport": "ASGI current source; deployed HTTP snapshot tested separately",
        "http_status": 200,
        "hits": len(result["hits"]),
        "coverage": result["coverage"],
        "route": trace["route"],
        "citation_source_page_excerpt_verified": True,
        "idempotent_replay_equal": True,
        "paid_generation_calls": 0,
        "trace_id": result["trace_id"],
        "reranker_identity_recorded": bool(trace.get("reranker_identity")),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=Path, default=ROOT / ".local/external-test-members.json")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(probe(arguments.members))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        {
            key: result[key]
            for key in (
                "passed",
                "http_status",
                "hits",
                "coverage",
                "route",
                "paid_generation_calls",
            )
        }
    )


if __name__ == "__main__":
    main()
