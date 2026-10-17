"""真实本地BGE＋受限PG＋ASGI：相同命令重放仍须核对来源head。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import apps.api.rag_routes as rag_routes
from apps.api.auth import password_hash
from apps.api.main import app
from packages.domain.runtime import connection
from packages.rag.http_port import DenseHttp, LexicalHttp
from packages.rag.indexing import compute_index, prepare_index, publish_index


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def main():
    out = Path(os.environ["QA_RAG_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    result = {
        "scope": "独立真实本地BGE/受限PG/当前ASGI检索重放反例；SQL原文夹具非真实PDF解析，不冒充live8802",
        "passed": False,
        "paid_model_requests": 0,
    }
    try:
        assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
        candidate = Path(
            "/workspace/tender-docs/04-验证与测试/rag-development-checks/candidate-02/candidate.json"
        )
        assert (
            hashlib.sha256(candidate.read_bytes()).hexdigest()
            == "03cbcf424b021d91c08d398e4a482b38b957c7fa0def1c6f2be7a86bd095e101"
        )
        declared = json.loads(candidate.read_text())
        frozen = out / "frozen-inputs"
        for row in declared["sources"]:
            p = Path(row["path"])
            assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
            target = frozen / p.relative_to(Path.cwd())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
        member = uuid4()
        login = "rag-qa-" + uuid4().hex
        password = secrets.token_urlsafe(24)
        with connection() as c:
            identity = c.execute(
                "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            assert not identity["rolsuper"]
            c.execute(
                "INSERT INTO members(id,display_name) VALUES(%s,%s)", (member, "RAG独立重放夹具")
            )
            c.execute(
                "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
                (member, login, password_hash(password)),
            )
        with TestClient(app) as client:
            origin = os.environ["APP_ORIGIN"]
            logged = client.post(
                "/api/v1/auth/login",
                json={"login_name": login, "password": password},
                headers={"Origin": origin},
            )
            assert logged.status_code == 200
            csrf = logged.json()["csrf_token"]
            headers = {"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex}
            created = client.post(
                "/api/v1/bids", json={"title": "RAG独立来源head重放验证"}, headers=headers
            )
            assert created.status_code == 201
            bid = created.json()["id"]
            version, blob, parse, evidence = [uuid4() for _ in range(4)]
            text = "本项目道路围挡高度不得低于2米。排水沟宽度为0.6米。独立合成来源。"
            with connection() as c:
                c.execute(
                    "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
                    (blob, sha(str(blob)), "workbench-checks/rag-sql-fixture/" + str(blob)),
                )
                c.execute(
                    "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'SQL证据夹具-不是实际解析.pdf',now()-interval '1 day','bid_body',%s)",
                    (version, bid, blob, member),
                )
                c.execute(
                    "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial',%s)",
                    (
                        parse,
                        bid,
                        version,
                        sha("independent-fixture-parser"),
                        sha(str(parse)),
                        Jsonb(["SQL fixture; actual PDF parsing not claimed"]),
                    ),
                )
                c.execute(
                    "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",
                    (bid, parse),
                )
                c.execute(
                    "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
                    (evidence, bid, parse, text, sha(text)),
                )
                c.execute(
                    "UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (version, bid)
                )
            dense = DenseHttp()
            lexical = LexicalHttp(dense)
            with connection(True) as c:
                prepared = prepare_index(c, bid, parse, dense, lexical)
            computed = compute_index(prepared, dense)
            with connection(True) as c:
                index = publish_index(c, computed)
            body = {"bid_id": bid, "context": "body_evidence", "query": "围挡高度要求", "limit": 4}
            key = uuid4().hex
            headers["Idempotency-Key"] = key
            first = client.post("/api/v1/retrievals", json=body, headers=headers)
            assert first.status_code == 200 and first.json()["hits"]
            unchanged = client.post("/api/v1/retrievals", json=body, headers=headers)
            assert (
                unchanged.status_code == 200
                and unchanged.headers.get("Idempotent-Replayed") == "true"
            )
            race_checks = []
            original_retrieve = rag_routes.retrieve
            for race in ["body", "material"]:
                old_material, new_material = uuid4(), uuid4()
                if race == "material":
                    with connection() as c:
                        for snapshot, revision in [(old_material, 1), (new_material, 2)]:
                            c.execute(
                                "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,'{}',%s)",
                                (snapshot, bid, revision, sha(str(snapshot))),
                            )
                        c.execute(
                            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
                            (old_material, bid, version),
                        )
                        c.execute(
                            "UPDATE bid_profiles SET current_body_id=NULL,current_material_id=%s WHERE bid_id=%s",
                            (old_material, bid),
                        )
                race_key = uuid4().hex
                headers["Idempotency-Key"] = race_key
                with connection() as c:
                    before = c.execute(
                        "SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s",
                        (bid,),
                    ).fetchone()["n"]

                def after_real_retrieval(*args, **kwargs):
                    actual = original_retrieve(*args, **kwargs)
                    with connection() as other:
                        if race == "body":
                            other.execute(
                                "UPDATE bid_profiles SET current_body_id=NULL WHERE bid_id=%s",
                                (bid,),
                            )
                        else:
                            other.execute(
                                "UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s",
                                (new_material, bid),
                            )
                    return actual

                with patch.object(rag_routes, "retrieve", side_effect=after_real_retrieval):
                    raced = client.post("/api/v1/retrievals", json=body, headers=headers)
                with connection() as c:
                    after = c.execute(
                        "SELECT count(*) AS n FROM retrieval.retrieval_traces WHERE bid_id=%s",
                        (bid,),
                    ).fetchone()["n"]
                    commands = c.execute(
                        "SELECT count(*) AS n FROM commands WHERE target_id=%s AND idempotency_key=%s",
                        (bid, race_key),
                    ).fetchone()["n"]
                    c.execute(
                        "UPDATE bid_profiles SET current_body_id=%s,current_material_id=NULL WHERE bid_id=%s",
                        (version, bid),
                    )
                race_checks.append(
                    dict(
                        name=race + "_head_change_after_real_retrieval",
                        status=raced.status_code,
                        trace_rollback=before == after,
                        command_rollback=commands == 0,
                        passed=raced.status_code == 409 and before == after and commands == 0,
                    )
                )
            result["controlled_race_checks"] = race_checks
            headers["Idempotency-Key"] = key
            with connection() as c:
                old = c.execute(
                    "SELECT * FROM retrieval.source_heads WHERE source_id=%s",
                    (index["original_source_id"],),
                ).fetchone()
                c.execute(
                    "UPDATE retrieval.source_heads SET revision=revision+1,valid_until=now()+interval '30 days' WHERE source_id=%s",
                    (index["original_source_id"],),
                )
                new = c.execute(
                    "SELECT * FROM retrieval.source_heads WHERE source_id=%s",
                    (index["original_source_id"],),
                ).fetchone()
            replay = client.post("/api/v1/retrievals", json=body, headers=headers)
            result.update(
                bid_id=bid,
                source_id=str(index["source_id"]),
                root_id=str(index["original_source_id"]),
                first_status=first.status_code,
                unchanged_replay_status=unchanged.status_code,
                old_head_revision=old["revision"],
                new_head_revision=new["revision"],
                old_head_valid_until=str(old["valid_until"]),
                new_head_valid_until=str(new["valid_until"]),
                replay_after_head_change_status=replay.status_code,
                replayed_original=replay.json() == first.json(),
                expected_after_change=409,
                passed=replay.status_code == 409 and all(item["passed"] for item in race_checks),
                first_response=first.json(),
                after_head_change_response=replay.json(),
                dense_identity=dense.identity,
                api_identity=dict(identity),
            )
    except Exception as error:
        result["failure"] = {
            "type": type(error).__name__,
            "message": "独立RAG反例执行异常；不输出临时账号/口令/token",
        }
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    print(
        {
            k: result.get(k)
            for k in [
                "passed",
                "first_status",
                "unchanged_replay_status",
                "replay_after_head_change_status",
                "old_head_revision",
                "new_head_revision",
                "failure",
            ]
        }
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
