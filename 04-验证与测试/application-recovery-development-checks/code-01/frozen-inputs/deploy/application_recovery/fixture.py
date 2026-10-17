"""Small self-owned synthetic archive fixture; no model invocation or qualification is minted."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from deploy.application_recovery import archive
from deploy.application_recovery import security as s


def password_hash(password):
    salt = secrets.token_hex(16)
    return (
        f"pbkdf2_sha256$600000${salt}$"
        + hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    )


def create(cluster, values, directory):
    port = archive.object_port(values)
    ids = {
        k: uuid4()
        for k in (
            "actor",
            "bid",
            "operation",
            "command",
            "material",
            "parse",
            "rules",
            "rule",
            "input",
            "review",
            "model",
            "chapter",
            "template",
            "report",
            "call",
            "evidence",
        )
    }
    credential = {
        "login_name": "ar-" + uuid4().hex,
        "password": secrets.token_urlsafe(24),
        "old_cookie": secrets.token_urlsafe(48),
        "source_session_secret": secrets.token_urlsafe(32),
    }
    s.save(directory / "member-credential.json", credential)
    raw_objects = {
        "original": b"Synthetic original: archive and permission checks only.\n",
        "model": b'{"synthetic":true,"formal_eligibility":"unavailable"}',
        "response": b'{"synthetic_unknown_call_response":true}',
        "chapter": b'{"synthetic_complete_chapter_archive":true}',
        "method": b'{"synthetic_judgment_method_original":true}',
        "template": b'{"synthetic_template_original":true}',
        "report": b"Synthetic report artifact, not a qualified review.\n",
    }
    objects = {}
    for kind, raw in raw_objects.items():
        identity = uuid4()
        key = "application-recovery/" + str(identity)
        port.put_object(
            Bucket=values["OBJECT_STORE_BUCKET"],
            Key=key,
            Body=raw,
            ContentType="application/octet-stream",
        )
        objects[kind] = {
            "id": identity,
            "key": key,
            "sha256": s.sha(raw),
            "bytes": len(raw),
            "document": uuid4(),
        }
    with psycopg.connect(s.connection_dsn(cluster, "api"), row_factory=dict_row) as c:
        c.execute("SET LOCAL search_path=design,public")
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,'合成应用恢复成员')", (ids["actor"],)
        )
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (ids["actor"], credential["login_name"], password_hash(credential["password"])),
        )
        token_digest = hmac.new(
            credential["source_session_secret"].encode(),
            credential["old_cookie"].encode(),
            hashlib.sha256,
        ).hexdigest()
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,clock_timestamp()+interval '1 day')",
            (uuid4(), ids["actor"], token_digest, s.sha(b"synthetic-old-csrf")),
        )
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成恢复原项目',%s,%s)",
            (
                ids["bid"],
                Jsonb(
                    {
                        "auto_review": False,
                        "consistency": True,
                        "scope_residue": True,
                        "show_prediction": True,
                        "pricing": True,
                        "result_reminder": True,
                        "accept_rules_as_written": False,
                    }
                ),
                ids["actor"],
            ),
        )
        engineering, round_id, bidder = uuid4(), uuid4(), uuid4()
        c.execute(
            "INSERT INTO engineering_projects(id,title) VALUES(%s,'合成恢复工程')", (engineering,)
        )
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, engineering),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成待核主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'preparation')",
            (ids["bid"], round_id, bidder),
        )
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (ids["bid"],))
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), ids["bid"]))
        for obj in objects.values():
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/octet-stream')",
                (obj["id"], obj["sha256"], obj["key"], obj["bytes"]),
            )
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'synthetic-archive.bin',clock_timestamp(),'source_original',%s)",
                (obj["document"], ids["bid"], obj["id"], ids["actor"]),
            )
        digest = objects["original"]["sha256"]
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'synthetic-recovery-fixture',%s,%s,%s)",
            (ids["command"], ids["actor"], ids["bid"], str(uuid4()), digest),
        )
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,'synthetic-paused-review',%s,'queued','synthetic-not-executable')",
            (ids["operation"], ids["bid"], ids["command"], digest),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (ids["material"], ids["bid"], digest),
        )
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial','[\"SYNTHETIC_ARCHIVE_FIXTURE\"]')",
            (ids["parse"], ids["bid"], objects["original"]["document"], digest, digest),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'partial','[\"SYNTHETIC_ARCHIVE_FIXTURE\"]')",
            (ids["bid"], ids["parse"]),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','合成结构样本非实际解析',%s)",
            (ids["evidence"], ids["bid"], ids["parse"], digest),
        )
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'partial')",
            (ids["rules"], ids["bid"], ids["material"], digest),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,evidence_ids,bands) VALUES(%s,%s,%s,'scoring','合成档案结构，不授评分资格','{}','{}',%s,'[]')",
            (ids["rule"], ids["bid"], ids["rules"], [ids["evidence"]]),
        )
        c.execute(
            "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at) VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
            (
                ids["input"],
                ids["bid"],
                ids["material"],
                objects["original"]["document"],
                digest,
                objects["method"]["sha256"],
                Jsonb({"synthetic": True, "method_original_blob_id": str(objects["method"]["id"])}),
            ),
        )
        c.execute(
            "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (ids["review"], ids["bid"], ids["input"], ids["operation"]),
        )
        c.execute(
            "INSERT INTO model_artifacts(id,artifact_blob_id,method_digest,scope,training_manifest_digest) VALUES(%s,%s,%s,%s,%s)",
            (
                ids["model"],
                objects["model"]["id"],
                objects["method"]["sha256"],
                Jsonb({"synthetic": True, "formal_eligibility": "unavailable"}),
                digest,
            ),
        )
        c.execute(
            "INSERT INTO chapter_packages(id,bid_id,review_id,rule_id,parse_id,required_ranges,read_ranges,unread_ranges,manifest_blob_id,digest) VALUES(%s,%s,%s,%s,%s,'[]','[]','[]',%s,%s)",
            (
                ids["chapter"],
                ids["bid"],
                ids["review"],
                ids["rule"],
                ids["parse"],
                objects["chapter"]["id"],
                objects["chapter"]["sha256"],
            ),
        )
        c.execute(
            "INSERT INTO model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state,response_ref) VALUES(%s,%s,'synthetic-unknown-never-provider-called',1,%s,%s,'unknown',%s)",
            (ids["call"], ids["operation"], digest, digest, objects["response"]["id"]),
        )
        c.execute(
            "INSERT INTO template_versions(id,name,kind,schema_digest,config,fontset_digest) VALUES(%s,'合成模板原件关系','outline',%s,%s,%s)",
            (
                ids["template"],
                digest,
                Jsonb({"synthetic": True, "original_blob_id": str(objects["template"]["id"])}),
                digest,
            ),
        )
        for published in (False, True):
            c.execute(
                "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload,published_at) VALUES(%s,%s,'tender.review.v1',%s,%s,CASE WHEN %s THEN clock_timestamp() ELSE NULL END)",
                (
                    uuid4(),
                    ids["operation"],
                    "synthetic-paused-" + str(uuid4()),
                    Jsonb({"synthetic": True, "never_dispatch": True}),
                    published,
                ),
            )
        from packages.knowledge.repository import create_candidate

        knowledge = create_candidate(
            c,
            ids["actor"],
            {
                "title": "合成恢复知识",
                "content": "合成工法原件仅验证恢复引用",
                "scope": "恢复合成样本",
                "source_version_ids": [str(objects["method"]["document"])],
            },
        )
        ids["knowledge"] = knowledge["id"]
        c.execute(
            "INSERT INTO audit_entries(id,actor_id,bid_id,event_type,payload) VALUES(%s,%s,%s,'report.requested',%s)",
            (
                uuid4(),
                ids["actor"],
                ids["bid"],
                Jsonb(
                    {
                        "synthetic": True,
                        "report_id": str(ids["report"]),
                        "review_id": str(ids["review"]),
                        "operation_id": str(ids["operation"]),
                        "artifact_kind": "review_report",
                        "format": "pdf",
                        "presentation_revision": 1,
                        "source_impact_revision": 1,
                    }
                ),
            ),
        )
        # Audit records retain typed originals without declaring a real report/standard acceptance.
        c.execute(
            "INSERT INTO audit_entries(id,actor_id,bid_id,event_type,payload) VALUES(%s,%s,%s,'recovery.synthetic_originals',%s)",
            (
                uuid4(),
                ids["actor"],
                ids["bid"],
                Jsonb(
                    {
                        "synthetic": True,
                        "method_blob_id": str(objects["method"]["id"]),
                        "template_blob_id": str(objects["template"]["id"]),
                        "report_original_version_id": str(objects["report"]["document"]),
                    }
                ),
            ),
        )
    result = {
        "ids": {k: str(v) for k, v in ids.items()},
        "objects": {
            k: {n: str(v) if n in {"id", "document"} else v for n, v in obj.items()}
            for k, obj in objects.items()
        },
        "synthetic_only": True,
        "provider_calls": 0,
    }
    s.save(directory / "fixture.json", result)
    return result
