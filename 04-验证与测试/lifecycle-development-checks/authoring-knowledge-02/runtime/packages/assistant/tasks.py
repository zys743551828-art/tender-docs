"""Core project Worker hooks. No business identity reads or domain decisions."""

from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb

from packages.assistant.authority import assert_source_guards
from packages.assistant.generation import request_for, validate_answer, validate_generation
from packages.assistant.jobs import job, require_schema
from packages.domain import model_calls
from packages.domain.runtime import DomainError, connection, fetch, sha


@dataclass(frozen=True)
class Outcome:
    job_id: UUID
    input_digest: str
    call_id: UUID
    payload: dict
    provider_result: dict


def validate_message(c, message, row):
    handler = "assistant/1" if row["kind"] == "answer" else "authoring-generate/1"
    step = "assistant-answer" if row["kind"] == "answer" else "authoring-generate"
    if (
        row["scope"] != "project"
        or str(row["bid_id"]) != message["bid_id"]
        or str(row["operation_id"]) != message["operation_id"]
        or message["handler_version"] != handler
        or message["step_key"] != step
        or message["payload_ref"] != str(row["id"])
        or message["input_digest"] != sha({"id": str(row["id"]), "digest": row["input_digest"]})
    ):
        raise DomainError("ASSISTANT_TASK_INVALID", "任务与冻结助手输入身份不符", 409)
    return row


def compute(message, lease):
    with connection(True) as c:
        require_schema(c, worker=True)
        row = validate_message(c, message, job(c, UUID(message["payload_ref"])))
        assert_source_guards(c, row["input_manifest"])
    manifest = row["input_manifest"]
    messages, schema = request_for(manifest)
    result, call = model_calls.generate(
        message,
        lease,
        logical_key="assistant.answer/1" if row["kind"] == "answer" else "authoring.generate/1",
        messages=messages,
        json_schema=schema,
        max_tokens=manifest.get("request", {}).get("max_tokens", 2048),
        purpose="assistant" if row["kind"] == "answer" else "authoring",
    )
    value = (
        validate_answer(manifest, result["data"])
        if row["kind"] == "answer"
        else validate_generation(manifest, result["data"])
    )
    return Outcome(row["id"], row["input_digest"], call, value, result)


def publish(c, message, outcome):
    row = validate_message(c, message, job(c, outcome.job_id))
    if row["input_digest"] != outcome.input_digest:
        raise DomainError("ASSISTANT_TASK_INVALID", "助手输入摘要已变化", 409)
    call = fetch(
        c,
        "SELECT * FROM model_calls WHERE id=%s AND operation_id=%s",
        (outcome.call_id, row["operation_id"]),
    )
    if call["state"] != "succeeded" or call["response_ref"] is None:
        raise DomainError("ASSISTANT_CALL_NOT_SUCCEEDED", "模型结果尚未可靠入账，未发布", 409)
    blob = fetch(c, "SELECT digest FROM blobs WHERE id=%s", (call["response_ref"],))
    if sha(outcome.provider_result) != blob["digest"]:
        raise DomainError("ASSISTANT_OUTPUT_CHANGED", "助手候选不对应已入账模型字节", 409)
    value = (
        validate_answer(row["input_manifest"], outcome.provider_result["data"])
        if row["kind"] == "answer"
        else validate_generation(row["input_manifest"], outcome.provider_result["data"])
    )
    if value != outcome.payload:
        raise DomainError("ASSISTANT_OUTPUT_CHANGED", "助手候选与实际模型结果不一致", 409)
    existing = c.execute(
        "SELECT digest,project_call_id FROM assistant_outputs WHERE job_id=%s", (row["id"],)
    ).fetchone()
    if existing:
        if existing["digest"] != sha(value) or existing["project_call_id"] != outcome.call_id:
            raise DomainError("ASSISTANT_OUTPUT_CHANGED", "重放结果与既有记录不一致", 409)
    else:
        c.execute(
            "INSERT INTO assistant_outputs(job_id,project_call_id,payload,digest) VALUES(%s,%s,%s,%s)",
            (row["id"], outcome.call_id, Jsonb(value), sha(value)),
        )
    return {
        "job_id": str(row["id"]),
        "result_ref": "/api/v1/assistant/jobs/" + str(row["id"]),
        "state": "candidate_ready",
        "domain_mutated": False,
    }
