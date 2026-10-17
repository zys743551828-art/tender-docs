"""Server-built Action cards. One global receipt across members and entry points."""

from datetime import timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.assistant import context, jobs
from packages.domain.runtime import DomainError, audit, clean, etag, fetch, sha, uid
from packages.lifecycle.service import assert_work_writable


def prepare(c, source_job, proposal):
    manifest = source_job["input_manifest"]
    target = UUID(proposal["target_bid_id"])
    if str(target) not in manifest["context"]["target_bid_ids"]:
        raise DomainError("ASSISTANT_TARGET_REQUIRED", "未明确选择要写入的项目", 422)
    assert_work_writable(c, target)
    heads = context.project_memory(c, target)["heads"]
    tool = proposal["tool"]
    requested = proposal["arguments"]
    documents = {
        s["selection"]["id"]
        for s in manifest["context"]["sources"]
        if s["selection"]["kind"] == "document" and s["bid_id"] == str(target)
    }
    if tool == "review.request":
        if not heads["current_body_id"] or not heads["current_material_id"]:
            raise DomainError("REVIEW_MATERIALS_REQUIRED", "目标项目尚无实际正文和资料批次", 409)
        arguments = {
            "body_version_id": heads["current_body_id"],
            "material_revision": heads["material_revision"],
            "trigger": "manual",
        }
        preview = {
            "kind": "request_review",
            "body_version_id": heads["current_body_id"],
            "material_revision": heads["material_revision"],
            "effect": "明确请求统一评审；尚未运行或通过",
        }
    elif tool == "authoring.generate":
        section = next(
            (
                s
                for s in manifest["context"]["sources"]
                if s["selection"]["kind"] == "section"
                and s["selection"]["id"] == requested["section_id"]
                and s["bid_id"] == str(target)
            ),
            None,
        )
        if section is None:
            raise DomainError("ASSISTANT_TARGET_REQUIRED", "编制目标必须是明确选择的章节", 422)
        arguments = {
            "section_id": requested["section_id"],
            "base_section_revision": section["provenance"]["revision"],
            "instruction": requested["instruction"],
            "source_version_ids": sorted(documents),
        }
        preview = {
            "kind": "generate_proposal",
            "section_id": requested["section_id"],
            "base_section_revision": arguments["base_section_revision"],
            "instruction": requested["instruction"],
            "effect": "排受控模型任务，只产差异候选，不改正文",
        }
    elif tool == "knowledge.candidate":
        if not set(requested["source_version_ids"]) <= documents:
            raise DomainError(
                "ASSISTANT_CITATION_INVALID", "知识候选只可选择本次明确提供的目标项目原件", 422
            )
        arguments = requested
        preview = {
            "kind": "knowledge_candidate",
            "title": requested["title"],
            "content": requested["content"],
            "scope": requested["scope"],
            "source_version_ids": requested["source_version_ids"],
            "effect": "仅建立待完善知识，不发布、不授事实资格",
        }
    else:
        raise DomainError("ASSISTANT_TOOL_FORBIDDEN", "工具不在服务端支持白名单", 422)
    identity = uid()
    payload = clean(
        {
            "kind": "assistant_tool",
            "tool": tool,
            "arguments": arguments,
            "source_job_id": source_job["id"],
            "context_digest": sha(manifest["context"]),
            "preview": preview,
            "purpose": "assistant",
            "formal_scoring_input": False,
        }
    )
    c.execute(
        "INSERT INTO proposed_actions(id,bid_id,base_revision,request_digest,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identity,
            target,
            heads["revision"],
            sha(payload),
            context.at(c) + timedelta(minutes=30),
            Jsonb(payload),
        ),
    )
    link(c, identity, source_job["id"], tool)
    return identity


def link(c, action, job_id, tool):
    c.execute(
        "INSERT INTO assistant_action_links(action_id,job_id,tool) VALUES(%s,%s,%s)",
        (action, job_id, tool),
    )


def assert_linked_generation_context(c, bid, identity):
    """Native A2 apply shares the same guard; absence of this optional schema is inert."""
    if (
        c.execute("SELECT to_regclass('design.assistant_action_links') AS id").fetchone()["id"]
        is None
    ):
        return
    linked = c.execute(
        """SELECT j.* FROM assistant_action_links l JOIN assistant_jobs j ON j.id=l.job_id
        WHERE l.action_id=%s AND l.tool='authoring.apply_patch'""",
        (identity,),
    ).fetchone()
    if linked is None:
        return
    if (
        linked["bid_id"] != UUID(str(bid))
        or linked["kind"] != "authoring"
        or sha(linked["input_manifest"]) != linked["input_digest"]
    ):
        raise DomainError("ACTION_IDENTITY_INVALID", "生成卡与已冻结助手任务身份不一致", 409)
    context.assert_generation_context(c, bid, linked["input_manifest"]["project_memory"])


def records(c, identity, *, lock=False):
    query = (
        "SELECT * FROM assistant_action_links WHERE action_id=%s FOR UPDATE"
        if lock
        else "SELECT * FROM assistant_action_links WHERE action_id=%s"
    )
    row = fetch(
        c,
        query,
        (identity,),
    )
    proposed = fetch(c, "SELECT * FROM proposed_actions WHERE id=%s", (identity,))
    if sha(proposed["payload"]) != proposed["request_digest"]:
        raise DomainError("ACTION_IDENTITY_INVALID", "操作卡内容身份不一致", 409)
    expected = "authoring_patch" if row["tool"] == "authoring.apply_patch" else "assistant_tool"
    if proposed["payload"].get("kind") != expected or (
        expected == "assistant_tool" and proposed["payload"].get("tool") != row["tool"]
    ):
        raise DomainError("ACTION_IDENTITY_INVALID", "操作卡类型与受控工具不一致", 409)
    return row, proposed


def view(c, identity):
    row, proposed = records(c, identity)
    consumption = c.execute(
        "SELECT consumed_by,result,consumed_at FROM action_consumptions WHERE action_id=%s",
        (identity,),
    ).fetchone()
    state = row["state"]
    if consumption:
        state = consumption["result"].get("assistant_resolution", {}).get("state", "executed")
    payload = proposed["payload"]
    if row["tool"] == "authoring.apply_patch":
        from packages.authoring.patches import replace_node

        original = fetch(
            c,
            "SELECT content FROM section_revisions WHERE section_id=%s AND revision=%s",
            (UUID(payload["section_id"]), payload["base_section_revision"]),
        )["content"]
        after = replace_node(
            original, payload["node_id"], payload["before_digest"], payload["replacement"]
        )
        after["attrs"]["source_refs"] = payload["source_refs"]
        preview = {
            "kind": "authoring_patch",
            "section_id": payload["section_id"],
            "base_section_revision": payload["base_section_revision"],
            "before": original,
            "after": after,
            "effect": "明确应用会追加新修订；不表示问题已解决",
        }
    else:
        preview = payload["preview"]
    return clean(
        {
            "action_id": identity,
            "job_id": row["job_id"],
            "target_bid_id": proposed["bid_id"],
            "tool": row["tool"],
            "state": state,
            "revision": row["revision"],
            "request_digest": proposed["request_digest"],
            "base_revision": proposed["base_revision"],
            "expires_at": proposed["expires_at"],
            "preview": preview,
            "result": consumption["result"] if consumption else None,
            "resolved_by": consumption["consumed_by"] if consumption else row["resolved_by"],
            "error_code": row["error_code"],
            "formal_scoring_input": False,
        }
    )


def tag(value):
    return etag("assistant-action", value["action_id"], sha(value))


def lock(c, identity):
    row, proposed = records(c, identity)
    source = jobs.job(c, row["job_id"])
    bids = {proposed["bid_id"]}
    if source["kind"] == "answer":
        bids.update(context.turn_bids(c, source["input_manifest"]["request"], source["bid_id"]))
    context.lock_scope(c, bids)
    return records(c, identity, lock=True), source


def resolve(c, identity, actor, decision, command_id, condition):
    (row, proposed), source = lock(c, identity)
    prior = c.execute(
        "SELECT result FROM action_consumptions WHERE action_id=%s", (identity,)
    ).fetchone()
    if prior:
        return view(c, identity), prior["result"].get("assistant_resolution", {}).get(
            "http_status", 200
        )
    current = view(c, identity)
    if not condition:
        raise DomainError("PRECONDITION_REQUIRED", "请先读取明确操作卡再提交", 428)
    if condition != tag(current):
        raise DomainError("REVISION_CONFLICT", "操作卡已变化，请保留输入并核对", 412)
    if row["state"] != "pending":
        return current, 409

    def resolved(state, *, domain=None, error=None, status=200):
        receipt = clean(
            {
                "assistant_resolution": {
                    "action_id": identity,
                    "state": state,
                    "tool": row["tool"],
                    "target_bid_id": proposed["bid_id"],
                    "domain_result": domain,
                    "error_code": error,
                    "http_status": status,
                    "business_effect_executed": state == "executed",
                }
            }
        )
        # Authoring apply has already appended the same immutable global receipt itself.
        if not c.execute(
            "SELECT 1 FROM action_consumptions WHERE action_id=%s", (identity,)
        ).fetchone():
            c.execute(
                "INSERT INTO action_consumptions(action_id,command_id,consumed_by,result) VALUES(%s,%s,%s,%s)",
                (identity, command_id, actor, Jsonb(receipt)),
            )
        c.execute(
            "UPDATE assistant_action_links SET state=%s,revision=revision+1,resolved_by=%s,resolved_at=clock_timestamp(),error_code=%s WHERE action_id=%s",
            (state, actor, error, identity),
        )
        audit(
            c,
            actor,
            proposed["bid_id"],
            "assistant.action_resolved",
            {"action_id": identity, "state": state, "tool": row["tool"], "error_code": error},
            command_id,
        )
        return view(c, identity), status

    if decision == "reject":
        return resolved("rejected")
    if proposed["expires_at"] <= context.at(c):
        return resolved("expired", error="ACTION_EXPIRED", status=409)
    payload = proposed["payload"]
    try:
        with c.transaction():
            assert_work_writable(c, proposed["bid_id"])
            if source["kind"] == "answer":
                context.revalidate_turn(
                    c,
                    source["input_manifest"]["context"],
                    source["input_manifest"]["request"],
                    source["bid_id"],
                )
                if (
                    payload.get("kind") != "assistant_tool"
                    or payload.get("source_job_id") != str(source["id"])
                    or payload.get("context_digest") != sha(source["input_manifest"]["context"])
                    or payload.get("tool") != row["tool"]
                ):
                    raise DomainError("ACTION_IDENTITY_INVALID", "工具与已验证提案身份不符", 409)
            else:
                context.assert_generation_context(
                    c, proposed["bid_id"], source["input_manifest"]["project_memory"]
                )
            if row["tool"] == "review.request":
                from packages.domain.reviews import queue_intent

                domain = queue_intent(c, proposed["bid_id"], command_id, payload["arguments"])
            elif row["tool"] == "authoring.generate":
                domain = jobs.queue_generation(
                    c, proposed["bid_id"], actor, payload["arguments"], command_id, identity
                )
            elif row["tool"] == "knowledge.candidate":
                from packages.knowledge.repository import create_candidate

                domain = create_candidate(c, actor, payload["arguments"])
            elif row["tool"] == "authoring.apply_patch":
                from packages.authoring.patches import apply_proposal
                from packages.domain.runtime import etag as draft_tag

                domain = apply_proposal(
                    c,
                    proposed["bid_id"],
                    identity,
                    actor,
                    command_id,
                    draft_tag("draft", UUID(payload["draft_id"]), proposed["base_revision"]),
                )
            else:
                raise DomainError("ASSISTANT_TOOL_FORBIDDEN", "未支持的工具不能执行", 422)
    except DomainError as error:
        stale = error.status in (409, 412) or error.code in (
            "KNOWLEDGE_SOURCE_INVALID",
            "AUTHORING_SOURCE_NOT_READY",
        )
        return resolved(
            "stale" if stale else "failed", error=error.code, status=409 if stale else 422
        )
    return resolved("executed", domain=domain)
