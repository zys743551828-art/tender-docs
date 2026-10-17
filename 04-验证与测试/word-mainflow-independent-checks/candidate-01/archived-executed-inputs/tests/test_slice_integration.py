"""实际PG/S3与Kafka切片自测；需专用空资料开发环境，保留合成审计。"""

import os
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import ROOT, connection, required, sha, uid, validate

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SLICE_INTEGRATION") != "1", reason="需要专用真实PG/Kafka/S3开发环境"
)


def pdf_bytes():
    content = b"BT /F1 12 Tf 40 80 Td (Tender technical material physical page test.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    return (
        data
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )


def wait_operation(client, identity):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        row = client.get("/api/v1/operations/" + identity).json()
        if row["state"] in ["succeeded", "failed", "cancelled"]:
            return row
        time.sleep(0.1)
    raise AssertionError("实际Worker未在期限内完成")


def test_real_pdf_slice_and_command_replay():
    login = "devtest-" + uuid4().hex
    password = uuid4().hex
    with connection() as c:
        member = uid()
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,%s)", (member, "合成切片自测成员")
        )
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (member, login, password_hash(password)),
        )
    workers = [
        subprocess.Popen([sys.executable, "-m", "apps.worker", mode], cwd=ROOT)
        for mode in ["relay", "consume", "reconcile"]
    ]
    try:
        with TestClient(app) as client:
            origin = required("APP_ORIGIN")
            auth = client.post(
                "/api/v1/auth/login",
                json={"login_name": login, "password": password},
                headers={"Origin": origin},
            )
            assert auth.status_code == 200, auth.text
            csrf = auth.json()["csrf_token"]

            def headers(key=None, tag=None):
                return {
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": key or uuid4().hex,
                } | ({"If-Match": tag} if tag else {})

            assert client.get("/api/v1/auth/session").json()["csrf_token"] == csrf
            assert (
                client.post(
                    "/api/v1/bids",
                    json={"title": "拒绝伪造请求"},
                    headers={"Origin": origin, "Idempotency-Key": uuid4().hex},
                ).status_code
                == 403
            )
            key = uuid4().hex
            created = client.post(
                "/api/v1/bids", json={"title": "合成PDF纵向自测"}, headers=headers(key)
            )
            assert created.status_code == 201, created.text
            bid = created.json()["id"]
            tag = created.headers["etag"]
            repeated = client.post(
                "/api/v1/bids", json={"title": "合成PDF纵向自测"}, headers=headers(key)
            )
            assert (
                repeated.json()["id"] == bid and repeated.headers["Idempotent-Replayed"] == "true"
            )
            batch = client.post(
                f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers()
            ).json()["id"]
            data = pdf_bytes()
            grant = client.post(
                f"/api/v1/bids/{bid}/uploads",
                json={
                    "batch_id": batch,
                    "role": "body",
                    "filename": "synthetic.pdf",
                    "byte_size": len(data),
                    "declared_sha256": sha(data),
                    "target": {"kind": "new_file"},
                },
                headers=headers(),
            )
            assert grant.status_code == 200, grant.text
            g = grant.json()
            response = httpx.put(g["upload_url"], content=data, trust_env=False)
            assert response.status_code == 200, response.text
            complete = client.post(
                f"/api/v1/uploads/{g['upload_id']}/complete",
                json={"parts": [], "declared_sha256": sha(data)},
                headers=headers(),
            )
            assert complete.status_code == 202, complete.text
            op = wait_operation(client, complete.json()["operation"]["id"])
            assert op["state"] == "succeeded", op
            verified = client.get(f"/api/v1/uploads/{g['upload_id']}").json()
            assert verified["state"] == "verified", verified
            commit = client.post(
                f"/api/v1/bids/{bid}/upload-batches/{batch}/commit",
                json={
                    "accepted_upload_ids": [verified["id"]],
                    "accepted_binding_ids": [verified["binding_id"]],
                    "explicit_partial_acceptance": False,
                },
                headers=headers(tag=tag),
            )
            assert commit.status_code == 202, commit.text
            parsed = wait_operation(client, commit.json()["operation"]["id"])
            assert parsed["state"] == "succeeded", parsed
            material = client.get(f"/api/v1/bids/{bid}/materials").json()
            doc = material["documents"][0]
            document_metadata = client.get(f"/api/v1/documents/{doc['id']}")
            assert document_metadata.status_code == 200
            validate("DocumentView", document_metadata.json())
            assert document_metadata.json()["id"] == doc["id"]
            evidence = client.get(f"/api/v1/documents/{doc['id']}/evidence").json()
            assert evidence["coverage"] == "partial" and len(evidence["items"]) == 1, evidence
            assert "physical page test" in evidence["items"][0]["quote"]
            unavailable = client.post(
                f"/api/v1/bids/{bid}/reviews",
                json={
                    "body_version_id": doc["id"],
                    "material_revision": material["material_revision"],
                    "trigger": "manual",
                },
                headers=headers(),
            )
            assert (
                unavailable.status_code == 503
                and unavailable.json()["code"] == "MODEL_NOT_CONFIGURED"
            )
            # 真实上传第二版，再从旧ID查元数据及previous链，不从当前materials猜历史。
            batch2 = client.post(
                f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers()
            ).json()["id"]
            data2 = data.replace(b"physical page test", b"physical page edit")
            grant2 = client.post(
                f"/api/v1/bids/{bid}/uploads",
                json={
                    "batch_id": batch2,
                    "role": "body",
                    "filename": "synthetic-v2.pdf",
                    "byte_size": len(data2),
                    "declared_sha256": sha(data2),
                    "target": {
                        "kind": "new_version",
                        "file_id": doc["file_id"],
                        "expected_file_revision": doc["file_head_revision"],
                    },
                },
                headers=headers(),
            )
            assert grant2.status_code == 200, grant2.text
            g2 = grant2.json()
            assert httpx.put(g2["upload_url"], content=data2, trust_env=False).status_code == 200
            completed2 = client.post(
                f"/api/v1/uploads/{g2['upload_id']}/complete",
                json={"parts": [], "declared_sha256": sha(data2)},
                headers=headers(),
            )
            assert (
                completed2.status_code == 202
                and wait_operation(client, completed2.json()["operation"]["id"])["state"]
                == "succeeded"
            )
            verified2 = client.get(f"/api/v1/uploads/{g2['upload_id']}").json()
            bidtag = client.get(f"/api/v1/bids/{bid}").headers["etag"]
            committed2 = client.post(
                f"/api/v1/bids/{bid}/upload-batches/{batch2}/commit",
                json={
                    "accepted_upload_ids": [verified2["id"]],
                    "accepted_binding_ids": [verified2["binding_id"]],
                    "explicit_partial_acceptance": False,
                },
                headers=headers(tag=bidtag),
            )
            assert (
                committed2.status_code == 202
                and wait_operation(client, committed2.json()["operation"]["id"])["state"]
                == "succeeded"
            )
            current_doc = client.get(f"/api/v1/bids/{bid}/materials").json()["documents"][0]
            historical = client.get(f"/api/v1/documents/{doc['id']}").json()
            validate("DocumentView", historical)
            assert (
                current_doc["id"] != doc["id"] and current_doc["previous_version_id"] == doc["id"]
            )
            assert historical["file_revision"] == 1 and historical["file_head_revision"] == 3
            assert historical["file_head_version_id"] == current_doc["id"]
            assert (
                client.get(f"/api/v1/documents/{doc['id']}/evidence").json()["items"][0][
                    "document_version_id"
                ]
                == doc["id"]
            )
            shared_login = "devtest-shared-" + uuid4().hex
            shared_password = uuid4().hex
            with connection() as c:
                shared_member = uid()
                c.execute(
                    "INSERT INTO members(id,display_name) VALUES(%s,%s)",
                    (shared_member, "合成共享访问成员"),
                )
                c.execute(
                    "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
                    (shared_member, shared_login, password_hash(shared_password)),
                )
            with TestClient(app) as other:
                other_auth = other.post(
                    "/api/v1/auth/login",
                    json={"login_name": shared_login, "password": shared_password},
                    headers={"Origin": origin},
                )
                assert other_auth.status_code == 200
                created_other = other.post(
                    "/api/v1/bids",
                    json={"title": "合成共享访问成员的独立项目"},
                    headers={
                        "Origin": origin,
                        "X-CSRF-Token": other_auth.json()["csrf_token"],
                        "Idempotency-Key": uuid4().hex,
                    },
                )
                assert created_other.status_code == 201
                shared_document = other.get(f"/api/v1/documents/{doc['id']}")
                assert (
                    shared_document.status_code == 200 and shared_document.json()["bid_id"] == bid
                )
                validate("DocumentView", shared_document.json())
            for schema, value in [
                ("AuthView", auth.json()),
                ("Bid", created.json()),
                ("UploadGrant", g),
                ("Accepted", complete.json()),
                ("Operation", op),
                ("UploadView", verified),
                ("Accepted", commit.json()),
                ("MaterialView", material),
                ("Error", unavailable.json()),
            ]:
                validate(schema, value)
            for value in evidence["items"]:
                validate("EvidenceView", value)
            assert client.post("/api/v1/auth/logout", headers=headers()).status_code == 204
            assert client.get("/api/v1/auth/session").status_code == 401
            assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 401
    finally:
        for process in workers:
            process.terminate()
        for process in workers:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
