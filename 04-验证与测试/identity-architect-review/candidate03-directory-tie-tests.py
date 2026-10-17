"""Real nonowner identity reuse, isolation, correction and HTTP conflict recovery."""

import hashlib
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


def test_directory_pagination_discovers_more_than_100_and_binds_filters(env):
    from fastapi.testclient import TestClient

    from apps.api.identity_routes import router
    from apps.api.main import app
    from packages.domain.runtime import secret_hash

    prefix = "分页合成-" + uuid4().hex
    token = str(uuid4())
    companies, projects, rounds = set(), set(), set()
    with connection() as c:
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (uid(), env["actor"], secret_hash(token), hashlib.sha256(b"synthetic").hexdigest()),
        )
        for i in range(107):
            companies.add(
                identities.register_bidder(
                    c, env["actor"], prefix + f"-{i:03}", "明确的分页合成公司夹具", None
                )["id"]
            )
            project = uid()
            projects.add(str(project))
            c.execute(
                "INSERT INTO engineering_projects(id,title) VALUES(%s,%s)",
                (project, prefix + f"-{i:03}"),
            )
        selected_project = project
        for i in range(107):
            round_id = uid()
            rounds.add(str(round_id))
            c.execute(
                "INSERT INTO bid_rounds(id,engineering_project_id,lot,round_no) VALUES(%s,%s,%s,%s)",
                (round_id, selected_project, [None, "", "一标段", "二标段"][i % 4], i + 1),
            )
    if not any(getattr(route, "path", None) == "/api/v1/identity/bidders" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        client.cookies.set("session", token)
        first_cursors = {}
        for path, params, schema, expected in [
            ("bidders", {"query": prefix}, "BiddingCompanyPage", companies),
            ("engineering-projects", {"query": prefix}, "EngineeringIdentityPage", projects),
            (
                "rounds",
                {"engineering_project_id": str(selected_project)},
                "EngineeringRoundPage",
                rounds,
            ),
        ]:
            observed = []
            cursor = None
            for _ in range(10):
                query = params | {"limit": 25} | ({"cursor": cursor} if cursor else {})
                response = client.get("/api/v1/identity/" + path, params=query)
                assert response.status_code == 200, response.json()
                page = response.json()
                validate(schema, page)
                observed.extend(item["id"] for item in page["items"])
                assert bool(page["next_cursor"]) == page["has_more"]
                cursor = page["next_cursor"]
                if path not in first_cursors:
                    first_cursors[path] = cursor
                if not cursor:
                    break
            else:
                pytest.fail("Pagination did not terminate")
            assert len(observed) == 107 and len(set(observed)) == 107 and set(observed) == expected
        for path, params in [
            ("bidders", {"query": prefix + "changed", "cursor": first_cursors["bidders"]}),
            ("engineering-projects", {"query": prefix, "cursor": first_cursors["bidders"]}),
            (
                "rounds",
                {
                    "engineering_project_id": next(
                        p for p in projects if p != str(selected_project)
                    ),
                    "cursor": first_cursors["rounds"],
                },
            ),
            ("bidders", {"query": prefix, "cursor": first_cursors["bidders"] + "x"}),
        ]:
            denied = client.get("/api/v1/identity/" + path, params=params)
            assert denied.status_code == 422 and denied.json()["code"] == "INVALID_CURSOR"


def test_architect_delayed_transaction_default_is_not_latest(env):
    from pathlib import Path
    import json
    with connection() as delayed:
        delayed.execute('SELECT transaction_timestamp()')
        with connection() as other:
            other.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('identity-default:'+str(env['actor']),))
            identities.set_default(other,env['actor'],env['company']['id'],None)
        delayed.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('identity-default:'+str(env['actor']),))
        response=identities.set_default(delayed,env['actor'],env['other']['id'],None)
    with connection() as c:actual=identities.default_bidder(c,env['actor'])
    assert response['bidder']['id']==env['other']['id'] and actual['bidder']['id']==env['other']['id']
    Path(os.environ['ARCHITECT_IDENTITY_OUT']).joinpath('candidate03-default-regression.json').write_text(json.dumps({'original_counterexample_closed':True,'last_explicit_choice_returned':True,'scope':'API-role delayed transaction; identical per-member advisory-lock ordering; no concurrent conflicting writes'},indent=2)+'\n')


def test_architect_later_correction_appears_in_earlier_as_of(env):
    from pathlib import Path
    import json
    with connection() as c:bid=new_project(c,env,{'bidder_id':env['company']['id']})
    with connection() as delayed:
        started=delayed.execute('SELECT transaction_timestamp() AS at').fetchone()['at']
        with connection() as witness:cutoff=witness.execute('SELECT clock_timestamp() AS at').fetchone()['at']
        delayed.execute('SELECT id FROM bids WHERE id=%s FOR UPDATE',(bid,))
        fixed=identities.correct(delayed,bid,env['actor'],None,{'bidder_id':env['other']['id'],'reason':'合成延迟事务：在冻结时点之后才取锁更正'})
    with connection() as c:historical=identities.at_time(c,bid,cutoff)
    assert historical['bidder_id']==env['company']['id'] and cutoff>started and historical['qualification_evidence'] is False
    Path(os.environ['ARCHITECT_IDENTITY_OUT']).joinpath('candidate03-history-regression.json').write_text(json.dumps({'original_counterexample_closed':True,'correction_lock_acquired_after_cutoff':True,'as_of_resolves_later_correction':False,'qualification_evidence':False,'scope':'API-role delayed transaction; no production data'},indent=2)+'\n')


def test_architect_directory_ties_do_not_skip_or_duplicate(env):
    from fastapi.testclient import TestClient

    from apps.api.identity_routes import router
    from apps.api.main import app
    from packages.domain.runtime import secret_hash

    prefix = "分页合成-" + uuid4().hex
    token = str(uuid4())
    companies, projects, rounds = set(), set(), set()
    with connection() as c:
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (uid(), env["actor"], secret_hash(token), hashlib.sha256(b"synthetic").hexdigest()),
        )
        for i in range(107):
            companies.add(
                identities.register_bidder(
                    c, env["actor"], prefix, "明确的分页合成公司夹具", None
                )["id"]
            )
            project = uid()
            projects.add(str(project))
            c.execute(
                "INSERT INTO engineering_projects(id,title) VALUES(%s,%s)",
                (project, prefix),
            )
        selected_project = project
        for i in range(107):
            round_id = uid()
            rounds.add(str(round_id))
            c.execute(
                "INSERT INTO bid_rounds(id,engineering_project_id,lot,round_no) VALUES(%s,%s,%s,%s)",
                (round_id, selected_project, [None, ""][i % 2], i // 2 + 1),
            )
    if not any(getattr(route, "path", None) == "/api/v1/identity/bidders" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        client.cookies.set("session", token)
        first_cursors = {}
        for path, params, schema, expected in [
            ("bidders", {"query": prefix}, "BiddingCompanyPage", companies),
            ("engineering-projects", {"query": prefix}, "EngineeringIdentityPage", projects),
            (
                "rounds",
                {"engineering_project_id": str(selected_project)},
                "EngineeringRoundPage",
                rounds,
            ),
        ]:
            observed = []
            cursor = None
            for _ in range(10):
                query = params | {"limit": 25} | ({"cursor": cursor} if cursor else {})
                response = client.get("/api/v1/identity/" + path, params=query)
                assert response.status_code == 200, response.json()
                page = response.json()
                validate(schema, page)
                observed.extend(item["id"] for item in page["items"])
                assert bool(page["next_cursor"]) == page["has_more"]
                cursor = page["next_cursor"]
                if path not in first_cursors:
                    first_cursors[path] = cursor
                if not cursor:
                    break
            else:
                pytest.fail("Pagination did not terminate")
            assert len(observed) == 107 and len(set(observed)) == 107 and set(observed) == expected
        for path, params in [
            ("bidders", {"query": prefix + "changed", "cursor": first_cursors["bidders"]}),
            ("engineering-projects", {"query": prefix, "cursor": first_cursors["bidders"]}),
            (
                "rounds",
                {
                    "engineering_project_id": next(
                        p for p in projects if p != str(selected_project)
                    ),
                    "cursor": first_cursors["rounds"],
                },
            ),
            ("bidders", {"query": prefix, "cursor": first_cursors["bidders"] + "x"}),
        ]:
            denied = client.get("/api/v1/identity/" + path, params=params)
            assert denied.status_code == 422 and denied.json()["code"] == "INVALID_CURSOR"
