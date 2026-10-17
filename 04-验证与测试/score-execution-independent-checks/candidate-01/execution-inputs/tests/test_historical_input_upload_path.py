"""Ordinary current upload -> offline fact -> historical authority.

Uses real parser CPU in isolated child, real limited PG roles and login/ASGI. Storage
is explicitly in-memory; only native DeepSeek MockTransport, no external calls/consumers.
"""

import io
import json
import os
import subprocess  # nosec B404 -- fixed trusted local parser, no shell
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.reviews import tick
from packages.domain.runtime import clean, connection, sha
from tests.test_research_integration import client as research_client  # noqa: F401 -- fixture
from tests.test_review_integration import pdf_bytes, pump

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HISTORICAL_INPUT_UPLOAD_PATH") != "1",
    reason="Needs NEW reviewed fresh input DB and local parser environment",
)
ROOT = Path(__file__).resolve().parents[1]


class LocalTestParser:
    def __init__(self):
        identity = self._run("identity", b"")
        self.parser_digest, self.config_digest = (
            identity["parser_digest"],
            identity["config_digest"],
        )

    @staticmethod
    def _run(mode, data):
        process = subprocess.run(  # nosec B603 -- fixed local argv, strict environment
            [
                str(ROOT / ".venv-parsing/bin/python"),
                "-I",
                str(ROOT / "tests/test_historical_parser_helper.py"),
                mode,
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=45,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            cwd=ROOT,
        )
        return json.loads(process.stdout)

    def parse(self, data):
        return self._run("parse", data)


def test_actual_new_upload_then_historical_input_keeps_real_now_and_no_old_review(
    research_client,  # noqa: F811 -- imported fixture
    monkeypatch,
):
    from apps.api.historical_input_routes import router as inputs
    from apps.api.historical_routes import router as proofs
    from apps.api.main import app
    from apps.worker import handlers
    from packages.domain import parsing_profile

    base_web, _, headers, objects = research_client
    app.include_router(proofs)
    app.include_router(inputs)
    parser = LocalTestParser()
    monkeypatch.setattr(parsing_profile, "configured_parser", lambda: parser)
    monkeypatch.setattr(handlers, "configured_parser", lambda: parser)
    monkeypatch.setattr(storage, "bucket", lambda: "explicit-memory-test")

    def put(key, raw, media):
        objects[key] = raw
        return {"ETag": sha(raw)}

    monkeypatch.setattr(storage, "put", put)

    class MemoryClient:
        def head_object(self, *, Bucket, Key):
            return {"ContentLength": len(objects[Key]), "ETag": sha(objects[Key])}

        def get_object(self, *, Bucket, Key):
            return {"ContentLength": len(objects[Key]), "Body": io.BytesIO(objects[Key])}

    monkeypatch.setattr(storage, "client", lambda: MemoryClient())
    requests = []

    def model_response(request):
        requests.append(request)
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        blocks = [
            block for document in payload["documents"] for block in document["evidence_blocks"]
        ]
        text = blocks[0]["text_content"]
        output = {
            "rules": [
                {
                    "kind": "scoring",
                    "statement": text,
                    "conditions": [],
                    "exceptions": [],
                    "scope": {"bid_id": payload["bid_id"], "description": "合成项目"},
                    "maximum": "60",
                    "bands": [
                        {
                            "label": "完整",
                            "description": text,
                            "points": "60",
                            "evidence_ids": [blocks[0]["id"]],
                        }
                    ],
                    "evidence_quotes": [{"evidence_id": blocks[0]["id"], "quote": text}],
                }
            ],
            "ambiguities": [],
            "read_evidence_ids": [block["id"] for block in blocks],
        }
        return httpx.Response(
            200,
            json={
                "id": "synthetic-" + uuid4().hex,
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    model = DeepSeekClient(
        api_key="explicit-synthetic-no-real-calls", transport=httpx.MockTransport(model_response)
    )
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: model))
    with TestClient(app) as web:
        web.cookies.update(base_web.cookies)
        company = web.post(
            "/api/v1/identity/bidders",
            json={
                "name": "合成历史上传主体" + uuid4().hex[:6],
                "reason": "显式合成身份，不宣称历史属实",
            },
            headers=headers(),
        )
        assert company.status_code == 201, company.text
        created = web.post(
            "/api/v1/bids",
            json={"title": "合成普通新上传历史重建", "bidder_id": company.json()["bidder"]["id"]},
            headers=headers(),
        )
        assert created.status_code == 201, created.text
        bid = created.json()["id"]
        batch = web.post(f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers())
        assert batch.status_code == 201, batch.text
        uploads = []
        for role in ("tender", "body"):
            data = pdf_bytes(
                f"Explicit synthetic original {role} document. Complete technical plan 60 points. Exact old content has its own proof. {uuid4().hex}"
            )
            grant = web.post(
                f"/api/v1/bids/{bid}/uploads",
                json={
                    "batch_id": batch.json()["id"],
                    "role": role,
                    "filename": role + ".pdf",
                    "byte_size": len(data),
                    "declared_sha256": sha(data),
                    "target": {"kind": "new_file"},
                },
                headers=headers(),
            )
            assert grant.status_code == 200, grant.text
            url = urlsplit(grant.json()["upload_url"])
            transfer = web.put(
                url.path + "?" + url.query,
                content=data,
                headers={k: v for k, v in headers().items() if k != "Idempotency-Key"}
                | {"Content-Type": "application/pdf", "Content-Length": str(len(data))},
            )
            assert transfer.status_code == 200, transfer.text
            complete = web.post(
                f"/api/v1/uploads/{grant.json()['upload_id']}/complete",
                json={"parts": [], "declared_sha256": sha(data)},
                headers=headers(),
            )
            assert complete.status_code == 202, complete.text
            assert pump(complete.json()["operation"]["id"])["state"] == "succeeded"
            uploads.append(web.get(f"/api/v1/uploads/{grant.json()['upload_id']}").json())
        commit = web.post(
            f"/api/v1/bids/{bid}/upload-batches/{batch.json()['id']}/commit",
            json={
                "accepted_upload_ids": [u["id"] for u in uploads],
                "accepted_binding_ids": [u["binding_id"] for u in uploads],
                "explicit_partial_acceptance": False,
            },
            headers=headers(web.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert commit.status_code == 202, commit.text
        assert pump(commit.json()["operation"]["id"])["state"] == "succeeded"
        tick(bid)
        state = web.get(f"/api/v1/bids/{bid}/review-state").json()["request"]
        assert pump(state["prepare_operation_id"])["state"] == "succeeded"
        actual = next(u["document_version_id"] for u in uploads if u["filename"] == "body.pdf")
        offline = web.post(
            f"/api/v1/bids/{bid}/submission-facts",
            json={
                "actual_document_version_id": actual,
                "submitted_at": "2019-02-02T00:00:00Z",
                "evidence_ref": uploads[0]["document_version_id"],
                "explanation": "明确合成历史实交补录，无当年review，不声明平台曾核验",
            },
            headers=headers(web.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert offline.status_code == 201, offline.text
        submission = offline.json()["id"]
        with connection() as c:
            original = c.execute(
                "SELECT review_id,submitted_at,created_at FROM submission_records WHERE id=%s",
                (submission,),
            ).fetchone()
            documents = clean(
                c.execute(
                    "SELECT d.id AS document_version_id,b.digest AS blob_digest,e.parse_id,e.id AS evidence_id,e.physical_page,e.digest AS evidence_digest,e.text_content AS quote,n.known_at,d.obtained_at FROM document_versions d JOIN blobs b ON b.id=d.blob_id JOIN parse_runs p ON p.document_version_id=d.id JOIN evidence_blocks e ON e.parse_id=p.id JOIN retrieval.source_nodes n ON n.document_version_id=d.id WHERE d.bid_id=%s AND EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=n.id) ORDER BY d.id,e.id",
                    (bid,),
                ).fetchall()
            )
            projection = clean(
                c.execute(
                    "SELECT bidder.id AS bidder_id,bidder.name AS bidder_name,r.id AS round_id,p.id AS engineering_project_id,p.title AS engineering_title,p.region,p.engineering_type,r.lot,r.round_no FROM bid_profiles b JOIN bidders bidder ON bidder.id=b.bidder_id JOIN bid_rounds r ON r.id=b.round_id JOIN engineering_projects p ON p.id=r.engineering_project_id WHERE b.bid_id=%s",
                    (bid,),
                ).fetchone()
            )
            rule = c.execute(
                "SELECT id FROM rule_sets WHERE bid_id=%s ORDER BY revision DESC LIMIT 1", (bid,)
            ).fetchone()
        assert original["review_id"] is None
        assert all(
            d["known_at"][:4] != "2019" and d["obtained_at"][:4] != "2019" for d in documents
        )
        proof_rows = [
            {k: v for k, v in d.items() if k not in {"known_at", "obtained_at"}}
            | {"explanation": "显式合成证明，本测试仅验证工程机制"}
            for d in documents
        ]

        def context():
            return web.get(f"/api/v1/historical-proof/submissions/{submission}/context")

        subject = web.post(
            "/api/v1/historical-proof/subjects",
            json={
                "submission_id": submission,
                "proposed_identity": projection,
                "proof_refs": [proof_rows[0]],
                "declared_data_origin": "synthetic",
                "reason": "明确合成历史主体确认",
            },
            headers=headers(context().headers["etag"]),
        )
        assert subject.status_code == 201, subject.text
        confirmed = web.post(
            f"/api/v1/historical-proof/subjects/{subject.json()['id']}/confirm",
            json={"reason": "显式合成人工核验"},
            headers=headers(subject.headers["etag"]),
        )
        assert confirmed.status_code == 200, confirmed.text
        claims = []
        for proof in proof_rows:
            kinds = ["content_existed", "known_to_bidder"] + (
                ["effective"] if proof["document_version_id"] != actual else []
            )
            claim = web.post(
                "/api/v1/historical-proof/source-claims",
                json={
                    "submission_id": submission,
                    "document_version_id": proof["document_version_id"],
                    "historical_subject_authority_id": subject.json()["id"],
                    "declared_data_origin": "synthetic",
                    "reason": "合成每个精确版本的历史依据",
                    "facts": [
                        {
                            "fact_kind": kind,
                            "claimed_at": "2019-01-01T00:00:00Z",
                            "proof_refs": [proof],
                            "explanation": "合成独立事实",
                        }
                        for kind in kinds
                    ],
                },
                headers=headers(context().headers["etag"]),
            )
            assert claim.status_code == 201, claim.text
            confirmed = web.post(
                f"/api/v1/historical-proof/source-claims/{claim.json()['id']}/confirm",
                json={"reason": "明确合成核验"},
                headers=headers(claim.headers["etag"]),
            )
            assert confirmed.status_code == 200, confirmed.text
            claims.append(claim.json()["id"])
        input_context = web.get(f"/api/v1/historical-inputs/submissions/{submission}/context")
        result = web.post(
            "/api/v1/historical-inputs",
            json={
                "submission_id": submission,
                "historical_subject_authority_id": subject.json()["id"],
                "source_claim_ids": claims,
                "material_version_ids": [
                    u["document_version_id"] for u in uploads if u["filename"] == "tender.pdf"
                ],
                "rule_set_id": str(rule["id"]),
                "reason": "合成普通上传到当前创建历史输入完整路径",
            },
            headers=headers(input_context.headers["etag"]),
        )
        assert result.status_code == 201, result.text
        assert result.json()["current"] and result.json()["formal_eligibility"] == "unavailable"
        assert len(requests) == 1  # Only explicit Mock prepare extraction, zero paid calls.
