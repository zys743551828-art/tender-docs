"""真实非owner PG/HTTP闸门边界；SQL文档夹具不冒称已走上传链路。"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import connection, required, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SUBMISSION_GATE_INTEGRATION") != "1", reason="需专用真实PG"
)


def test_empty_gate_is_explicit_and_known_report_bytes_cannot_be_relabelled_across_projects():
    actor, login, password = uuid4(), uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成提交边界')", (actor,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
    with TestClient(app) as client, TestClient(app) as anonymous:
        logged = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": required("APP_ORIGIN")},
        )
        assert logged.status_code == 200

        def headers():
            return {
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": logged.json()["csrf_token"],
                "Idempotency-Key": uuid4().hex,
            }

        bids = [
            client.post("/api/v1/bids", json={"title": "合成用途边界"}, headers=headers()).json()[
                "id"
            ]
            for _ in range(2)
        ]
        path = f"/api/v1/bids/{bids[0]}/submission-gate"
        assert anonymous.get(path).status_code == 401
        empty = client.get(path)
        assert empty.status_code == 200 and empty.json()["body_version_id"] is None
        assert not empty.json()["allowed"]
        assert {item["code"] for item in empty.json()["blockers"]} >= {
            "ACTUAL_BODY_REQUIRED",
            "CURRENT_REVIEW_REQUIRED",
            "BID_IDENTITY_UNCONFIRMED",
        }
        Draft202012Validator(
            {"$ref": "#/components/schemas/GateView", "components": app.openapi()["components"]}
        ).validate(empty.json())
        # 元数据夹具表示同一内容寻址blob被另一个项目再次声称为正文。
        # 没有上传成功/模型质量/真实原件字节的验收断言。
        blob, report, body = uuid4(), uuid4(), uuid4()
        digest = sha(uuid4().bytes)
        with connection() as c:
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,17,'application/pdf')",
                (blob, digest, "blobs/" + digest),
            )
            for bid, identity, kind in (
                (bids[1], report, "review_report"),
                (bids[0], body, "bid_body"),
            ):
                c.execute(
                    "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成用途.pdf',now(),%s,%s)",
                    (identity, bid, blob, kind, actor),
                )
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bids[0],))
            c.execute("UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (body, bids[0]))
        guarded = client.get(path)
        assert guarded.status_code == 200 and not guarded.json()["allowed"]
        assert "SUBMISSION_ARTIFACT_PURPOSE_CONFLICT" in {
            item["code"] for item in guarded.json()["blockers"]
        }
        assert client.get(f"/api/v1/bids/{uuid4()}/submission-gate").status_code == 404
