"""报价真实非owner PG工程测试；明确合成条款，内存对象存储，无收费模型。"""

import os
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.auth import csrf
from apps.api.pricing_routes import router
from packages.domain import storage
from packages.domain.runtime import DomainError, connection, required, secret_hash, sha, uid
from packages.pricing.models import SUPPORTED_FIELDS
from packages.rag.indexing import ensure_parse_sources, original_source_id, parse_source_id

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PRICING_INTEGRATION") != "1",
    reason="需要独立真实PG报价机制库；不使用owner跑业务",
)

TEXT = "合成报价条款：金额单位元，币种CNY；价格满分30分。有效报价须大于0，无最低限价、无最高限价；包含本方至少1个有效报价。以本方及全部数值有效的对手假设中的最低有效报价B为基准，价格分=30×B/本方报价。上下方向偏离系数为0。不排除同价对手。最终价格分按half_up_2dp四舍五入保留2位小数，计算过程不取整，价格分限制0至30分。"


@pytest.fixture
def client(monkeypatch):
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, raw, media: objects.__setitem__(key, raw))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    actor, token = uid(), uuid4().hex
    with connection() as c:
        who = c.execute(
            "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not who["rolsuper"]
        assert not c.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='design'::regnamespace AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owns"
        ).fetchone()["owns"]
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,'显式合成报价机制成员')", (actor,)
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (uid(), actor, secret_hash(token), sha(csrf(token).encode())),
        )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(
            {"code": error.code, "message": error.message, "details": error.details},
            status_code=error.status,
        )

    with TestClient(app) as web:
        web.cookies.set("session", token)
        yield (
            web,
            actor,
            lambda tag=None: {
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": csrf(token),
                "Idempotency-Key": uuid4().hex,
                **({"If-Match": tag} if tag else {}),
            },
            objects,
        )


def seed(actor, text=TEXT):
    bid, project, round_id, bidder, doc, material, blob, parse, evidence = [uid() for _ in range(9)]
    with connection() as c:
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'显式合成报价项目','{\"pricing\":true}',%s)",
            (bid, actor),
        )
        c.execute(
            "INSERT INTO engineering_projects(id,title) VALUES(%s,'合成报价工程')", (project,)
        )
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,lot,round_no) VALUES(%s,%s,'合成标段',1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成报价公司')", (bidder,))
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (blob, sha(str(blob)), "synthetic-pricing/" + str(blob)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成报价条款.pdf',now()-interval '1 day','source_original',%s)",
            (doc, bid, blob, actor),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
            (material, bid, doc),
        )
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase,current_material_id) VALUES(%s,%s,%s,'working',%s)",
            (bid, round_id, bidder, material),
        )
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
            (parse, bid, doc, sha("synthetic-pricing-parser"), sha("synthetic")),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
            (bid, parse),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
            (evidence, bid, parse, text, sha(text.encode())),
        )
        ensure_parse_sources(c, bid, parse, publish_readable=True)
    return {
        "bid": str(bid),
        "doc": str(doc),
        "parse": str(parse),
        "evidence": str(evidence),
        "material": str(material),
    }


def formula_request(item, text=TEXT):
    return {
        "title": "合成最低有效报价比例法",
        "formula": {
            "method": "lowest_valid_ratio",
            "maximum": "30",
            "minimum_price": None,
            "maximum_price": None,
            "minimum_valid_bids": 1,
            "above_coefficient": "0",
            "below_coefficient": "0",
        },
        "amount_unit": "元",
        "currency": "CNY",
        "rounding_policy": "half_up_2dp",
        "interpretation": {
            "baseline_set": "own_and_all_numerically_eligible_assumptions",
            "exclusions": "none",
            "random_parameters": "none",
            "intermediate_rounding": "none",
            "clamping": "zero_to_maximum",
        },
        "terms_complete_confirmed": True,
        "matches_source_confirmed": True,
        "evidence_quotes": [
            {
                "source_id": str(parse_source_id(UUID(item["parse"]))),
                "source_revision": 2,
                "document_version_id": item["doc"],
                "parse_id": item["parse"],
                "physical_page": 1,
                "evidence_id": item["evidence"],
                "quote": text,
                "supported_fields": sorted(SUPPORTED_FIELDS),
            }
        ],
        "reason": "显式合成条款人工对照全部受限语义；不是真实招标业务验收",
    }


def register(client, item, value=None):
    web, _, headers, _ = client
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    assert context.status_code == 200, context.text
    value = value or formula_request(item)
    value["evidence_quotes"][0]["source_id"] = str(parse_source_id(UUID(item["parse"])))
    result = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert result.status_code == 201, result.text
    return result, value


def scenario(formula_id):
    return {
        "formula_version_id": formula_id,
        "candidate_price": "120",
        "competitor_price_assumptions": ["100", "100", "0"],
        "rounding_policy": "half_up_2dp",
        "candidate_prices": ["120.0", "100"],
        "curve_range": {"lower": "80", "upper": "120", "points": 3},
        "amount_unit": "元",
        "currency": "CNY",
    }


def test_registered_blob_feature_exact_sources_and_independent_simulation_saved_history(client):
    web, actor, headers, _ = client
    item = seed(actor)
    formula, _ = register(client, item)
    f = formula.json()
    from test_pricing_contracts import validate

    validate("PricingFormulaView", f)
    validate("PricingContext", web.get(f"/api/v1/bids/{item['bid']}/pricing-context").json())
    validate("PricingFormulaPage", web.get(f"/api/v1/bids/{item['bid']}/pricing-formulas").json())
    validate(
        "PricingFormulaSourcePage",
        web.get(f"/api/v1/bids/{item['bid']}/pricing-formula-sources").json(),
    )
    with connection() as c:
        assert (
            c.execute(
                "SELECT kind FROM retrieval.source_nodes WHERE id=%s", (f["formula_version_id"],)
            ).fetchone()["kind"]
            == "feature"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE target_object_id=%s",
                (f["formula_version_id"],),
            ).fetchone()["n"]
            == 1
        )
    result = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-simulations",
        json=scenario(f["formula_version_id"]),
        headers=headers(formula.headers["etag"]),
    )
    assert result.status_code == 201, result.text
    sim = result.json()
    validate("PricingSimulationView", sim)
    assert sim["price_score"] == "25.00" and sim["eligible"] is True
    assert len(sim["candidates"]) == 2 and [p["score"] for p in sim["curve"]] == [
        "30.00",
        "30.00",
        "25.00",
    ]
    assert (
        sim["valid_price_count"] == 3 and sim["excluded_competitor_assumptions"][0]["price"] == "0"
    )
    plans = web.get(f"/api/v1/bids/{item['bid']}/pricing-plans").json()
    assert plans["items"] == []
    saved = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans",
        json={"simulation_id": sim["id"], "reason": "保留合成情景"},
        headers=headers(result.headers["etag"]),
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["result"]["price_score"] == "25.00"
    assert saved.json()["formula_snapshot"] == f["formula"]
    validate("PricingPlanView", saved.json())
    validate("PricingPlanPage", web.get(f"/api/v1/bids/{item['bid']}/pricing-plans").json())


def post_scenario(client, item, formula, value=None, request_headers=None):
    web, _, headers, _ = client
    return web.post(
        f"/api/v1/bids/{item['bid']}/pricing-simulations",
        json=value or scenario(formula.json()["formula_version_id"]),
        headers=request_headers or headers(formula.headers["etag"]),
    )


def withdraw(item, source=None):
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (source or original_source_id(UUID(item["doc"])),),
        )


def test_unknown_ineligible_and_real_zero_are_distinct_and_null_can_be_saved(client):
    web, actor, headers, _ = client
    text = TEXT.replace("至少1个", "至少4个")
    item = seed(actor, text)
    value = formula_request(item, text)
    value["formula"]["minimum_valid_bids"] = 4
    formula, _ = register(client, item, value)
    unknown = post_scenario(client, item, formula)
    assert unknown.status_code == 201, unknown.text
    assert (
        unknown.json()["eligible"] is True
        and unknown.json()["price_score"] is None
        and unknown.json()["notes"]
    )
    saved = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans",
        json={"simulation_id": unknown.json()["id"], "reason": "保留信息不足情景，不能填0"},
        headers=headers(unknown.headers["etag"]),
    )
    assert saved.status_code == 201 and saved.json()["result"]["price_score"] is None
    request = scenario(formula.json()["formula_version_id"])
    request["candidate_price"] = "0"
    invalid = post_scenario(client, item, formula, request)
    assert (
        invalid.status_code == 201
        and invalid.json()["eligible"] is False
        and invalid.json()["price_score"] is None
    )
    mean_text = (
        TEXT.replace("最低有效报价B", "算术平均报价B")
        .replace("30×B/本方报价", "30×(1-偏离比例×5)")
        .replace("系数为0", "系数为5")
    )
    mean_item = seed(actor, mean_text)
    value = formula_request(mean_item, mean_text)
    value["formula"].update(
        method="mean_valid_deviation", above_coefficient="5", below_coefficient="5"
    )
    mean, _ = register(client, mean_item, value)
    request = scenario(mean.json()["formula_version_id"])
    request.update(candidate_price="1000", competitor_price_assumptions=["100"])
    zero = post_scenario(client, mean_item, mean, request)
    assert (
        zero.status_code == 201
        and zero.json()["eligible"] is True
        and zero.json()["price_score"] == "0.00"
    )


def test_replays_recheck_source_and_history_preserves_original_without_recalculation(client):
    web, actor, headers, _ = client
    item = seed(actor)
    formula, _ = register(client, item)
    request_headers = headers(formula.headers["etag"])
    result = post_scenario(client, item, formula, request_headers=request_headers)
    repeated = post_scenario(client, item, formula, request_headers=request_headers)
    assert repeated.status_code == 201 and repeated.json()["id"] == result.json()["id"]
    save_headers = headers(result.headers["etag"])
    save_body = {"simulation_id": result.json()["id"], "reason": "不可变合成情景"}
    saved = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans", json=save_body, headers=save_headers
    )
    assert saved.status_code == 201
    withdraw(item)
    replay = post_scenario(client, item, formula, request_headers=request_headers)
    assert replay.status_code == 409 and replay.json()["code"] == "PRICING_SOURCE_CHANGED"
    replay_save = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans", json=save_body, headers=save_headers
    )
    assert replay_save.status_code == 409
    historical = web.get(f"/api/v1/bids/{item['bid']}/pricing-plans/" + saved.json()["id"])
    assert historical.status_code == 200 and historical.json()["result"] == saved.json()["result"]
    assert historical.json()["current_applicability"]["applicable"] is False
    assert (
        web.get(f"/api/v1/bids/{item['bid']}/pricing-formulas").json()["items"][0][
            "current_applicability"
        ]["applicable"]
        is False
    )


def test_formula_put_has_no_transaction_and_source_cas_refuses_withdrawal(client, monkeypatch):
    web, actor, headers, objects = client
    item = seed(actor)
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    original = storage.put
    observations = []

    def put(key, raw, media):
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                ).fetchone()["n"]
                == 0
            )
            c.execute("SET LOCAL lock_timeout='2s'")
        original(key, raw, media)
        withdraw(item)
        observations.append("withdrawn_during_put")

    monkeypatch.setattr(storage, "put", put)
    result = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=formula_request(item),
        headers=headers(context.headers["etag"]),
    )
    assert result.status_code == 409, result.text
    assert observations == ["withdrawn_during_put"] and objects
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",
                (item["bid"],),
            ).fetchone()["n"]
            == 0
        )


def test_switch_blocks_all_pricing_business_reads_writes_and_replay_but_preserves_history(client):
    web, actor, headers, _ = client
    item = seed(actor)
    formula, value = register(client, item)
    simulation = post_scenario(client, item, formula)
    save_body = {"simulation_id": simulation.json()["id"], "reason": "开关不删除历史"}
    saved = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans",
        json=save_body,
        headers=headers(simulation.headers["etag"]),
    )
    with connection() as c:
        c.execute(
            "UPDATE bids SET settings=jsonb_set(settings,'{pricing}','false') WHERE id=%s",
            (item["bid"],),
        )
    base = f"/api/v1/bids/{item['bid']}"
    for suffix in [
        "/pricing-context",
        "/pricing-formula-sources",
        "/pricing-formulas",
        "/pricing-formulas/" + formula.json()["formula_version_id"],
        "/pricing-simulations/" + simulation.json()["id"],
        "/pricing-plans",
        "/pricing-plans/" + saved.json()["id"],
    ]:
        response = web.get(base + suffix)
        assert response.status_code == 403 and response.json()["code"] == "PRICING_DISABLED", (
            suffix,
            response.text,
        )
    for suffix, body in [
        ("/pricing-formulas", value),
        ("/pricing-simulations", scenario(formula.json()["formula_version_id"])),
        ("/pricing-plans", save_body),
    ]:
        response = web.post(base + suffix, json=body, headers=headers(formula.headers["etag"]))
        assert response.status_code == 403 and response.json()["code"] == "PRICING_DISABLED"
    with connection() as c:
        c.execute(
            "UPDATE bids SET settings=jsonb_set(settings,'{pricing}','true') WHERE id=%s",
            (item["bid"],),
        )
    assert (
        web.get(base + "/pricing-plans/" + saved.json()["id"]).json()["result"]
        == saved.json()["result"]
    )


def test_cross_project_exact_parse_quote_and_unsupported_terms_rejected(client):
    web, actor, headers, _ = client
    item = seed(actor)
    other = seed(actor)
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    value = formula_request(other)
    cross = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert cross.status_code == 409
    value = formula_request(item)
    value["evidence_quotes"][0]["source_id"] = str(original_source_id(UUID(item["doc"])))
    badparse = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert badparse.status_code == 409
    value = formula_request(item)
    value["evidence_quotes"][0]["quote"] = TEXT[:-5]
    truncated = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert truncated.status_code == 422 and truncated.json()["code"] == "PRICING_QUOTE_INVALID"
    random_item = seed(actor, TEXT + "另抽签确定随机系数。")
    context2 = web.get(f"/api/v1/bids/{random_item['bid']}/pricing-context")
    bad = web.post(
        f"/api/v1/bids/{random_item['bid']}/pricing-formulas",
        json=formula_request(random_item, TEXT + "另抽签确定随机系数。"),
        headers=headers(context2.headers["etag"]),
    )
    assert bad.status_code == 422 and bad.json()["code"] == "PRICING_FORMULA_UNSUPPORTED"
    formula, _ = register(client, item)
    assert (
        web.get(
            f"/api/v1/bids/{other['bid']}/pricing-formulas/" + formula.json()["formula_version_id"]
        ).status_code
        == 404
    )


def test_measure_input_if_match_csrf_and_unknown_fields_do_not_create_simulation(client):
    web, actor, headers, _ = client
    item = seed(actor)
    formula, _ = register(client, item)
    value = scenario(formula.json()["formula_version_id"])
    value["amount_unit"] = "万元"
    response = post_scenario(client, item, formula, value)
    assert response.status_code == 422 and response.json()["code"] == "PRICING_MEASURE_MISMATCH"
    assert (
        post_scenario(client, item, formula, request_headers=headers('"stale"')).status_code == 412
    )
    badheaders = headers(formula.headers["etag"])
    badheaders.pop("X-CSRF-Token")
    assert post_scenario(client, item, formula, request_headers=badheaders).status_code == 403
    value = scenario(formula.json()["formula_version_id"])
    value["price_score"] = "30.00"
    assert post_scenario(client, item, formula, value).status_code == 422
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM pricing_simulations WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 0
        )


def test_product_hand_calculations_work_through_both_sourced_formula_apis(client):
    web, actor, _, _ = client
    text = TEXT.replace("无最低限价、无最高限价", "最低价格80元、最高价格120元").replace(
        "至少1个", "至少2个"
    )
    item = seed(actor, text)
    value = formula_request(item, text)
    value["formula"].update(minimum_price="80", maximum_price="120", minimum_valid_bids=2)
    formula, _ = register(client, item, value)
    request = scenario(formula.json()["formula_version_id"])
    request.update(
        candidate_price="100",
        competitor_price_assumptions=["90", "110", "70"],
        candidate_prices=["80", "100", "120"],
    )
    simulated = post_scenario(client, item, formula, request)
    assert simulated.status_code == 201, simulated.text
    assert simulated.json()["price_score"] == "27.00"
    assert [point["score"] for point in simulated.json()["curve"]] == ["30.00", "27.00", "22.50"]
    assert simulated.json()["excluded_competitor_assumptions"] == [
        {"index": 2, "price": "70", "reason": "低于公式最低价格"}
    ]
    text = (
        text.replace("最低有效报价B", "算术平均报价B")
        .replace("30×B/本方报价", "30×(1-偏离比例×对应系数)")
        .replace("上下方向偏离系数为0", "上方向偏离系数为2，下方向为1")
    )
    item2 = seed(actor, text)
    value = formula_request(item2, text)
    value["formula"].update(
        method="mean_valid_deviation",
        minimum_price="80",
        maximum_price="120",
        minimum_valid_bids=2,
        above_coefficient="2",
        below_coefficient="1",
    )
    formula2, _ = register(client, item2, value)
    request = scenario(formula2.json()["formula_version_id"])
    request.update(candidate_price="110", competitor_price_assumptions=["90"])
    mean = post_scenario(client, item2, formula2, request)
    assert mean.status_code == 201 and mean.json()["price_score"] == "24.00"
    assert [point["baseline_price"] for point in mean.json()["curve"]] == ["85", "95", "105"]
    assert web.get(f"/api/v1/bids/{item2['bid']}/pricing-plans").json()["items"] == []


def test_feature_extra_lineage_and_formula_blob_tampering_cannot_authorize_simulation(client):
    web, actor, headers, objects = client
    item = seed(actor)
    formula, _ = register(client, item)
    other = seed(actor)
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
            (formula.json()["formula_version_id"], original_source_id(UUID(other["doc"]))),
        )
    invalid = post_scenario(client, item, formula)
    assert invalid.status_code == 409 and invalid.json()["code"] == "PRICING_SOURCE_CHANGED"
    assert (
        web.get(
            f"/api/v1/bids/{item['bid']}/pricing-formulas/" + formula.json()["formula_version_id"]
        ).json()["current_applicability"]["applicable"]
        is False
    )
    second = seed(actor)
    registered, _ = register(client, second)
    with connection() as c:
        key = c.execute(
            "SELECT object_key FROM blobs WHERE id=%s", (registered.json()["formula_version_id"],)
        ).fetchone()["object_key"]
    objects[key] = b'{"formula":{"maximum":"100"}}'
    corrupt = post_scenario(client, second, registered)
    assert corrupt.status_code == 409 and corrupt.json()["code"] == "PRICING_FORMULA_CORRUPTED"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM pricing_simulations WHERE bid_id=ANY(%s::uuid[])",
                ([item["bid"], second["bid"]],),
            ).fetchone()["n"]
            == 0
        )


def test_pricing_rr_snapshot_rejected_and_unrelated_body_material_change_keeps_formula_current(
    client,
):
    from packages.pricing import sources

    web, actor, _, _ = client
    item = seed(actor)
    formula, _ = register(client, item)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(DomainError) as denied:
            sources.exact_sources(
                c, bid=item["bid"], quotes=formula.json()["formula"]["evidence_quotes"]
            )
        assert denied.value.code == "PRICING_FRESH_TRANSACTION_REQUIRED"
    # A new material snapshot with identical effective tender/supplement scope must not force reconfirmation.
    with connection() as c:
        material = uid()
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,2,'{}',%s)",
            (material, item["bid"], sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
            (material, item["bid"], item["doc"]),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s",
            (material, item["bid"]),
        )
        c.execute("UPDATE bids SET material_revision=2 WHERE id=%s", (item["bid"],))
    current = web.get(
        f"/api/v1/bids/{item['bid']}/pricing-formulas/" + formula.json()["formula_version_id"]
    )
    assert current.json()["current_applicability"]["applicable"] is True
    assert post_scenario(client, item, current).status_code == 201


def test_formula_single_confirmation_replay_reuses_same_object_and_rechecks_source(client):
    web, actor, headers, objects = client
    item = seed(actor)
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    command_headers = headers(context.headers["etag"])
    body = formula_request(item)
    path = f"/api/v1/bids/{item['bid']}/pricing-formulas"
    first = web.post(path, json=body, headers=command_headers)
    assert first.status_code == 201, first.text
    count = len(objects)
    replay = web.post(path, json=body, headers=command_headers)
    assert replay.status_code == 201 and replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == first.json() and len(objects) == count == 1
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",
                (item["bid"],),
            ).fetchone()["n"]
            == 1
        )
    withdraw(item)
    stale = web.post(path, json=body, headers=command_headers)
    assert stale.status_code == 409 and stale.json()["code"] == "PRICING_SOURCE_CHANGED"
    assert len(objects) == 1


def test_new_unread_supplement_invalidates_old_formula_and_preserves_old_simulation(client):
    web, actor, _, _ = client
    item = seed(actor)
    formula, _ = register(client, item)
    simulated = post_scenario(client, item, formula)
    assert simulated.status_code == 201
    with connection() as c:
        material, blob, doc = uid(), uid(), uid()
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (item["bid"],))
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (blob, sha(str(blob)), "synthetic-pricing/" + str(blob)),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成未读补遗.pdf',now(),'source_original',%s)",
            (doc, item["bid"], blob, actor),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,2,'{}',%s)",
            (material, item["bid"], sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0),(%s,%s,%s,'supplement',0)",
            (material, item["bid"], item["doc"], material, item["bid"], doc),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s",
            (material, item["bid"]),
        )
        c.execute("UPDATE bids SET material_revision=2 WHERE id=%s", (item["bid"],))
    denied = post_scenario(client, item, formula)
    assert denied.status_code == 409 and denied.json()["code"] == "PRICING_SOURCE_CHANGED"
    history = web.get(f"/api/v1/bids/{item['bid']}/pricing-simulations/" + simulated.json()["id"])
    assert (
        history.status_code == 200
        and history.json()["price_score"] == simulated.json()["price_score"]
    )
    assert history.json()["current_applicability"]["applicable"] is False


@pytest.mark.parametrize(
    "source_unit,requested_unit",
    [("万元", "元"), ("元", "万元"), ("千元", "元"), ("百万元", "万元"), ("美元", "元")],
)
def test_whole_source_unit_mismatch_is_rejected_before_any_object_put(
    client, monkeypatch, source_unit, requested_unit
):
    web, actor, headers, objects = client
    text = TEXT.replace("元", source_unit)
    item = seed(actor, text)
    value = formula_request(item, text)
    value["amount_unit"] = requested_unit
    attempted = []

    def forbidden_put(*arguments):
        attempted.append(arguments)
        raise AssertionError("非法单位应在对象PUT之前拒绝")

    monkeypatch.setattr(storage, "put", forbidden_put)
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    rejected = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert (
        rejected.status_code == 422 and rejected.json()["code"] == "PRICING_UNIT_WITHOUT_EVIDENCE"
    )
    assert rejected.json()["details"]["field"] == "amount_unit"
    assert attempted == [] and objects == {}
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",
                (item["bid"],),
            ).fetchone()["n"]
            == 0
        )


def test_consistent_wanyuan_is_supported_and_mixed_unit_formula_is_refused_before_put(client):
    web, actor, headers, objects = client
    text = TEXT.replace("元", "万元")
    item = seed(actor, text)
    value = formula_request(item, text)
    value["amount_unit"] = "万元"
    formula, _ = register(client, item, value)
    request = scenario(formula.json()["formula_version_id"])
    request["amount_unit"] = "万元"
    simulated = post_scenario(client, item, formula, request)
    assert simulated.status_code == 201 and simulated.json()["price_score"] == "25.00"
    mixed_text = text + "最低价格80元。"
    mixed = seed(actor, mixed_text)
    value = formula_request(mixed, mixed_text)
    value["amount_unit"] = "万元"
    count = len(objects)
    context = web.get(f"/api/v1/bids/{mixed['bid']}/pricing-context")
    rejected = web.post(
        f"/api/v1/bids/{mixed['bid']}/pricing-formulas",
        json=value,
        headers=headers(context.headers["etag"]),
    )
    assert (
        rejected.status_code == 422 and rejected.json()["code"] == "PRICING_UNIT_WITHOUT_EVIDENCE"
    )
    assert len(objects) == count


def test_previously_registered_wrong_unit_cannot_be_reused_or_replayed(client, monkeypatch):
    from packages.pricing import repository as repo

    web, actor, headers, _ = client
    text = TEXT.replace("元", "万元")
    item = seed(actor, text)
    value = formula_request(item, text)
    context = web.get(f"/api/v1/bids/{item['bid']}/pricing-context")
    command_headers = headers(context.headers["etag"])
    original = repo.validate_terms
    # Explicit synthetic earlier-accepted state representing F01's confirmed substring defect.
    monkeypatch.setattr(repo, "validate_terms", lambda value: None)
    formula = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas", json=value, headers=command_headers
    )
    assert formula.status_code == 201, formula.text
    prior_simulation = post_scenario(client, item, formula)
    assert prior_simulation.status_code == 201
    monkeypatch.setattr(repo, "validate_terms", original)
    current = web.get(
        f"/api/v1/bids/{item['bid']}/pricing-formulas/" + formula.json()["formula_version_id"]
    )
    assert (
        current.status_code == 200
        and current.json()["current_applicability"]["applicable"] is False
    )
    denied = post_scenario(client, item, formula)
    assert denied.status_code == 409 and denied.json()["code"] == "PRICING_SOURCE_CHANGED"
    replay = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-formulas", json=value, headers=command_headers
    )
    assert replay.status_code == 409 and replay.json()["code"] == "PRICING_SOURCE_CHANGED"
    historical = web.get(
        f"/api/v1/bids/{item['bid']}/pricing-simulations/" + prior_simulation.json()["id"]
    )
    assert (
        historical.status_code == 200
        and historical.json()["price_score"] == prior_simulation.json()["price_score"]
    )
    assert historical.json()["current_applicability"]["applicable"] is False
    saved = web.post(
        f"/api/v1/bids/{item['bid']}/pricing-plans",
        json={
            "simulation_id": prior_simulation.json()["id"],
            "reason": "错误单位不能继续保存为当前方案",
        },
        headers=headers(prior_simulation.headers["etag"]),
    )
    assert saved.status_code == 409 and saved.json()["code"] == "PRICING_SOURCE_CHANGED"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM pricing_simulations WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 1
        )
