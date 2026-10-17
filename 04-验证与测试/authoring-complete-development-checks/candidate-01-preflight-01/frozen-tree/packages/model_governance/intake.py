"""API-only orchestration: exact server artifacts -> clean purpose exports, never client labels."""

from uuid import UUID

from packages.domain.runtime import DomainError, clean, fetch, now, sha
from packages.model_governance import features, leakage
from packages.model_governance.common import GovernanceError
from packages.rag.indexing import original_source_id, parse_source_id
from packages.research import authority, batches, outcomes


def metadata(c, run_id, repeat_index):
    run = fetch(
        c,
        "SELECT r.*,p.request FROM research_runs r JOIN experiment_previews p ON p.id=r.preview_id WHERE r.id=%s",
        (run_id,),
    )
    if run["state"] != "succeeded":
        raise DomainError(
            "GOVERNANCE_RESEARCH_INCOMPLETE", "训练输入须来自已完整完成且未暂停的冻结研究批次", 409
        )
    pair = batches.manifest_pair_metadata(c, run["request"])
    rows = c.execute(
        "SELECT item.sample_id,item.operation_id,b.* FROM research_run_items item JOIN blobs b ON b.id=item.result_blob_id WHERE item.run_id=%s AND item.repeat_index=%s AND item.state='succeeded' ORDER BY item.sample_id",
        (run_id, repeat_index),
    ).fetchall()
    if {str(row["sample_id"]) for row in rows} != set(run["request"]["sample_ids"]):
        raise DomainError(
            "GOVERNANCE_REPLICATE_INCOMPLETE", "冻结重复轮次必须覆盖完整样本集合", 409
        )
    return {"run": run, "pair": pair, "results": rows, "repeat_index": repeat_index}


def read_materials(snapshot):
    # Called only after metadata transaction closes. API may read supervised artifacts;
    # the CPU training process will only receive separate bounded SQL-purpose exports.
    dataset, split = batches.read_pair(snapshot["pair"])
    results = {str(row["sample_id"]): batches.read_manifest(row) for row in snapshot["results"]}
    return {"dataset": dataset, "split": split, "results": results}


def package_metadata(c, dataset):
    return {
        sample["id"]: [
            batches.manifest_metadata(
                c, ref["manifest_blob_id"], "application/vnd.tender.chapter-package+json"
            )
            for ref in sample["chapter_packages"]
        ]
        for sample in dataset["samples"]
    }


def read_packages(metadata_by_sample):
    return {
        key: [batches.read_manifest(row) for row in rows]
        for key, rows in metadata_by_sample.items()
    }


def construct_exports(*, origin, protocol, materials, packages, calibration_group_ids):
    dataset, split = materials["dataset"], materials["split"]
    calibration = set(calibration_group_ids)
    if len(calibration_group_ids) != len(calibration):
        raise GovernanceError("CALIBRATION_DUPLICATE", "Calibration groups must not repeat")
    train_groups = {
        sample["source_group_id"]
        for sample in dataset["samples"]
        if split["assignments"][sample["id"]] == "train"
    }
    if not calibration <= train_groups:
        raise GovernanceError(
            "CALIBRATION_LEAK",
            "Calibration groups must be an explicit independent subset of frozen training groups",
        )
    members, rows, final_features, final_labels = [], [], [], []
    for sample in dataset["samples"]:
        side = split["assignments"][sample["id"]]
        purpose = {
            "train": "calibration" if sample["source_group_id"] in calibration else "fit",
            "validation": "development",
            "test": "final",
            "excluded": "excluded",
        }[side]
        frozen_identity = sample["identity_snapshot"]
        member = {
            "id": sample["id"],
            "purpose": purpose,
            "project_id": frozen_identity["engineering_project_id"],
            "group_id": sample["source_group_id"],
            "body_digest": sample["body_digest"],
            "template_key": sample.get("template_key"),
            "near_duplicate_key": sample.get("near_duplicate_key"),
            "known_at": sample["known_at"],
            "submitted_at": sample["submitted_at"],
        }
        members.append(member)
        if purpose == "excluded":
            continue
        result = materials["results"][sample["id"]]
        if result["sample_id"] != sample["id"]:
            raise GovernanceError(
                "RESULT_SAMPLE_MISMATCH", "Research result is bound to a different sample"
            )
        maximum = sample["label"]["maximum"]
        vector = features.build(
            packages[sample["id"]],
            result["items"],
            technical_maximum=maximum,
            maximum_evidence_verified=True,
        )
        if vector["status"] != "complete":
            raise GovernanceError(
                "INCOMPLETE_FEATURES",
                "Every fitting and final member requires complete valid three-judgment features; do not silently discard failures",
            )
        method = packages[sample["id"]][0]["method_digest"]
        if any(package["method_digest"] != method for package in packages[sample["id"]]):
            raise GovernanceError(
                "METHOD_MISMATCH", "One frozen judgment method per observation required"
            )
        row = {
            "id": sample["id"],
            "features": vector["values"],
            "target": float(sample["label"]["technical_total"]) * 100 / float(maximum),
            "maximum": float(maximum),
            "project_id": member["project_id"],
            "group_id": member["group_id"],
            "method": method,
            "purpose": purpose,
            "engineering_type": frozen_identity["engineering_type"],
        }
        if purpose == "final":
            # Explicitly separate: trainer never gets these targets or full dataset manifest.
            final_features.append({k: v for k, v in row.items() if k != "target"})
            final_labels.append({k: v for k, v in row.items() if k != "features"})
        else:
            rows.append(row)
    leakage.check_members(members, test_start=split["cutoff"])
    bundle = {
        "schema": "purpose-bound-training-export/1",
        "origin": origin,
        "protocol": protocol,
        "members": members,
        "rows": rows,
        "test_start": split["cutoff"],
    }
    return {
        "training": bundle,
        "final_features": {
            "schema": "clean-final-feature-export/1",
            "origin": origin,
            "rows": final_features,
        },
        "final_labels": {
            "schema": "sealed-final-label-export/1",
            "origin": origin,
            "rows": final_labels,
        },
    }


def capture(c, *, snapshot, materials, packages):
    run = snapshot["run"]
    latest = metadata(c, run["id"], snapshot["repeat_index"])
    if sha(clean(latest)) != sha(clean(snapshot)):
        raise DomainError("GOVERNANCE_RESEARCH_CHANGED", "研究执行结果或冻结输入身份已变化", 409)
    dataset, split = batches.validate_manifests(
        c, run["request"], (materials["dataset"], materials["split"])
    )
    current = {str(row["id"]): row for row in outcomes.sample_rows(c)}
    members, chapters, parses, sources = [], [], [], []
    evidence: list[dict] = []
    for sample in dataset["samples"]:
        row = current[sample["id"]]
        verification = fetch(
            c,
            "SELECT h.decision_id,d.evidence_ids FROM decision_heads h JOIN decisions d ON d.id=h.decision_id WHERE h.bid_id=%s AND h.object_kind='outcome' AND h.object_id=%s AND d.decision='verified'",
            (row["bid_id"], row["outcome_id"]),
        )
        identity = row["identity_snapshot"]
        members.append(
            {
                "sample_id": sample["id"],
                "bid_id": str(row["bid_id"]),
                "submission_id": sample["submission_id"],
                "outcome_id": sample["outcome_id"],
                "group_id": str(row["source_group_id"]),
                "project_id": identity["engineering_project_id"],
                "body_digest": row["body_digest"],
                "template_key": row["template_group"],
                "near_duplicate_key": row["near_duplicate_group"],
                "grouping_decision_id": row["grouping_decision_id"],
                "verification_decision_id": str(verification["decision_id"]),
                "identity_authority_id": identity["authority_id"],
                "identity_snapshot": identity,
                "submitted_at": row["submitted_at"].isoformat(),
                "known_at": row["known_at"].isoformat(),
            }
        )
        cp_rows = c.execute(
            "SELECT cp.*,a.bindings,a.as_of FROM chapter_packages cp JOIN research_chapter_authorities a ON a.chapter_package_id=cp.id WHERE cp.id=ANY(%s::uuid[]) ORDER BY cp.id",
            ([ref["id"] for ref in sample["chapter_packages"]],),
        ).fetchall()
        expected_rules = {
            str(r["id"])
            for r in c.execute(
                "SELECT rule.id FROM submission_records s JOIN review_runs r ON r.id=s.review_id JOIN review_inputs i ON i.id=r.input_id JOIN rule_items rule ON rule.rule_set_id=(i.manifest->>'rule_set_id')::uuid AND rule.bid_id=s.bid_id AND rule.kind='scoring' WHERE s.id=%s",
                (sample["submission_id"],),
            ).fetchall()
        }
        if {str(cp["rule_id"]) for cp in cp_rows} != expected_rules or not expected_rules:
            raise DomainError(
                "GOVERNANCE_INCOMPLETE_RULE_SCOPE", "不得遗漏任何必要评分项后再训练或测试", 409
            )
        if {p["digest"] for p in packages[sample["id"]]} != {cp["digest"] for cp in cp_rows}:
            raise DomainError(
                "GOVERNANCE_PACKAGE_CHANGED", "训练章节实际字节与数据库冻结范围不一致", 409
            )
        by_parse: dict[str, dict] = {}
        for cp in cp_rows:
            authority.assert_current_api(c, cp["id"])
            chapters.append({"sample_id": sample["id"], "chapter_package_id": str(cp["id"])})
            for binding in cp["bindings"]:
                if UUID(binding["source_id"]) != parse_source_id(UUID(binding["parse_id"])) or UUID(
                    binding["original_source_id"]
                ) != original_source_id(UUID(binding["document_version_id"])):
                    raise DomainError(
                        "GOVERNANCE_PARSE_SOURCE_MISMATCH", "必须使用共享canonical解析来源身份", 409
                    )
                entry = by_parse.setdefault(
                    binding["parse_id"],
                    {
                        **binding,
                        "purpose": "body"
                        if UUID(binding["parse_id"]) == cp["parse_id"]
                        else "rule",
                        "evidence_ids": set(),
                    },
                )
                entry["evidence_ids"].update(binding["evidence_ids"])
        official = fetch(
            c,
            "SELECT p.id,p.document_version_id FROM outcomes o JOIN parse_runs p ON p.document_version_id=o.source_version_id WHERE o.id=%s ORDER BY p.created_at DESC,p.id DESC LIMIT 1",
            (row["outcome_id"],),
        )
        by_parse[str(official["id"])] = {
            "parse_id": str(official["id"]),
            "document_version_id": str(official["document_version_id"]),
            "source_id": str(parse_source_id(official["id"])),
            "original_source_id": str(original_source_id(official["document_version_id"])),
            "purpose": "official",
            "evidence_ids": {str(v) for v in verification["evidence_ids"]},
        }
        for parse in by_parse.values():
            parses.append(
                {
                    "sample_id": sample["id"],
                    **{
                        k: parse[k]
                        for k in (
                            "parse_id",
                            "document_version_id",
                            "source_id",
                            "original_source_id",
                            "purpose",
                        )
                    },
                }
            )
            blocks = c.execute(
                "SELECT id,parse_id,physical_page,ordinal,digest FROM evidence_blocks WHERE bid_id=%s AND parse_id=%s AND id=ANY(%s::uuid[]) ORDER BY id",
                (row["bid_id"], parse["parse_id"], sorted(parse["evidence_ids"])),
            ).fetchall()
            if {str(block["id"]) for block in blocks} != parse["evidence_ids"]:
                raise DomainError(
                    "GOVERNANCE_EXACT_EVIDENCE_MISMATCH",
                    "冻结证据必须精确属于本项目、解析和物理页",
                    409,
                )
            evidence.extend(
                {
                    "sample_id": sample["id"],
                    "evidence_id": str(block["id"]),
                    **clean(
                        {k: block[k] for k in ("parse_id", "physical_page", "ordinal", "digest")}
                    ),
                }
                for block in blocks
            )
            heads = c.execute(
                "SELECT a.ancestor_id,n.document_version_id,n.kind,n.policy_class,n.digest,n.known_at,h.revision,h.status,h.valid_from,h.valid_until,d.bid_id FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN document_versions d ON d.id=n.document_version_id WHERE a.source_id=%s ORDER BY a.ancestor_id",
                (parse["source_id"],),
            ).fetchall()
            visible = (
                row["known_at"]
                if parse["purpose"] == "official"
                else min(row["submitted_at"], min(cp["as_of"] for cp in cp_rows))
            )
            policy = "official_label" if parse["purpose"] == "official" else "prebid_content"
            if not heads:
                raise DomainError(
                    "GOVERNANCE_SOURCE_UNAVAILABLE",
                    "原件/解析完整祖先尚未登记，不能将其假装可用",
                    409,
                )
            for head in heads:
                if (
                    head["bid_id"] != row["bid_id"]
                    or head["kind"] != "document"
                    or head["policy_class"] != policy
                    or head["status"] != "published"
                    or head["known_at"] > visible
                    or head["valid_from"] is None
                    or head["valid_from"] > visible
                    or (head["valid_until"] is not None and head["valid_until"] <= now())
                ):
                    raise DomainError(
                        "GOVERNANCE_SOURCE_NOT_VISIBLE",
                        "来源祖先不是当时可见且当前有效的精确用途资料",
                        409,
                    )
                sources.append(
                    {
                        "sample_id": sample["id"],
                        "source_id": parse["source_id"],
                        **clean(
                            {
                                k: head[k]
                                for k in (
                                    "ancestor_id",
                                    "revision",
                                    "digest",
                                    "policy_class",
                                    "known_at",
                                    "valid_from",
                                    "valid_until",
                                )
                            }
                        ),
                    }
                )
        # All selected judgment call IDs must be persisted successful calls of this sample attempt.
        attempt = next(
            item for item in snapshot["results"] if str(item["sample_id"]) == sample["id"]
        )
        calls = [
            j["call_id"]
            for item in materials["results"][sample["id"]]["items"]
            for j in item["judgments"]
        ]
        actual = c.execute(
            "SELECT id FROM model_calls WHERE id=ANY(%s::uuid[]) AND operation_id=%s AND research_run_id=%s AND state='succeeded' AND response_ref IS NOT NULL",
            (calls, attempt["operation_id"], run["id"]),
        ).fetchall()
        if len(calls) != len(set(calls)) or {str(v["id"]) for v in actual} != set(calls):
            raise DomainError(
                "GOVERNANCE_JUDGMENT_CALL_INVALID",
                "三判断必须来自本冻结研究样本的独立成功账本记录",
                409,
            )
    return {
        "members": members,
        "chapters": chapters,
        "parses": parses,
        "evidence": evidence,
        "sources": sources,
        "metadata_digest": sha(clean(snapshot)),
    }
