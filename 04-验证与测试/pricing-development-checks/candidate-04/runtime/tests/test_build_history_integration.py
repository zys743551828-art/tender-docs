"""真实PG/ASGI构建历史可发现与有界分页；不执行渲染、不发模型请求。"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import connection, required

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BUILD_HISTORY_INTEGRATION") != "1", reason="需独立PG"
)


def test_builds_refresh_discovery_scope_cursor_and_single_view_equivalence():
    actor, login, password = uuid4(), "build-history-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成历史发现')", (actor,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        logged = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert logged.status_code == 200
        csrf = logged.json()["csrf_token"]

        def headers():
            return {"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex}

        bid = client.post("/api/v1/bids", json={"title": "合成构建历史"}, headers=headers()).json()[
            "id"
        ]
        other = client.post(
            "/api/v1/bids", json={"title": "合成游标隔离"}, headers=headers()
        ).json()["id"]
        empty = client.get(f"/api/v1/bids/{bid}/builds")
        assert empty.status_code == 200 and empty.json() == {
            "items": [],
            "has_more": False,
            "next_cursor": None,
        }
        draft_response = client.post(
            f"/api/v1/bids/{bid}/drafts",
            json={"title": "合成正文", "outline_template_version_id": None},
            headers=headers(),
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()
        template = client.get("/api/v1/authoring/capabilities").json()[
            "default_template_version_id"
        ]
        ids = set()
        for _ in range(3):
            built = client.post(
                f"/api/v1/bids/{bid}/builds",
                json={
                    "draft_id": draft["id"],
                    "draft_revision": draft["revision"],
                    "template_version_id": template,
                    "formats": ["docx"],
                    "follow_up": "none",
                },
                headers=headers(),
            )
            assert built.status_code == 202
            ids.add(built.json()["resource_id"])
        page1 = client.get(f"/api/v1/bids/{bid}/builds?limit=2")
        assert page1.status_code == 200 and page1.json()["has_more"]
        cursor = page1.json()["next_cursor"]
        page2 = client.get(f"/api/v1/bids/{bid}/builds", params={"limit": 2, "cursor": cursor})
        assert page2.status_code == 200 and not page2.json()["has_more"]
        assert {item["id"] for item in page1.json()["items"] + page2.json()["items"]} == ids
        wrong = client.get(f"/api/v1/bids/{other}/builds", params={"cursor": cursor})
        assert wrong.status_code == 422
        actual = app.openapi()
        for page in (page1, page2):
            Draft202012Validator(
                {"$ref": "#/components/schemas/BuildPage", "components": actual["components"]}
            ).validate(page.json())
            for item in page.json()["items"]:
                assert item == client.get(f"/api/v1/builds/{item['id']}").json()
        with TestClient(app) as second:
            logged_again = second.post(
                "/api/v1/auth/login",
                json={"login_name": login, "password": password},
                headers={"Origin": origin},
            )
            assert logged_again.status_code == 200
            restored = second.get(f"/api/v1/bids/{bid}/builds")
            assert (
                restored.status_code == 200
                and {item["id"] for item in restored.json()["items"]} == ids
            )
