"""配置、固定契约、能力连接与命令事务。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
for _line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in _line and not _line.lstrip().startswith("#"):
        _key, _, _value = _line.partition("=")
        _parts = shlex.split(_value, comments=True)
        os.environ.setdefault(_key.strip(), _parts[0] if _parts else "")


class DomainError(Exception):
    def __init__(self, code, message, status=409, details=None, retryable=False):
        self.code, self.message, self.status = code, message, status
        self.details, self.retryable = details or {}, retryable


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"缺少配置 {name}")
    return value


def now():
    return datetime.now(timezone.utc)


def uid():
    return uuid4()


def clean(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def sha(value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(
            clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )
    return hashlib.sha256(raw).hexdigest()


def secret_hash(value, name="SESSION_SECRET"):
    return hmac.new(required(name).encode(), value.encode(), hashlib.sha256).hexdigest()


def validate(name, body):
    schema = json.loads((ROOT / "packages/contracts/schemas/protocol.schema.json").read_text())
    errors = list(
        Draft202012Validator(
            {"$ref": "#/$defs/" + name, "$defs": schema["$defs"]}, format_checker=FormatChecker()
        ).iter_errors(body)
    )
    if errors:
        raise DomainError(
            "INVALID_REQUEST",
            "请检查输入字段",
            422,
            {
                "fields": [
                    {
                        "path": "/".join(map(str, e.path)),
                        "message": "字段不符合协议要求",
                        "constraint": e.validator,
                    }
                    for e in errors[:20]
                ]
            },
        )
    return body


@contextmanager
def connection(worker=False):
    key = "WORKER_DATABASE_DSN" if worker else "API_DATABASE_DSN"
    with psycopg.connect(required(key), row_factory=dict_row) as conn:
        conn.execute("SET LOCAL search_path=design,public")
        yield conn


def assert_role(worker=False):
    with connection(worker) as c:
        r = c.execute(
            "SELECT current_user,rolsuper,pg_has_role(current_user,%s,'MEMBER') AS capability FROM pg_roles WHERE rolname=current_user",
            ("tender_worker" if worker else "tender_api",),
        ).fetchone()
        owns = c.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='design'::regnamespace AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owns"
        ).fetchone()["owns"]
        if r["rolsuper"] or owns or not r["capability"]:
            raise RuntimeError("运行账号必须为非owner的限定能力LOGIN")


def etag(kind, object_id, revision):
    return f'"{kind}:{object_id}:{revision}"'


def match(value, kind, object_id, revision):
    if not value:
        raise DomainError("PRECONDITION_REQUIRED", "请刷新对象后重试", 428)
    if value != etag(kind, object_id, revision):
        raise DomainError(
            "REVISION_CONFLICT", "对象已更新，请核对最新内容", 412, {"current_revision": revision}
        )


def fetch(c, sql, params=()):
    r = c.execute(sql, params).fetchone()
    if r is None:
        raise DomainError("NOT_FOUND", "未找到指定对象", 404)
    return r


def audit(c, actor, bid, event, payload, command=None):
    c.execute(
        "INSERT INTO audit_entries(id,actor_id,bid_id,command_id,event_type,payload) VALUES(%s,%s,%s,%s,%s,%s)",
        (uid(), actor, bid, command, event, Jsonb(clean(payload))),
    )
    if bid:
        head = c.execute(
            "UPDATE stream_heads SET last_seq=last_seq+1 WHERE bid_id=%s RETURNING id,last_seq",
            (bid,),
        ).fetchone()
        if head:
            c.execute(
                "INSERT INTO stream_events(stream_id,seq,event_id,event_type,payload) VALUES(%s,%s,%s,%s,%s)",
                (head["id"], head["last_seq"], uid(), event, Jsonb(clean(payload))),
            )


class Command:
    def __init__(self, c, actor, kind, target, key, request, condition=None):
        if not key or len(key) > 128:
            raise DomainError("IDEMPOTENCY_REQUIRED", "请提供有效的操作幂等键", 428)
        self.c, self.id, self.replay = c, uid(), None
        digest = sha({"body": request, "if_match": condition})
        inserted = c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (self.id, actor, kind, target, key, digest),
        ).rowcount
        if not inserted:
            old = fetch(
                c,
                "SELECT * FROM commands WHERE actor_id=%s AND command_kind=%s AND target_id=%s AND idempotency_key=%s FOR UPDATE",
                (actor, kind, target, key),
            )
            if old["request_digest"] != digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "同一幂等键对应不同输入", 409)
            self.id, self.replay = old["id"], old["response"]
            if self.replay is None:
                raise DomainError("COMMAND_INCOMPLETE", "操作尚未完成", 409)

    def finish(self, body, status=200, headers=None):
        result = {"body": clean(body), "status": status, "headers": headers or {}}
        self.c.execute("UPDATE commands SET response=%s WHERE id=%s", (Jsonb(result), self.id))
        return result


def enqueue(c, bid, command, kind, payload):
    operation = uid()
    digest = sha(payload)
    c.execute(
        "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES(%s,%s,%s,%s,%s,'queued','document/1')",
        (operation, bid, command, kind, digest),
    )
    c.execute(
        "INSERT INTO operation_steps(operation_id,step_key,input_digest,handler_version,required,depends_on,dispatch_generation) VALUES(%s,%s,%s,%s,true,%s,1)",
        (operation, kind, digest, "document/1", []),
    )
    message = {
        "schema_version": 1,
        "event_type": "step.requested",
        "event_id": str(uid()),
        "operation_id": str(operation),
        "bid_id": str(bid),
        "step_key": kind,
        "input_digest": digest,
        "generation": 1,
        "handler_version": "document/1",
        "payload_ref": str(payload["id"]),
        "created_at": now().isoformat(),
    }
    c.execute(
        "INSERT INTO outbox(id,operation_id,topic,deduplication_key,payload) VALUES(%s,%s,%s,%s,%s)",
        (
            message["event_id"],
            operation,
            "tender.task.document.v1",
            f"{operation}:{kind}:1",
            Jsonb(message),
        ),
    )
    return {
        "id": str(operation),
        "bid_id": str(bid),
        "kind": kind,
        "state": "queued",
        "input_digest": digest,
        "handler_version": "document/1",
        "result_ref": None,
    }


def accepted(operation, resource_id, kind):
    return {
        "resource_id": str(resource_id),
        "resource_kind": kind,
        "operation": operation,
        "disposition": "created",
        "operation_url": "/api/v1/operations/" + operation["id"],
    }


def encode_cursor(row):
    import base64

    raw = json.dumps(
        {"scope": "bids/1", "at": row["created_at"].isoformat(), "id": str(row["id"])},
        separators=(",", ":"),
    ).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token + "." + secret_hash(token)


def decode_cursor(cursor):
    import base64

    try:
        token, signature = cursor.split(".")
        if not hmac.compare_digest(secret_hash(token), signature):
            raise ValueError("signature")
        obj = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if obj["scope"] != "bids/1":
            raise ValueError("scope")
        return datetime.fromisoformat(obj["at"]), UUID(obj["id"])
    except (ValueError, KeyError, TypeError):
        raise DomainError("INVALID_CURSOR", "分页游标无效，请重新加载列表", 422) from None
