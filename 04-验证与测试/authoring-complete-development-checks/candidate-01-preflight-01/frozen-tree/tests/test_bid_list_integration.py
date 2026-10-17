"""不完整base行不能毒死实际项目集合；游标分母与投影集合完全一致。"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import connection, required

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BID_LIST_INTEGRATION") != "1", reason="需独立非owner PG"
)


def test_base_only_and_missing_stream_do_not_poison_paginated_business_workunits():
    actor, login, password = uuid4(), "list-boundary-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成项目列表验证')", (actor,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
        incomplete = uuid4()
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'仅base不属于完整业务单元','{}',%s)",
            (incomplete, actor),
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
        created = set()
        for _ in range(3):
            response = client.post(
                "/api/v1/bids",
                json={"title": "合成完整业务单元"},
                headers={"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex},
            )
            assert response.status_code == 201
            created.add(response.json()["id"])
        with connection() as c:
            half = uuid4()
            c.execute(
                "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'缺事件流的半初始化单元','{}',%s)",
                (half, actor),
            )
            c.execute(
                "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) SELECT %s,round_id,bidder_id,'working' FROM bid_profiles WHERE bid_id=%s",
                (half, next(iter(created))),
            )
            c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (half,))
        with connection() as c:
            # 新插入的不完整行排在最新位置，limit过滤后才能计算has_more/cursor。
            latest = uuid4()
            c.execute(
                "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'最新但缺完整投影','{}',%s)",
                (latest, actor),
            )
        with connection() as c:
            expected = {
                str(row["id"])
                for row in c.execute(
                    "SELECT b.id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id JOIN stream_heads s ON s.bid_id=b.id JOIN review_slots r ON r.bid_id=b.id"
                ).fetchall()
            }
        seen = set()
        cursor = None
        for _ in range(1000):
            params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
            page = client.get("/api/v1/bids", params=params)
            assert page.status_code == 200
            ids = {item["id"] for item in page.json()["items"]}
            assert not seen & ids
            seen.update(ids)
            if not page.json()["has_more"]:
                assert page.json()["next_cursor"] is None
                break
            assert len(ids) == 2 and page.json()["next_cursor"]
            cursor = page.json()["next_cursor"]
        else:
            raise AssertionError("集合分页没有有限结束")
        assert seen == expected and created <= seen
        assert str(incomplete) not in seen and str(latest) not in seen and str(half) not in seen
        missing = client.get(f"/api/v1/bids/{incomplete}")
        unknown = client.get(f"/api/v1/bids/{uuid4()}")
        assert missing.status_code == 404 and unknown.status_code == 404
