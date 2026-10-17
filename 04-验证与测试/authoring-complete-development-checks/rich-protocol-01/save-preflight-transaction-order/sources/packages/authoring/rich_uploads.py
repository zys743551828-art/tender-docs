"""Bounded image upload coordinator: short qualification, closed DB I/O, final CAS.

Prepared/unfinished/completed audit events preserve recoverability without an
extra parallel upload database. There is no automatic background retry or model.
"""

from __future__ import annotations

import base64
import binascii

from botocore.exceptions import BotoCoreError, ClientError

from packages.authoring import rich_assets, rich_images
from packages.authoring.content import validate_content
from packages.domain import storage
from packages.domain.runtime import DomainError, audit, connection, sha
from packages.domain.work_access import begin
from packages.lifecycle.service import assert_work_writable

KIND = "upload-authoring-image"


def decode(body):
    if not isinstance(body, dict) or set(body) != {
        "title",
        "reason",
        "media_type",
        "content_base64",
        "source_refs",
    }:
        raise DomainError("INVALID_AUTHORING_IMAGE_UPLOAD", "图片上传字段不符合协议", 422)
    for name, maximum in [("title", 200), ("reason", 1000)]:
        value = body[name]
        if (
            not isinstance(value, str)
            or not 1 <= len(value.strip()) <= maximum
            or any(ord(c) < 32 for c in value)
        ):
            raise DomainError("INVALID_AUTHORING_IMAGE_UPLOAD", "请输入图片名称及登记原因", 422)
    encoded = body["content_base64"]
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 4 * (
        (rich_images.MAX_SOURCE_BYTES + 2) // 3
    ):
        raise DomainError("AUTHORING_IMAGE_SIZE_LIMIT", "图片原件超过8MiB", 413)
    try:
        source = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise DomainError("INVALID_AUTHORING_IMAGE_UPLOAD", "图片字节编码无效", 422) from None
    if not 0 < len(source) <= rich_images.MAX_SOURCE_BYTES or body["media_type"] not in {
        "image/png",
        "image/jpeg",
    }:
        raise DomainError("INVALID_AUTHORING_IMAGE_UPLOAD", "仅支持不超过8MiB的PNG/JPEG", 422)
    validate_content(
        {
            "type": "doc",
            "attrs": {
                "title": "上传图片",
                "ordinal": 0,
                "rule_ids": [],
                "source_refs": body["source_refs"],
            },
            "content": [],
        }
    )
    return source


def upload(bid, actor, key, body, *, final_auth):
    source = decode(body)
    # Use the canonical command parser, then explicitly discard a new provisional
    # command. No incomplete command remains committed across external I/O.
    with connection() as c:
        command = begin(c, actor, KIND, bid, key, body, None, bid=bid)
        if command.replay:
            return command.replay, True
        rich_assets.source_guard(c, bid, body["source_refs"])
        c.rollback()
    upload_id = sha({"bid": str(bid), "actor": str(actor), "key": key})
    request_digest = sha(body)
    with connection() as c:
        assert_work_writable(c, bid)
        rich_assets.source_guard(c, bid, body["source_refs"])
        audit(
            c,
            actor,
            bid,
            "authoring.image_upload_prepared",
            {
                "upload_id": upload_id,
                "request_digest": request_digest,
                "input_digest": sha(source),
                "state": "prepared",
            },
        )
    # No connection or graph/project lock survives this point.
    prefix = f"authoring-images/{bid}/{upload_id}/"
    objects = []
    try:
        normalized = rich_images.normalize(source, body["media_type"])
        for kind, data, media in [
            ("original", source, body["media_type"]),
            ("normalized", normalized["bytes"], "image/png"),
        ]:
            digest = sha(data)
            locator = prefix + digest + "." + kind
            storage.put(locator, data, media)
            if storage.get(locator) != data:
                raise DomainError("AUTHORING_IMAGE_OBJECT_CHANGED", "图片对象回读与写入不一致", 503)
            objects.append(
                {
                    "digest": digest,
                    "byte_size": len(data),
                    "object_key": locator,
                    "media_type": media,
                }
            )
        if final_auth() != actor:
            raise DomainError("AUTHORING_IMAGE_ACTOR_CHANGED", "上传发起身份已变化", 403)
        with connection() as c:
            command = begin(c, actor, KIND, bid, key, body, None, bid=bid)
            if command.replay:
                return command.replay, True
            value = rich_assets.insert(
                c,
                bid,
                actor,
                body["title"],
                body["reason"],
                body["source_refs"],
                normalized["proof"],
                objects[0],
                objects[1],
            )
            value["upload_id"] = upload_id
            audit(
                c,
                actor,
                bid,
                "authoring.image_upload_completed",
                {
                    "upload_id": upload_id,
                    "request_digest": request_digest,
                    "asset_id": value["asset_id"],
                    "asset_version_id": value["id"],
                    "state": "completed",
                },
                command.id,
            )
            return command.finish(
                value,
                201,
                {
                    "ETag": value["etag"],
                    "Location": f"/api/v1/bids/{bid}/authoring-images/{value['asset_id']}",
                },
            ), False
    except (DomainError, OSError, ClientError, BotoCoreError) as error:
        code = error.code if isinstance(error, DomainError) else "OBJECT_STORE_UNAVAILABLE"
        state = (
            "rejected" if isinstance(error, DomainError) and error.status < 500 else "unconfirmed"
        )
        with connection() as c:
            audit(
                c,
                actor,
                bid,
                "authoring.image_upload_" + state,
                {
                    "upload_id": upload_id,
                    "request_digest": request_digest,
                    "error_code": code,
                    "state": state,
                },
            )
        if isinstance(error, DomainError):
            raise DomainError(
                error.code,
                error.message,
                error.status,
                error.details | {"upload_id": upload_id},
                retryable=error.retryable,
            ) from None
        raise DomainError(
            "AUTHORING_IMAGE_UPLOAD_UNCONFIRMED",
            "对象上传未确认，请保留原图并用同一内容和幂等键明确重试",
            503,
            {"upload_id": upload_id},
            retryable=True,
        ) from None
