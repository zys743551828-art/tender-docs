"""真实受限PG必要范围协议反例；显式解析/模型结果夹具，不宣称解析或模型质量。"""

import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from packages.domain.core_recheck_scope import evaluate
from packages.domain.finding_lifecycle import lifecycle
from packages.domain.review_data import source_closure
from packages.domain.runtime import clean, connection, sha
from packages.rag.indexing import ensure_parse_sources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CORE_RECHECK_SCOPE_INTEGRATION") != "1", reason="需专用真实PG"
)


def add_document(c, bid, actor, pages, *, coverage="complete", declared_page_count=None):
    blob, version, parse = uuid4(), uuid4(), uuid4()
    digest = sha(str(blob))
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,10,'application/pdf')",
        (blob, digest, "blobs/" + digest),
    )
    c.execute(
        "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'显式范围夹具.pdf',now()-interval '3 hours','bid_body',%s)",
        (version, bid, blob, actor),
    )
    c.execute(
        "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            parse,
            bid,
            version,
            sha("range-parser"),
            sha({}),
            len(pages) if declared_page_count is None else declared_page_count,
            coverage,
            Jsonb([] if coverage == "complete" else ["EXPLICIT_UNREAD_RANGE"]),
        ),
    )
    evidence = []
    for number, (quality, text) in enumerate(pages, 1):
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,595,842,0,%s,%s)",
            (
                bid,
                parse,
                number,
                quality,
                Jsonb([] if quality == "usable" else ["NECESSARY_FINAL_DURATION_SCHEDULE_UNREAD"]),
            ),
        )
        if text:
            identity = uuid4()
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,%s,0,'text',%s,%s)",
                (identity, bid, parse, number, text, sha(text.encode())),
            )
            evidence.append(identity)
    _, source = ensure_parse_sources(c, bid, parse, publish_readable=True)
    closure = source_closure(c, [{"id": str(source)}])
    ref = {
        "id": str(source),
        "kind": "body",
        "digest": digest,
        "lineage_manifest_digest": sha(closure),
    }
    return {"version": version, "parse": parse, "evidence": evidence, "blob": blob, "ref": ref}


@pytest.mark.parametrize(
    "case",
    [
        "referenced_unread_schedule",
        "unreferenced_unread_conflict",
        "complete_body_unrelated_attachment_partial",
        "complete_body_rule_diagnostic",
        "cross_document_page_reference",
        "zero_declared_pages",
        "extra_physical_page",
        "missing_physical_page",
        "recurrence_after_earlier_success",
        "independent_conflict_not_closed_by_core",
    ],
)
def test_only_proved_complete_body_recheck_can_close_old_critical(case):
    with connection() as c:
        actor, bid, material, rule_set, rule_id = [uuid4() for _ in range(5)]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成必要范围')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'必要范围关闭资格','{}',%s)",
            (bid, actor),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        original = add_document(
            c,
            bid,
            actor,
            [("usable", "本项目总工期120天")]
            * (100 if case == "cross_document_page_reference" else 1),
        )
        unread = case in ("referenced_unread_schedule", "unreferenced_unread_conflict")
        positive = (
            "本项目暂定工期90天，subject to final duration schedule physical page 3"
            if case == "referenced_unread_schedule"
            else "本项目总工期90天"
        )
        if case == "cross_document_page_reference":
            positive += "；背景详见招标文件第100页。step2施工安排按已读正文执行"
        revised = add_document(
            c,
            bid,
            actor,
            [
                ("usable", positive),
                ("usable", "其他已读正文"),
                ("partial", "") if unread else ("usable", "联系人及无工期变更的正文"),
            ],
            coverage="partial" if unread else "complete",
            declared_page_count={
                "zero_declared_pages": 0,
                "extra_physical_page": 2,
                "missing_physical_page": 4,
            }.get(case),
        )
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha("requirement")),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'disqualification','本项目总工期必须90天','[]','[]',%s,'[]')",
            (rule_id, bid, rule_set, [original["evidence"][0]]),
        )
        as_of = c.execute("SELECT now()-interval '30 minutes' AS at").fetchone()["at"]
        reviews = []
        for index, doc in enumerate((original, revised)):
            command, operation, input_id, review = [uuid4() for _ in range(4)]
            manifest = clean(
                {
                    "bid_id": bid,
                    "body_version_id": doc["version"],
                    "parse_run_ids": [doc["parse"]],
                    "source_refs": [doc["ref"]],
                    "as_of": as_of,
                }
            )
            if index == 1 and case == "cross_document_page_reference":
                manifest["parse_run_ids"].append(str(original["parse"]))
                manifest["source_refs"].append(original["ref"] | {"kind": "tender"})
            if index == 1 and case == "complete_body_unrelated_attachment_partial":
                attachment = add_document(
                    c, bid, actor, [("partial", "附件仅有可读片段")], coverage="partial"
                )
                manifest["parse_run_ids"].append(str(attachment["parse"]))
                manifest["source_refs"].append(attachment["ref"])
            c.execute(
                "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'range_probe',%s,%s,%s)",
                (command, actor, bid, uuid4().hex, sha(str(command))),
            )
            c.execute(
                "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'review',%s,'succeeded','review/1',now())",
                (operation, bid, command, sha(str(operation))),
            )
            c.execute(
                "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)",
                (
                    input_id,
                    bid,
                    material,
                    doc["version"],
                    sha(str(input_id)),
                    sha("method"),
                    Jsonb(manifest),
                    doc["blob"],
                    sha(manifest),
                    as_of,
                    sha("context"),
                ),
            )
            c.execute(
                "INSERT INTO review_runs(id,bid_id,input_id,operation_id,created_at) VALUES(%s,%s,%s,%s,now()-(%s*interval '1 hour'))",
                (review, bid, input_id, operation, 2 - index),
            )
            reviews.append((review, operation, manifest))
        c.execute(
            "INSERT INTO matrix_checks(bid_id,review_id,rule_id,state,verdict,evidence_ids,revision) VALUES(%s,%s,%s,'succeeded','satisfied',%s,1)",
            (bid, reviews[1][0], rule_id, [revised["evidence"][0]]),
        )
        finding = uuid4()
        c.execute(
            "INSERT INTO findings(id,bid_id,review_id,rule_id,category,severity,verdict,statement,searched_ranges,evidence_ids) VALUES(%s,%s,%s,%s,'disqualification','critical','violated','旧稿工期违反90天要求','[]',%s)",
            (finding, bid, reviews[0][0], rule_id, [original["evidence"][0]]),
        )
        if case == "complete_body_rule_diagnostic":
            c.execute(
                "INSERT INTO validation_diagnostics(id,bid_id,operation_id,step_key,code,field_path,evidence_ids,action,message) VALUES(%s,%s,%s,'core-judgment','unit_mismatch',%s,%s,'needs_review','所选工期单位未核验')",
                (uuid4(), bid, reviews[1][1], "rules." + str(rule_id), [revised["evidence"][0]]),
            )
        if case == "recurrence_after_earlier_success":
            third_operation, third_review = uuid4(), uuid4()
            c.execute(
                "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) SELECT %s,bid_id,command_id,kind,%s,'succeeded',handler_version,now() FROM operations WHERE id=%s",
                (third_operation, sha(str(third_operation)), reviews[0][1]),
            )
            c.execute(
                "INSERT INTO review_runs(id,bid_id,input_id,operation_id,created_at) SELECT %s,bid_id,input_id,%s,now() FROM review_runs WHERE id=%s",
                (third_review, third_operation, reviews[0][0]),
            )
            c.execute(
                "INSERT INTO findings(id,bid_id,review_id,rule_id,category,severity,verdict,statement,searched_ranges,evidence_ids,carried_from_id) VALUES(%s,%s,%s,%s,'disqualification','critical','violated','真实V3再次违反',%s,%s,%s)",
                (
                    uuid4(),
                    bid,
                    third_review,
                    rule_id,
                    Jsonb(["全正文"]),
                    [original["evidence"][0]],
                    finding,
                ),
            )
        if case == "independent_conflict_not_closed_by_core":
            c.execute(
                "INSERT INTO audit_entries(id,bid_id,event_type,payload) VALUES(%s,%s,'content_check.finding_recorded',%s)",
                (
                    uuid4(),
                    bid,
                    Jsonb(
                        {
                            "finding_id": str(finding),
                            "kind": "consistency",
                            "issue_key": sha(str(finding)),
                        }
                    ),
                ),
            )
    with connection(True) as worker:
        row = worker.execute("SELECT * FROM findings WHERE id=%s", (finding,)).fetchone()
        proof = evaluate(
            worker,
            manifest=reviews[1][2],
            rule_id=rule_id,
            evidence_ids=[revised["evidence"][0]],
            operation_id=reviews[1][1],
        )
        state = lifecycle(worker, row)
        if case in ("complete_body_unrelated_attachment_partial", "cross_document_page_reference"):
            assert proof["eligible"] and state == "resolved_by_recheck"
        elif case in (
            "recurrence_after_earlier_success",
            "independent_conflict_not_closed_by_core",
        ):
            assert proof["eligible"] and state == "open"
        else:
            assert not proof["eligible"] and state == "open"
            if unread:
                assert 3 in proof["required_pages"] and 3 in proof["unread_pages"]
            elif case == "complete_body_rule_diagnostic":
                assert proof["diagnostic_ids"]
            else:
                assert proof["reason"] == "BODY_PHYSICAL_PAGE_SET_INVALID"
