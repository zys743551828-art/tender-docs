"""受限API角色真实PG补遗事务机制，合成原页，不访问远端模型。"""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain import storage
from packages.domain.fact_sets import evidence_basis
from packages.domain.human_decisions import append_decision
from packages.domain.review_data import read_bundle
from packages.domain.rule_sets import create_extracted_set, current_decision, rules
from packages.domain.runtime import Command, DomainError, connection, required, sha
from packages.domain.source_effectivity import decide, evidence_impacts, termination_basis

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SOURCE_EFFECTIVITY_INTEGRATION") != "1", reason="需独立非owner PG"
)


@pytest.fixture
def context(request):
    with connection() as c:
        actor, bid, project, round_id, bidder, material, rule_set = [uuid4() for _ in range(7)]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成效力核对')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成效力项目','{}',%s)",
            (bid, actor),
        )
        c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成工程')", (project,))
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成投标主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        versions, evidences = [], []
        texts = [
            "原招标文件：工期90天；质保24个月。",
            "本补遗替代原招标文件工期条款：工期120天；其他条款不变。",
        ]
        variant = getattr(request, "param", None)
        if variant in ("whole", "mixed"):
            texts[1] = "本文件全文替代原招标文件，工期120天；质保24个月。"
        for index, text in enumerate(texts):
            blob, version, parse, evidence = [uuid4() for _ in range(4)]
            versions.append(version)
            evidences.append(evidence)
            raw = json.dumps(str(blob)).encode()
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",
                (blob, sha(raw), f"workbench-checks/{blob}", len(raw)),
            )
            if os.environ.get("RUN_SOURCE_EFFECTIVITY_HTTP") == "1":
                storage.put(f"workbench-checks/{blob}", raw, "application/pdf")
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),'source_original',%s)",
                (version, bid, blob, f"合成{index}.pdf", actor),
            )
            c.execute(
                "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,0)",
                (material, bid, version, "tender" if index == 0 else "supplement"),
            )
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
                (parse, bid, version, sha("synthetic"), sha("synthetic-config")),
            )
            c.execute(
                "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
                (bid, parse),
            )
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
                (evidence, bid, parse, text, sha(text.encode())),
            )
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha("synthetic-rules")),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s,current_rule_set_id=%s WHERE bid_id=%s",
            (material, rule_set, bid),
        )
        items = []
        for statement in ("工期90天", "质保24个月"):
            identity = uuid4()
            items.append(identity)
            c.execute(
                "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands,applicability_scope) VALUES(%s,%s,%s,'mandatory',%s,'[]','[]',%s,'[]',%s)",
                (
                    identity,
                    bid,
                    rule_set,
                    statement,
                    evidences
                    if variant == "mixed" and statement == "质保24个月"
                    else [evidences[0]],
                    Jsonb({"bid_id": str(bid), "description": "本项目"}),
                ),
            )
            append_decision(
                c,
                bid,
                actor,
                "rule",
                identity,
                {
                    "decision": "confirmed",
                    "reason": "已核对原文件",
                    "evidence_ids": [str(evidences[0])],
                },
                basis=evidence_basis(c, bid, [evidences[0]]),
            )
    return {
        "actor": actor,
        "bid": bid,
        "material": material,
        "rule_set": rule_set,
        "versions": versions,
        "evidences": evidences,
        "rules": items,
        "texts": texts,
    }


def body(context, action="partial_replace"):
    return {
        "action": action,
        "base_rule_set_id": str(context["rule_set"]),
        "old_document_version_id": str(context["versions"][0]),
        "new_document_version_id": str(context["versions"][1]),
        "rule_ids": [str(context["rules"][0])] if action != "whole_replace" else [],
        "reason": "核对补遗原页及范围后明确本次影响",
        "scope_basis": []
        if action == "defer"
        else [{"evidence_id": str(context["evidences"][1]), "quote": context["texts"][1]}],
        "additions": []
        if action == "defer"
        else [
            {
                "kind": "mandatory",
                "statement": "工期120天",
                "conditions": [],
                "exceptions": [],
                "maximum": None,
                "bands": [],
                "scope": {"bid_id": str(context["bid"]), "description": "本项目"},
                "evidence_quotes": [
                    {"evidence_id": str(context["evidences"][1]), "quote": "工期120天"}
                ],
            }
        ],
    }


def apply(context, request):
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (context["bid"],))
        cmd = Command(
            c, context["actor"], "test-source-effectivity", context["bid"], uuid4().hex, request
        )
        result = decide(c, context["bid"], context["actor"], cmd.id, request)
        cmd.finish({"rule_set_id": str(result)})
    return result


def test_partial_replacement_preserves_unaffected_human_actor_and_core_obligations(context):
    with connection() as c:
        previous = current_decision(c, context["bid"], "rule", context["rules"][1])
    new_set = apply(context, body(context))
    with connection() as c:
        items = rules(c, new_set)
        assert len(items) == 3
        ended = next(item for item in items if item["statement"] == "工期90天")
        untouched = next(item for item in items if item["statement"] == "质保24个月")
        added = next(item for item in items if item["statement"] == "工期120天")
        assert ended["provenance_status"] == "superseded" and termination_basis(
            c, context["bid"], ended
        )
        assert untouched["provenance_status"] == "effective"
        assert current_decision(c, context["bid"], "rule", untouched["id"])["id"] == previous["id"]
        assert (
            current_decision(c, context["bid"], "rule", added["id"])["actor_id"] == context["actor"]
        )
        assert all(
            item["provenance_status"] == "effective" for item in rules(c, context["rule_set"])
        )
        # 再提取同一旧证据不能将已明确结束的工期义务复活。
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (context["bid"],))
        rebuilt = create_extracted_set(
            c,
            context["bid"],
            context["material"],
            [dict(ended), dict(untouched)],
            "complete",
            read_bundle(c, context["bid"], context["material"]),
        )
        again = next(item for item in rules(c, rebuilt) if item["statement"] == "工期90天")
        assert again["provenance_status"] == "superseded" and termination_basis(
            c, context["bid"], again
        )
        impacts = evidence_impacts(c, context["bid"], [str(context["evidences"][0])])
        assert len(impacts) == 1 and impacts[0]["affected_rules"] == [
            {"id": str(context["rules"][0]), "statement": "工期90天"}
        ]
        create_extracted_set(
            c,
            context["bid"],
            context["material"],
            [dict(untouched)],
            "complete",
            read_bundle(c, context["bid"], context["material"]),
        )
        later = create_extracted_set(
            c,
            context["bid"],
            context["material"],
            [dict(ended), dict(untouched)],
            "complete",
            read_bundle(c, context["bid"], context["material"]),
        )
        assert (
            next(item for item in rules(c, later) if item["statement"] == "工期90天")[
                "provenance_status"
            ]
            == "superseded"
        )


def test_defer_creates_known_gap_and_repeated_note_does_not_clone_package(context):
    request = body(context, "defer")
    first = apply(context, request)
    with connection() as c:
        items = rules(c, first)
        target = next(item for item in items if item["statement"] == "工期90天")
        assert target["provenance_status"] == "needs_revalidation"
        assert current_decision(c, context["bid"], "rule", target["id"]) is None
        assert (
            c.execute("SELECT coverage FROM rule_sets WHERE id=%s", (first,)).fetchone()["coverage"]
            == "partial"
        )
        assert (
            evidence_impacts(c, context["bid"], [str(context["evidences"][0])])[0]["state"]
            == "needs_revalidation"
        )
        count = c.execute(
            "SELECT count(*) AS n FROM rule_sets WHERE bid_id=%s", (context["bid"],)
        ).fetchone()["n"]
    request.update(
        base_rule_set_id=str(first),
        rule_ids=[str(target["id"])],
        reason="再次补记歧义，尚无生效依据",
    )
    assert apply(context, request) == first
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM rule_sets WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["n"]
            == count
        )


def test_invalid_quote_rolls_back_every_new_rule_decision_and_package(context):
    request = body(context)
    request["scope_basis"][0]["quote"] = "不存在于原文的整份替代声明"
    with pytest.raises(DomainError):
        apply(context, request)
    with connection() as c:
        assert (
            c.execute(
                "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["current_rule_set_id"]
            == context["rule_set"]
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM rule_sets WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM decisions WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["n"]
            == 2
        )


def test_explicit_partial_scope_cannot_be_confirmed_as_whole(context):
    with pytest.raises(DomainError) as error:
        apply(context, body(context, "whole_replace"))
    assert error.value.code == "SOURCE_EFFECTIVITY_SCOPE_CONFLICT"


@pytest.mark.parametrize("context", ["whole", "mixed"], indirect=True)
def test_whole_replacement_only_ends_exclusively_supported_obligations(context):
    new_set = apply(context, body(context, "whole_replace"))
    with connection() as c:
        items = rules(c, new_set)
        deadline = next(item for item in items if item["statement"] == "工期90天")
        warranty = next(item for item in items if item["statement"] == "质保24个月")
        assert deadline["provenance_status"] == "superseded"
        mixed = len(warranty["evidence_ids"]) > 1
        assert warranty["provenance_status"] == ("needs_revalidation" if mixed else "superseded")
        assert current_decision(c, context["bid"], "rule", warranty["id"]) is None


@pytest.mark.skipif(
    os.environ.get("RUN_SOURCE_EFFECTIVITY_HTTP") != "1", reason="需真实本地S3验证合成原件字节身份"
)
def test_http_effectivity_cookie_csrf_cas_schema_and_response_loss_replay(context, monkeypatch):
    login, password = uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (context["actor"], login, password_hash(password)),
        )
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": required("APP_ORIGIN")},
        )
        assert logged.status_code == 200
        csrf = logged.json()["csrf_token"]
        path = f"/api/v1/bids/{context['bid']}/source-effectivity"
        reading = client.get(path)
        assert reading.status_code == 200
        actual = app.openapi()
        Draft202012Validator(
            {
                "$ref": "#/components/schemas/SourceEffectivityView",
                "components": actual["components"],
            }
        ).validate(reading.json())
        request = body(context)
        headers = {
            "Origin": required("APP_ORIGIN"),
            "X-CSRF-Token": csrf,
            "Idempotency-Key": uuid4().hex,
        }
        endpoint = path + "-decisions"
        assert client.post(endpoint, json=request, headers=headers).status_code == 428
        assert (
            client.post(
                endpoint,
                json=request,
                headers=headers | {"Idempotency-Key": uuid4().hex, "If-Match": '"stale"'},
            ).status_code
            == 412
        )
        successful = headers | {"Idempotency-Key": uuid4().hex, "If-Match": reading.headers["etag"]}
        committed = client.post(endpoint, json=request, headers=successful)
        assert committed.status_code == 200
        Draft202012Validator(
            {"$ref": "#/components/schemas/RuleSetView", "components": actual["components"]}
        ).validate(committed.json())
        import apps.api.source_effectivity_routes as route

        def no_second_object_io(_):
            raise AssertionError("命令重放不应再次读取远端原件")

        monkeypatch.setattr(route, "verify_originals", no_second_object_io)
        repeated = client.post(endpoint, json=request, headers=successful)
        assert repeated.status_code == 200 and repeated.headers["Idempotent-Replayed"] == "true"
        assert repeated.json() == committed.json()
        changed = client.get(path)
        assert (
            changed.headers["etag"] != reading.headers["etag"] and changed.json()["requires_review"]
        )
        with connection() as c:
            assert not c.execute(
                "SELECT 1 FROM operations WHERE bid_id=%s", (context["bid"],)
            ).fetchone()


def test_actual_batch_500_explicit_selection_atomic_limit_and_replay(context):
    login, password = uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (context["actor"], login, password_hash(password)),
        )
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (context["bid"],))
        original = rules(c, context["rule_set"])[0]
        candidates = [
            dict(original) | {"statement": f"合成批量核对要求{index}", "id": uuid4()}
            for index in range(500)
        ]
        rule_set = create_extracted_set(
            c,
            context["bid"],
            context["material"],
            candidates,
            "complete",
            read_bundle(c, context["bid"], context["material"]),
        )
    with TestClient(app) as client:
        logged = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": required("APP_ORIGIN")},
        )
        assert logged.status_code == 200
        reading = client.get(f"/api/v1/bids/{context['bid']}/rules")
        assert reading.status_code == 200 and len(reading.json()["rules"]) == 500
        request = {
            "base_rule_set_id": str(rule_set),
            "rule_ids": [item["id"] for item in reading.json()["rules"]],
            "reason": "已逐条勾选合成要求，不包含未勾选项",
        }
        headers = {
            "Origin": required("APP_ORIGIN"),
            "X-CSRF-Token": logged.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
            "If-Match": reading.headers["etag"],
        }
        endpoint = f"/api/v1/bids/{context['bid']}/rules/decisions"
        too_many = client.post(
            endpoint,
            json=request | {"rule_ids": request["rule_ids"] + [str(uuid4())]},
            headers=headers | {"Idempotency-Key": uuid4().hex},
        )
        assert too_many.status_code == 422
        committed = client.post(endpoint, json=request, headers=headers)
        assert committed.status_code == 200 and all(
            item["confirmation"] == "confirmed" for item in committed.json()["rules"]
        )
        repeated = client.post(endpoint, json=request, headers=headers)
        assert repeated.status_code == 200 and repeated.json() == committed.json()
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_id=ANY(%s::uuid[])",
                    (context["bid"], request["rule_ids"]),
                ).fetchone()["n"]
                == 500
            )
