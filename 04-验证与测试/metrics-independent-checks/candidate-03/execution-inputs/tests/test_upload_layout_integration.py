"""真实非owner PG/ASGI跨浏览器读取；不将合成multipart元数据称为OSS验收。"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import connection, required, sha
from packages.uploads.protocol import PART_SIZE, pack

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_UPLOAD_LAYOUT_INTEGRATION") != "1", reason="需独立PG"
)


def test_new_browser_reads_authoritative_layout_and_digest_without_local_grant():
    actor, login, password = uuid4(), uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成传输恢复')", (actor,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
    with TestClient(app) as first, TestClient(app) as second, TestClient(app) as anonymous:
        sessions = []
        for client in (first, second):
            logged = client.post(
                "/api/v1/auth/login",
                json={"login_name": login, "password": password},
                headers={"Origin": required("APP_ORIGIN")},
            )
            assert logged.status_code == 200
            sessions.append(logged.json()["csrf_token"])

        def headers(csrf, tag=None):
            return {
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": csrf,
                "Idempotency-Key": uuid4().hex,
            } | ({"If-Match": tag} if tag else {})

        bid = first.post(
            "/api/v1/bids", json={"title": "合成布局恢复"}, headers=headers(sessions[0])
        ).json()["id"]
        batch = first.post(
            f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers(sessions[0])
        ).json()["id"]
        declared = sha(b"synthetic-large-file-identity")
        create = first.post(
            f"/api/v1/bids/{bid}/uploads",
            json={
                "batch_id": batch,
                "filename": "合成布局.pdf",
                "role": "tender",
                "byte_size": PART_SIZE + 17,
                "declared_sha256": declared,
                "target": {"kind": "new_file"},
            },
            headers=headers(sessions[0]),
        )
        assert create.status_code == 200
        identity = create.json()["upload_id"]
        path = f"/api/v1/uploads/{identity}/layout"
        assert anonymous.get(path).status_code == 401
        single = second.get(path)
        assert single.status_code == 200 and single.json()["mode"] == "single"
        assert (
            single.json()["byte_size"] == PART_SIZE + 17
            and single.json()["declared_sha256"] == declared
        )
        assert single.json()["part_count"] == 1 and single.json()["part_size"] == PART_SIZE + 17
        refreshed = second.post(
            f"/api/v1/uploads/{identity}/authorizations",
            json={"part_numbers": [1]},
            headers=headers(sessions[1], single.headers["etag"]),
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["revision"] == single.json()["revision"] + 1
        assert (
            first.post(
                f"/api/v1/uploads/{identity}/authorizations",
                json={"part_numbers": [1]},
                headers=headers(sessions[0], single.headers["etag"]),
            ).status_code
            == 412
        )
        # 只注入专用分片布局夹具；不创建远端会话、不伪装完成分片或原件核验。
        with connection() as c:
            row = c.execute("SELECT staging_key FROM uploads WHERE id=%s", (identity,)).fetchone()
            c.execute(
                "UPDATE upload_transfers SET mode='multipart',part_size=%s,part_count=2,provider_upload_id=%s,revision=revision+1 WHERE upload_id=%s",
                (
                    PART_SIZE,
                    pack(
                        "ready",
                        key=row["staging_key"],
                        nonce=str(uuid4()),
                        provider_id="synthetic-layout-no-remote-session",
                    ),
                    identity,
                ),
            )
        batch_restored = second.get(f"/api/v1/bids/{bid}/upload-batches/{batch}")
        assert batch_restored.status_code == 200
        restored_id = batch_restored.json()["uploads"][0]["id"]
        assert restored_id == identity
        restored = second.get(f"/api/v1/uploads/{restored_id}/layout")
        assert restored.status_code == 200
        result = restored.json()
        assert (
            result["mode"] == "multipart"
            and result["part_size"] == PART_SIZE
            and result["part_count"] == 2
        )
        assert result["byte_size"] == PART_SIZE + 17 and result["declared_sha256"] == declared
        assert (
            result["state"] == "authorized" and restored.headers["etag"] != single.headers["etag"]
        )
        actual = app.openapi()
        Draft202012Validator(
            {"$ref": "#/components/schemas/UploadLayoutView", "components": actual["components"]}
        ).validate(result)
        assert set(result) == {
            "upload_id",
            "bid_id",
            "mode",
            "part_size",
            "part_count",
            "byte_size",
            "declared_sha256",
            "revision",
            "state",
            "session_expires_at",
        }
