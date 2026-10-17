"""Exact lifecycle read/write projections; lifecycle source bytes remain reviewed inputs."""


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def extend_lifecycle(actual):
    schemas = actual["components"]["schemas"]
    uuid = {"type": "string", "format": "uuid"}
    optional_uuid = {"type": ["string", "null"], "format": "uuid"}
    date = {"type": ["string", "null"], "format": "date-time"}
    positive = {"type": "integer", "minimum": 1}
    phase = {"enum": ["preparation", "working", "submitted", "closed", "terminated"]}
    reason = {"type": "string", "minLength": 1, "maxLength": 4000}
    actions = {"enum": ["start", "end", "terminate", "archive", "reopen"]}
    batch = obj({"id": uuid, "bid_id": uuid, "state": {"type": "string"}, "revision": positive})
    closure = obj(
        {
            "id": uuid,
            "bid_id": uuid,
            "batch_id": uuid,
            "actor_id": uuid,
            "command_id": uuid,
            "reason": reason,
            "created_at": {"type": "string", "format": "date-time"},
        }
    )
    schemas["LifecycleChangeRequest"] = obj({"action": actions, "reason": reason})
    schemas["LifecycleBatchCloseRequest"] = obj({"reason": reason})
    schemas["ProjectLifecycleView"] = obj(
        {
            "bid_id": uuid,
            "phase": phase,
            "archived_at": date,
            "revision": positive,
            "allowed_actions": {"type": "array", "items": actions},
            "archive_blockers": {
                "type": "array",
                "items": {
                    "enum": ["OPERATIONS_IN_FLIGHT", "UPLOADS_UNFINISHED", "BATCHES_UNFINISHED"]
                },
            },
            "active_operations": {
                "type": "array",
                "items": obj(
                    {
                        "id": uuid,
                        "kind": {"type": "string"},
                        "state": {"enum": ["queued", "running", "cancel_requested"]},
                    }
                ),
            },
            "unfinished_uploads": {
                "type": "array",
                "items": obj(
                    {
                        "id": uuid,
                        "batch_id": uuid,
                        "state": {"type": "string"},
                        "verified_version_id": optional_uuid,
                        "aborted_at": date,
                    }
                ),
            },
            "unfinished_batches": {
                "type": "array",
                "items": obj({"id": uuid, "state": {"type": "string"}, "revision": positive}),
            },
            "batch_closure_available": {"type": "boolean"},
        }
    )
    schemas["BatchClosureStateView"] = obj(
        {
            "batch": batch,
            "uploads": {
                "type": "array",
                "items": obj(
                    {
                        "id": uuid,
                        "state": {"type": "string"},
                        "verified_version_id": optional_uuid,
                        "revision": {"type": ["integer", "null"], "minimum": 1},
                        "aborted_at": date,
                        "committed_binding_id": optional_uuid,
                    }
                ),
            },
            "closure": {"anyOf": [closure, {"type": "null"}]},
            "available": {"type": "boolean"},
        }
    )
    specs = [
        (
            "/api/v1/bids/{bid_id}/lifecycle",
            "get",
            "getProjectLifecycle",
            None,
            "ProjectLifecycleView",
        ),
        (
            "/api/v1/bids/{bid_id}/lifecycle",
            "post",
            "changeProjectLifecycle",
            "LifecycleChangeRequest",
            "ProjectLifecycleView",
        ),
        (
            "/api/v1/bids/{bid_id}/upload-batches/{batch_id}/closure-state",
            "get",
            "getUploadBatchClosureState",
            None,
            "BatchClosureStateView",
        ),
        (
            "/api/v1/bids/{bid_id}/upload-batches/{batch_id}/close",
            "post",
            "closeUploadBatchKeepStaging",
            "LifecycleBatchCloseRequest",
            "BatchClosureStateView",
        ),
    ]
    for path, method, identifier, request, response in specs:
        operation = actual["paths"].get(path, {}).get(method)
        if operation is None:
            continue
        operation.update(operationId=identifier, security=[{"SessionCookie": []}])
        operation["responses"] = {
            "200": {
                "description": "实际生命周期与未结束意图快照",
                "headers": {"ETag": {"schema": {"type": "string"}}},
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + response}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + request}}
                },
            }
            operation.setdefault("parameters", []).extend(
                {"in": "header", "name": name, "required": True, "schema": {"type": "string"}}
                for name in ("Origin", "X-CSRF-Token", "Idempotency-Key", "If-Match")
            )
