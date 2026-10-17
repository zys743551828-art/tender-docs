"""真实本地BGE/受限PG独立RAG机制与来源反例；不启动消费者/收费模型。"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from psycopg.types.json import Jsonb

from packages.domain.runtime import connection
from packages.rag.core import EvidenceText, chunk_evidence, normalized_vector, validate_citation
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp
from packages.rag.indexing import compute_index, prepare_index, publish_index
from packages.rag.repository import RetrievalScope, assert_current_sources, insert_chunks, retrieve


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    out = Path(os.environ["QA_RAG_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    result = dict(
        scope="独立真实BGE+实际受限PG机制/来源/引用；SQL原文夹具不是实际PDF入库，非业务召回质量",
        passed=False,
        checks=[],
        failures=[],
        paid_generation_calls=0,
    )
    stage = "冻结"
    try:
        assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
        candidate = Path(os.environ["QA_RAG_CANDIDATE"])
        assert (
            hashlib.sha256(candidate.read_bytes()).hexdigest()
            == os.environ["QA_RAG_CANDIDATE_SHA256"]
        )
        declared = json.loads(candidate.read_text())
        frozen = out / "frozen-inputs"
        frozen.mkdir()
        for item in declared["sources"]:
            source = Path(item["path"])
            assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"], source
            target = frozen / source.relative_to(Path.cwd())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        (frozen / "candidate.json").write_bytes(candidate.read_bytes())

        def check(name, condition, observation=None):
            row = dict(name=name, passed=bool(condition))
            if observation is not None:
                row["observation"] = observation
            result["checks"].append(row)
            if not condition:
                result["failures"].append(row)

        member, bid, foreign_bid = uuid4(), uuid4(), uuid4()
        with connection() as c:
            c.execute(
                "INSERT INTO members(id,display_name) VALUES(%s,'RAG独立SQL证据夹具')", (member,)
            )
            for target in [bid, foreign_bid]:
                c.execute(
                    "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立RAG测试','{}',%s)",
                    (target, member),
                )
            api_role = c.execute(
                "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        with connection(True) as c:
            worker_role = c.execute(
                "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        check(
            "真实API/Worker独立非super登录",
            not api_role["rolsuper"]
            and not worker_role["rolsuper"]
            and api_role["current_user"] != worker_role["current_user"],
            {"api": api_role["current_user"], "worker": worker_role["current_user"]},
        )
        dense = DenseHttp()
        lexical = LexicalHttp(dense)
        reranker = RerankHttp(dense)
        result["dense_identity"] = dense.identity

        def seed(label, project=bid, second=False, version=None):
            version = version or uuid4()
            blob, parse = uuid4(), uuid4()
            texts = [f"独立合成{label}：本项目道路围挡高度为2米，禁止误用其他项目。"]
            if second:
                texts.append("本项目排水沟宽度为0.6米，管道接口采用柔性连接。物理第二页。")
            with connection() as c:
                exists = c.execute(
                    "SELECT id FROM document_versions WHERE id=%s", (version,)
                ).fetchone()
                if not exists:
                    c.execute(
                        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
                        (blob, sha(str(blob)), "workbench-checks/rag-sql-fixture/" + str(blob)),
                    )
                    c.execute(
                        "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now()-interval '2 days','source_original',%s)",
                        (version, project, blob, "SQL夹具-" + label + ".pdf", member),
                    )
                c.execute(
                    "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,'partial',%s)",
                    (
                        parse,
                        project,
                        version,
                        sha("independent-sql-parse"),
                        sha(str(parse)),
                        len(texts),
                        Jsonb(["SQL fixture, not actual PDF parsing"]),
                    ),
                )
                evidence = []
                for number, text in enumerate(texts, 1):
                    c.execute(
                        "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,595,842,0,'usable','[]')",
                        (project, parse, number),
                    )
                    identity = uuid4()
                    evidence.append(identity)
                    c.execute(
                        "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,%s,0,'text',%s,%s)",
                        (identity, project, parse, number, text, sha(text)),
                    )
            with connection(True) as c:
                prepared = prepare_index(c, project, parse, dense, lexical)

            # 仅在compute期间观测图锁，真实embed仍由实际BGE运行。
            class ObservedDense:
                identity = dense.identity

                def embed(self, texts):
                    with connection() as observer:
                        free = observer.execute(
                            "SELECT pg_try_advisory_xact_lock(90127,1) AS free"
                        ).fetchone()["free"]
                    if not free:
                        raise AssertionError("compute期间不应持来源图锁")
                    return dense.embed(texts)

            computed = compute_index(prepared, ObservedDense())
            with connection(True) as c:
                with patch(
                    "httpx.Client.request", side_effect=AssertionError("publish期间禁止模型HTTP")
                ):
                    indexed = publish_index(c, computed)
            return dict(
                project=project,
                version=version,
                parse=parse,
                texts=texts,
                evidence=evidence,
                prepared=prepared,
                computed=computed,
                **indexed,
            )

        stage = "真实索引与向量"
        sources = {
            label: seed(label, foreign_bid if label == "foreign" else bid, second=label == "base")
            for label in [
                "base",
                "future",
                "withdrawn",
                "grouped",
                "policy",
                "bounds",
                "foreign",
                "reindex",
            ]
        }
        base = sources["base"]
        profile = base["profile_id"]
        with connection(True) as c:
            rows = c.execute(
                "SELECT e.embedding::text AS vector FROM retrieval.embeddings e JOIN retrieval.chunks ch ON ch.id=e.chunk_id WHERE ch.parse_id=%s",
                (base["parse"],),
            ).fetchall()
        vectors = [json.loads(row["vector"]) for row in rows]
        check(
            "实际BGE和PG存储1024有限单位向量",
            len(vectors) == 2
            and all(
                len(v) == 1024
                and all(math.isfinite(x) for x in v)
                and abs(math.sqrt(math.fsum(x * x for x in v)) - 1) < 1e-5
                for v in vectors
            ),
        )
        check("真实index compute不持图锁/发布不调用HTTP", True, {"indexed_sources": len(sources)})
        long_text = "中文施工组织与排水措施Road_0123456789，保持原文边界。" * 900
        long_block = EvidenceText(uuid4(), uuid4(), 2, long_text)
        long_chunks = chunk_evidence([long_block], dense)
        check(
            "实际tokenizer长中文分块保页且原文精确",
            len(long_chunks) > 1
            and all(
                0 < c.token_count <= 1200
                and c.physical_page == 2
                and c.text in long_text
                and c.digest == sha(c.text)
                for c in long_chunks
            ),
        )
        for label, vector in [
            ("wrong_dimensions", [1.0] * 1023),
            ("nan", [float("nan")] * 1024),
            ("inf", [float("inf")] * 1024),
            ("zero", [0.0] * 1024),
        ]:
            try:
                normalized_vector(vector)
                rejected = False
            except ValueError:
                rejected = True
            check("非法向量" + label + "拒绝", rejected)

        def eligible(source, as_of=None, groups=(), formal=False):
            with connection() as c:
                return c.execute(
                    "SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,%s)",
                    (
                        [source["source_id"]],
                        as_of or datetime.now(timezone.utc),
                        list(groups),
                        formal,
                    ),
                ).fetchall()

        check("正常来源正向返回", len(eligible(base)) == 2)
        with connection() as c:
            for label in ["future", "policy", "withdrawn"]:
                source = sources[label]
                ancestor, intermediate = uuid4(), uuid4()
                known = datetime.now(timezone.utc) + (
                    timedelta(days=1) if label == "future" else -timedelta(days=1)
                )
                for identity, policy, kind in [
                    (
                        ancestor,
                        "official_label" if label == "policy" else "prebid_content",
                        "document",
                    ),
                    (intermediate, "prebid_content", "summary"),
                ]:
                    c.execute(
                        "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,%s,%s,%s,%s)",
                        (
                            identity,
                            source["version"],
                            kind,
                            policy,
                            known
                            if identity == ancestor
                            else datetime.now(timezone.utc) - timedelta(days=1),
                            sha(str(identity)),
                        ),
                    )
                    c.execute(
                        "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,%s,now()-interval '2 days')",
                        (
                            identity,
                            "withdrawn"
                            if label == "withdrawn" and identity == ancestor
                            else "published",
                        ),
                    )
                c.execute(
                    "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s),(%s,%s)",
                    (source["source_id"], intermediate, intermediate, ancestor),
                )
            group = uuid4()
            c.execute(
                "INSERT INTO retrieval.source_groups(id,reason) VALUES(%s,'独立禁止组夹具')",
                (group,),
            )
            c.execute(
                "INSERT INTO retrieval.source_group_memberships(source_id,group_id) VALUES(%s,%s)",
                (sources["grouped"]["original_source_id"], group),
            )
        check("多跳future祖先当前排除", not eligible(sources["future"]))
        check(
            "future在允许业务时点后正向",
            len(eligible(sources["future"], datetime.now(timezone.utc) + timedelta(days=2))) == 1,
        )
        check("多跳withdrawn祖先排除", not eligible(sources["withdrawn"]))
        check(
            "禁止组传递祖先排除与无禁组正向",
            not eligible(sources["grouped"], groups=[group])
            and len(eligible(sources["grouped"])) == 1,
        )
        check(
            "正式评分排除标签祖先，普通许可用途单独",
            not eligible(sources["policy"], formal=True)
            and len(eligible(sources["policy"], formal=False)) == 1,
        )
        instant = datetime.now(timezone.utc) - timedelta(hours=1)
        upper = instant + timedelta(seconds=10)
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET valid_from=%s,valid_until=%s,revision=revision+1 WHERE source_id=%s",
                (instant, upper, sources["bounds"]["source_id"]),
            )
        check(
            "业务as_of区间下界含上界不含",
            not eligible(sources["bounds"], instant - timedelta(microseconds=1))
            and len(eligible(sources["bounds"], instant)) == 1
            and not eligible(sources["bounds"], upper),
        )
        check(
            "审计冻结不参与有效时点SQL",
            len(eligible(sources["bounds"], instant + timedelta(seconds=5))) == 1,
        )

        class NoInference:
            identity = dense.identity

            def embed(self, texts):
                raise AssertionError("拒绝前不得额外推理")

        stage = "跨项目与profile"
        with connection() as c:
            try:
                retrieve(
                    c,
                    RetrievalScope(
                        bid,
                        "assist",
                        (sources["foreign"]["source_id"],),
                        datetime.now(timezone.utc),
                    ),
                    "围挡",
                    NoInference(),
                    profile,
                    "a" * 64,
                    lexical,
                )
                foreign_rejected = False
            except ValueError:
                foreign_rejected = True
        check("跨项目私有源在推理前拒绝", foreign_rejected)
        bad_profile = NoInference()
        bad_profile.identity = {**dense.identity, "preprocessing_digest": "0" * 64}
        with connection() as c:
            try:
                retrieve(
                    c,
                    RetrievalScope(bid, "assist", (base["source_id"],), datetime.now(timezone.utc)),
                    "围挡",
                    bad_profile,
                    profile,
                    "a" * 64,
                    lexical,
                )
                profile_rejected = False
            except ValueError:
                profile_rejected = True
        check("同1024维但profile不符仍推理前拒绝", profile_rejected)
        try:
            RetrievalScope(bid, "formal_score", (base["source_id"],), datetime.now(timezone.utc))
            purpose_rejected = False
        except ValueError:
            purpose_rejected = True
        check("普通检索构造不接正式评分用途", purpose_rejected)

        stage = "真实混合检索与引用"
        plans = []

        class ObservedConnection:
            def __init__(self, native):
                self.native = native

            def __getattr__(self, name):
                return getattr(self.native, name)

            def execute(self, query, params=None):
                text = query.as_string(self.native) if hasattr(query, "as_string") else query
                if "permitted_embeddings AS MATERIALIZED" in text:
                    plan = self.native.execute(
                        "EXPLAIN (FORMAT JSON,COSTS OFF) " + text, params
                    ).fetchone()["QUERY PLAN"]
                    plans.append(plan)
                return self.native.execute(query, params)

        scope = RetrievalScope(bid, "assist", (base["source_id"],), datetime.now(timezone.utc))
        with connection() as c:
            found = retrieve(
                ObservedConnection(c),
                scope,
                "围挡高度",
                dense,
                profile,
                "a" * 64,
                lexical,
                reranker,
            )
        check(
            "真实RRF与BGE重排有实际引用",
            found["route"].endswith("+reranker")
            and bool(found["evidence"])
            and bool(reranker.last_identity),
            {"route": found["route"], "reranker_identity": reranker.last_identity},
        )
        text = json.dumps(plans, ensure_ascii=False, default=str)
        check(
            "实际PG先白名单物化精确向量路由",
            bool(plans)
            and "CTE allowed" in text
            and "CTE permitted_embeddings" in text
            and "embeddings_cosine_hnsw" not in text,
        )
        (out / "actual-explain.json").write_text(
            json.dumps(plans, ensure_ascii=False, default=str, indent=2) + "\n"
        )
        citations_ok = True
        for item in found["evidence"]:
            citations_ok &= (
                item.physical_page in [1, 2]
                and item.parse_id == base["parse"]
                and item.text in base["texts"][item.physical_page - 1]
            )
            citations_ok &= (
                validate_citation(
                    item.citation_id, item.text, item.physical_page, found["evidence"]
                )
                == item
            )
        check("自有原文夹具逐字/物理页/parse引用一致", citations_ok)
        item = found["evidence"][0]
        for label, citation, quote, page in [
            ("伪ID", "not-supplied", item.text, item.physical_page),
            ("错页", item.citation_id, item.text, 3),
            ("伪引文", item.citation_id, "从未供应的资质条件", item.physical_page),
        ]:
            try:
                validate_citation(citation, quote, page, found["evidence"])
                rejected = False
            except ValueError:
                rejected = True
            check(label + "引用拒绝", rejected)

        class UnavailableReranker:
            def rank(self, query, texts):
                raise RuntimeError("explicit QA local rerank failure")

        with connection() as c:
            fallback = retrieve(
                c, scope, "围挡高度", dense, profile, "a" * 64, lexical, UnavailableReranker()
            )
        check(
            "重排不可用明确RRF降级",
            fallback["route"].endswith("+reranker_unavailable") and bool(fallback["evidence"]),
        )

        stage = "应用时图权威锁和来源变更"
        with connection() as c:
            assert_current_sources(c, found)
            with connection() as second:
                locked = not second.execute(
                    "SELECT pg_try_advisory_xact_lock(90127,1) AS free"
                ).fetchone()["free"]
        with connection() as second:
            released = second.execute(
                "SELECT pg_try_advisory_xact_lock(90127,1) AS free"
            ).fetchone()["free"]
        check("应用校验持图锁至commit再释放", locked and released)
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
                (base["original_source_id"],),
            )
        with connection() as c:
            try:
                assert_current_sources(c, found)
                stale_rejected = False
            except ValueError:
                stale_rejected = True
        check("检索后祖先撤回应用时拒绝", stale_rejected)
        reindex = sources["reindex"]
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
                (reindex["original_source_id"],),
            )
        # 不改旧parse；再次发布同一冻结计算也不得复活已撤回原件。
        with connection(True) as c:
            try:
                publish_index(c, reindex["computed"])
                resurrection_rejected = False
            except ValueError:
                resurrection_rejected = True
        check("重发索引不得复活撤回原件根", resurrection_rejected)
        wrong = replace(base["prepared"].chunks[0], id=uuid4(), physical_page=2)
        with connection(True) as c:
            try:
                insert_chunks(c, bid, base["source_id"], [wrong], lexical)
                forged_rejected = False
            except ValueError:
                forged_rejected = True
        check("错误物理页的原文chunk不可写入", forged_rejected)
        result.update(
            passed=not result["failures"],
            bid_id=str(bid),
            foreign_bid_id=str(foreign_bid),
            source_ids={k: str(v["source_id"]) for k, v in sources.items()},
            candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
    except Exception as error:
        result["fatal"] = dict(
            stage=stage,
            type=type(error).__name__,
            message="实际RAG独立验证未完成；不输出私有配置/token",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    print(
        dict(
            passed=result["passed"],
            checks=len(result["checks"]),
            failures=result["failures"],
            fatal=result.get("fatal"),
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
