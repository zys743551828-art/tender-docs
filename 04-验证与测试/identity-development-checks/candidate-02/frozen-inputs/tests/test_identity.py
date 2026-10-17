"""Real nonowner identity reuse, isolation, correction and HTTP conflict recovery."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from packages.domain.runtime import DomainError, assert_role, connection, uid
from packages.identity import repository as identities
from packages.identity.contracts import validate


@pytest.mark.parametrize(
    "value",
    [
        {"name": " ", "reason": "明确登记"},
        {"name": "合成公司", "reason": ""},
        {"name": "合成公司", "reason": "明确登记", "set_as_default": 1},
    ],
)
def test_invalid_identity_is_not_registration(value):
    with pytest.raises(DomainError):
        validate("RegisterBidder", value)


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_IDENTITY_PG") != "1",
    reason="Explicit nonowner PG integration profile required",
)


@pytest.fixture
def env():
    assert_role()
    with connection() as c:
        actor = uid()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成身份测试成员')", (actor,))
        company = identities.register_bidder(
            c, actor, "合成市政建设有限公司", "测试用明确登记，不核验资质", None
        )
        other = identities.register_bidder(
            c, actor, "合成市政建设集团有限公司", "近似名称必须保持独立主体", None
        )
    return {"actor": actor, "company": company, "other": other}


def new_project(c, env, body=None):
    data = {"title": "合成身份测试工程"} | (body or {})
    selected = identities.create_association(c, data)
    bid, stream = uid(), uid()
    c.execute(
        "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{}',%s)",
        (bid, data["title"], env["actor"]),
    )
    c.execute(
        "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
        (bid, selected["round_id"], selected["bidder_id"]),
    )
    c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (stream, bid))
    c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
    identities.record_binding(
        c, bid, env["actor"], None, reason="明确创建合成测试工程", confirmed=selected["confirmed"]
    )
    return bid


def test_same_company_reused_and_similar_names_stay_distinct(env):
    with connection() as c:
        a = new_project(c, env, {"bidder_id": env["company"]["id"]})
        b = new_project(c, env, {"bidder_id": env["company"]["id"]})
        other = new_project(c, env, {"bidder_id": env["other"]["id"]})
        sa, sb, so = [identities.snapshot(c, bid) for bid in (a, b, other)]
        assert sa["bidder_id"] == sb["bidder_id"] != so["bidder_id"]
        assert sa["engineering_project_id"] != sb["engineering_project_id"]
        assert sa["binding_status"] == sb["binding_status"] == so["binding_status"] == "confirmed"


def test_no_default_is_silently_applied_to_project(env):
    with connection() as c:
        identities.set_default(c, env["actor"], env["company"]["id"], None)
        bid = new_project(c, env)
        state = identities.snapshot(c, bid)
        assert state["binding_status"] == "pending"
        assert state["bidder_id"] != env["company"]["id"]
        with pytest.raises(DomainError) as error:
            identities.require_bidder(c, state["bidder_id"])
        assert error.value.code == "BIDDER_IDENTITY_UNCONFIRMED"


def test_new_lot_and_new_round_do_not_reuse_work_units(env):
    with connection() as c:
        first = new_project(c, env, {"bidder_id": env["company"]["id"], "lot": "一标段"})
        original = identities.snapshot(c, first)
        common = {
            "bidder_id": env["company"]["id"],
            "engineering_project_id": original["engineering_project_id"],
        }
        second = new_project(c, env, common | {"lot": "二标段"})
        rebid = new_project(c, env, common | {"lot": "一标段"})
        same_round = new_project(
            c, env, {"bidder_id": env["other"]["id"], "round_id": original["round_id"]}
        )
        assert identities.snapshot(c, second)["round_no"] == 1
        assert identities.snapshot(c, rebid)["round_no"] == 2
        assert identities.snapshot(c, same_round)["round_id"] == original["round_id"]
        assert len({identities.snapshot(c, b)["round_id"] for b in (first, second, rebid)}) == 3
    with connection() as c:
        with pytest.raises(DomainError) as error:
            identities.create_association(
                c,
                {
                    "title": "错误关联",
                    "bidder_id": env["company"]["id"],
                    "engineering_project_id": identities.snapshot(c, second)[
                        "engineering_project_id"
                    ],
                    "round_id": identities.snapshot(c, same_round)["round_id"],
                    "lot": "错误标段",
                },
            )
        assert error.value.code == "ROUND_LOT_MISMATCH"


def test_correction_preserves_audit_identity_at_time_and_does_not_invent_history(env):
    with connection() as c:
        bid = new_project(c, env, {"bidder_id": env["company"]["id"]})
        before = identities.snapshot(c, bid)
    with connection() as c:
        at = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        result = identities.correct(
            c,
            bid,
            env["actor"],
            None,
            {"bidder_id": env["other"]["id"], "reason": "合成原登记主体有误，按明确依据纠正"},
        )
        assert result["identity"]["bidder_id"] == env["other"]["id"]
        historical = identities.at_time(c, bid, at)
        assert historical["bidder_id"] == before["bidder_id"]
        assert historical["authority_id"] == before["authority_id"]
        assert identities.at_time(c, bid, "2000-01-01T00:00:00Z")["binding_status"] == "unavailable"
        assert not result["historical_records_rewritten"]
        assert result["qualification_recheck_required"]


def test_delayed_transaction_default_follows_action_order_not_transaction_start(env):
    ready, release = Event(), Event()

    def delayed():
        with connection() as c:
            c.execute("SELECT now()")
            ready.set()
            assert release.wait(10)
            return identities.set_default(c, env["actor"], env["other"]["id"], None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delayed)
        assert ready.wait(10)
        with connection() as c:
            identities.set_default(c, env["actor"], env["company"]["id"], None)
        release.set()
        assert future.result(timeout=10)["bidder"]["id"] == env["other"]["id"]
    with connection() as c:
        assert identities.default_bidder(c, env["actor"])["bidder"]["id"] == env["other"]["id"]


def test_delayed_correction_is_not_backdated_to_transaction_start(env):
    with connection() as c:
        bid = new_project(c, env, {"bidder_id": env["company"]["id"]})
    ready, release = Event(), Event()

    def delayed():
        with connection() as c:
            c.execute("SELECT now()")
            ready.set()
            assert release.wait(10)
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
            identities.correct(
                c,
                bid,
                env["actor"],
                None,
                {"bidder_id": env["other"]["id"], "reason": "在观察时点后明确纠正"},
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delayed)
        assert ready.wait(10)
        with connection() as c:
            cutoff = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        release.set()
        future.result(timeout=10)
    with connection() as c:
        observed = identities.at_time(c, bid, cutoff)
        assert observed["bidder_id"] == env["company"]["id"]
        assert observed["qualification_evidence"] is False


def test_login_http_registration_default_cas_and_bid_correction(env):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.identity_routes import router
    from apps.api.main import app
    from packages.domain.runtime import required

    login, password = "identity-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
        bid = new_project(c, env)
    if not any(getattr(route, "path", None) == "/api/v1/identity/bidders" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/v1/identity/bidders").status_code == 401
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": required("APP_ORIGIN")},
        )
        assert signed.status_code == 200
        base = {"Origin": required("APP_ORIGIN"), "X-CSRF-Token": signed.json()["csrf_token"]}

        def headers(tag=None, key=None):
            return (
                base | {"Idempotency-Key": key or uuid4().hex} | ({"If-Match": tag} if tag else {})
            )

        assert (
            client.post(
                "/api/v1/identity/bidders",
                json={"name": "测试", "reason": "注册"},
                headers={"Origin": required("APP_ORIGIN")},
            ).status_code
            == 403
        )
        got = client.get("/api/v1/identity/default-bidder")
        assert got.json()["bidder"] is None
        validate("DefaultBiddingCompanyView", got.json())
        selected = client.put(
            "/api/v1/identity/default-bidder",
            json={"bidder_id": env["company"]["id"]},
            headers=headers(got.headers["etag"]),
        )
        assert selected.status_code == 200
        validate("DefaultBiddingCompanyView", selected.json())
        assert (
            client.put(
                "/api/v1/identity/default-bidder",
                json={"bidder_id": env["other"]["id"]},
                headers=headers(got.headers["etag"]),
            ).status_code
            == 412
        )
        assert (
            client.put(
                "/api/v1/identity/default-bidder",
                json={"bidder_id": env["company"]["id"]},
                headers=headers(),
            ).status_code
            == 428
        )
        key = uuid4().hex
        body = {"name": "合成新登记公司", "reason": "明确真实主体测试", "set_as_default": True}
        registered = client.post("/api/v1/identity/bidders", json=body, headers=headers(key=key))
        assert registered.status_code == 201
        validate("RegisteredBiddingCompanyView", registered.json())
        replay = client.post("/api/v1/identity/bidders", json=body, headers=headers(key=key))
        assert (
            replay.json() == registered.json() and replay.headers["idempotent-replayed"] == "true"
        )
        assert not registered.json()["qualification_verified"]
        found = client.get("/api/v1/identity/bidders", params={"query": "合成新登记公司"})
        validate("BiddingCompanyPage", found.json())
        assert registered.json()["bidder"]["id"] in {i["id"] for i in found.json()["items"]}
        endpoint = f"/api/v1/bids/{bid}/identity"
        current = client.get(endpoint)
        validate("BidIdentityView", current.json())
        assert current.json()["identity"]["binding_status"] == "pending"
        before_tag = current.headers["etag"]
        body = {"bidder_id": env["company"]["id"], "reason": "核对本次实际投标主体"}
        key = uuid4().hex
        fixed = client.put(endpoint, json=body, headers=headers(before_tag, key))
        assert fixed.status_code == 200, fixed.json()
        validate("BidIdentityCorrection", fixed.json())
        assert fixed.json()["identity"]["binding_status"] == "confirmed"
        replay = client.put(endpoint, json=body, headers=headers(before_tag, key))
        assert replay.status_code == 200 and replay.json() == fixed.json()
        assert (
            client.put(
                endpoint, json=body | {"bidder_id": env["other"]["id"]}, headers=headers(before_tag)
            ).status_code
            == 412
        )
        fresh = client.get(endpoint).headers["etag"]

        def competing(company):
            return client.put(
                endpoint,
                json={"bidder_id": company, "reason": "并发更正验证"},
                headers=headers(fresh),
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(competing, [env["other"]["id"], registered.json()["bidder"]["id"]])
            )
        assert sorted(codes) == [200, 412]
