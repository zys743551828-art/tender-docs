"""非owner PG人工事实集原子性与决定等价沿用；纯合成证据，不声称提取效果。"""

import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from packages.domain.fact_sets import carryover, current_facts, replace_set
from packages.domain.human_decisions import append_decision
from packages.domain.rule_sets import current_decision
from packages.domain.runtime import DomainError, connection, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_FACT_SETS_INTEGRATION") != "1", reason="需独立非owner PG"
)


@pytest.fixture
def context():
    with connection() as c:
        actor, bid, project, round_id, bidder, blob, version, snapshot, parse, evidence = [
            uuid4() for _ in range(10)
        ]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成事实机制')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成事实集','{}',%s)",
            (bid, actor),
        )
        c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成项目')", (project,))
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
            (blob, sha(str(blob)), f"workbench-checks/{blob}"),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成.pdf',now(),'source_original',%s)",
            (version, bid, blob, actor),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (snapshot, bid, sha(str(snapshot))),
        )
        c.execute(
            "INSERT INTO material_assignments(bid_id,snapshot_id,role,ordinal,document_version_id) VALUES(%s,%s,'tender',0,%s)",
            (bid, snapshot, version),
        )
        c.execute("UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s", (snapshot, bid))
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
            (parse, bid, version, sha("synthetic"), sha("config")),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
            (bid, parse),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','合成来源：质保24个月',%s)",
            (evidence, bid, parse, sha("合成来源：质保24个月".encode())),
        )
        c.execute("INSERT INTO fact_heads(bid_id,revision) VALUES(%s,0)", (bid,))
    return actor, bid, evidence


def fact(evidence, name):
    return {
        "subject": "合成主体",
        "scope": "本项目",
        "name": name,
        "value": "24",
        "unit": "月",
        "evidence_ids": [str(evidence)],
        "reason": "已逐页核对合成来源",
    }


def test_fact_snapshot_confirmation_exact_carry_and_effectivity_change(context):
    actor, bid, evidence = context
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        first, first_id = replace_set(
            c, bid, actor, "人工新增", kind="add", new_fact=fact(evidence, "质保期")
        )
        original = current_decision(c, bid, "fact", first_id)
        assert original["decision"] == "confirmed" and original["actor_id"] == actor
        second, second_id = replace_set(
            c, bid, actor, "追加事实", kind="add", new_fact=fact(evidence, "服务期")
        )
        head, items = current_facts(c, bid)
        assert head["fact_set_id"] == second and head["revision"] == 2 and len(items) == 2
        cloned = next(row for row in items if row["name"] == "质保期")
        inherited = current_decision(c, bid, "fact", cloned["id"])
        assert inherited["id"] == original["id"] and inherited["object_id"] == first_id
        assert cloned["id"] != first_id and carryover(c, cloned["id"])["from_fact_id"] == str(
            first_id
        )
        c.execute(
            "UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id IN(SELECT e.parent_id FROM retrieval.lineage_edges e JOIN retrieval.source_nodes n ON n.id=e.child_id JOIN document_versions v ON v.id=n.document_version_id WHERE v.bid_id=%s)",
            (bid,),
        )
        third, _ = replace_set(
            c, bid, actor, "重新核验新事实", kind="add", new_fact=fact(evidence, "维护期")
        )
        _, new_items = current_facts(c, bid)
        prior = next(row for row in new_items if row["name"] == "质保期")
        assert current_decision(c, bid, "fact", prior["id"]) is None
        assert len(new_items) == 3 and third != second and first != second
        assert (
            c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s", (bid,)).fetchone()["n"]
            == 3
        )


def test_fact_invalid_evidence_is_atomic_and_manual_resolve_is_forbidden(context):
    actor, bid, evidence = context
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        with pytest.raises(DomainError), c.transaction():
            replace_set(c, bid, actor, "错误依据", kind="add", new_fact=fact(uuid4(), "质保期"))
        head, items = current_facts(c, bid)
        assert head["revision"] == 0 and items == []
        assert (
            c.execute("SELECT count(*) AS n FROM fact_sets WHERE bid_id=%s", (bid,)).fetchone()["n"]
            == 0
        )
        with pytest.raises(DomainError) as error:
            append_decision(
                c,
                bid,
                actor,
                "finding",
                uuid4(),
                {"decision": "resolved_by_recheck", "reason": "试图人工关闭", "evidence_ids": []},
            )
        assert error.value.code == "DECISION_NOT_ALLOWED"


def test_manual_rule_correction_is_atomic_and_persists_after_model_omission(context):
    from packages.domain.review_data import read_bundle
    from packages.domain.rule_corrections import correct_rules
    from packages.domain.rule_sets import create_extracted_set, rules
    from packages.domain.runtime import uid

    actor, bid, evidence = context
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        material = c.execute(
            "SELECT current_material_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()["current_material_id"]
        old_set, old_rule, command = uid(), uid(), uid()
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (old_set, bid, material, sha(str(old_set))),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands,applicability_scope) VALUES(%s,%s,%s,'mandatory','质保24个月','[]','[]',NULL,%s,'[]',%s)",
            (
                old_rule,
                bid,
                old_set,
                [evidence],
                Jsonb({"bid_id": str(bid), "description": "本项目"}),
            ),
        )
        c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (old_set, bid))
        original = append_decision(
            c,
            bid,
            actor,
            "rule",
            old_rule,
            {"decision": "confirmed", "reason": "人工核对原条款", "evidence_ids": [str(evidence)]},
        )
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'rule_correction_probe',%s,%s,%s)",
            (command, actor, bid, str(command), sha(str(command))),
        )
        addition = {
            "kind": "disqualification",
            "statement": "原文质保条款须逐项核对",
            "conditions": ["质保24个月"],
            "exceptions": [],
            "maximum": None,
            "bands": [],
            "scope": {"bid_id": str(bid), "description": "本项目"},
            "evidence_quotes": [{"evidence_id": str(evidence), "quote": "合成来源：质保24个月"}],
        }
        request = {
            "base_rule_set_id": str(old_set),
            "reason": "补充并确认原文要求",
            "additions": [addition],
        }
        invalid = {
            **request,
            "additions": [
                {
                    **addition,
                    "evidence_quotes": [{"evidence_id": str(evidence), "quote": "原文没有这段"}],
                }
            ],
        }
        with pytest.raises(DomainError), c.transaction():
            correct_rules(c, bid, actor, command, invalid)
        assert (
            c.execute(
                "SELECT current_rule_set_id FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()["current_rule_set_id"]
            == old_set
        )
        assert (
            c.execute("SELECT count(*) AS n FROM rule_sets WHERE bid_id=%s", (bid,)).fetchone()["n"]
            == 1
        )
        new_set = correct_rules(c, bid, actor, command, request)
        items = rules(c, new_set)
        human = next(row for row in items if row["origin_kind"] == "human_added")
        assert human["manual_origin_rule_id"] == human["id"]
        assert current_decision(c, bid, "rule", human["id"])["decision"] == "confirmed"
        carried_original = next(row for row in items if row["origin_kind"] == "model_extracted")
        assert current_decision(c, bid, "rule", carried_original["id"])["id"] == original["id"]
        later = create_extracted_set(
            c, bid, material, [], "complete", read_bundle(c, bid, material)
        )
        retained = rules(c, later)
        assert len(retained) == 1 and retained[0]["origin_kind"] == "carried_human"
        assert (
            retained[0]["manual_origin_rule_id"] == human["id"]
            and retained[0]["provenance_status"] == "effective"
        )
        assert current_decision(c, bid, "rule", retained[0]["id"])["actor_id"] == actor
        assert (
            c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s", (bid,)).fetchone()["n"]
            == 2
        )
        from packages.domain.rule_corrections import reject_rule

        reject_command = uid()
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'rule_rejection_probe',%s,%s,%s)",
            (reject_command, actor, bid, str(reject_command), sha(str(reject_command))),
        )
        rejected, ended_set = reject_rule(
            c,
            bid,
            actor,
            retained[0]["id"],
            {"decision": "rejected", "reason": "人工有据判断此解释重复不适用", "evidence_ids": []},
            command_id=reject_command,
        )
        assert rejected["actor_id"] == actor
        ended = rules(c, ended_set)[0]
        assert ended["provenance_status"] == "superseded"
        again = create_extracted_set(
            c, bid, material, [dict(ended)], "complete", read_bundle(c, bid, material)
        )
        assert rules(c, again)[0]["provenance_status"] == "superseded"
        assert current_decision(c, bid, "rule", rules(c, again)[0]["id"])["decision"] == "rejected"
