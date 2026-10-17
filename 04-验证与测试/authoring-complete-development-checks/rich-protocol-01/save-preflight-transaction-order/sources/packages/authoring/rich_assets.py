"""API-controlled image identity and Worker-readable final qualification.

Network I/O belongs to an outer coordinator after DB close; this module never
uploads, downloads, normalizes, or signs objects within a transaction.
"""

from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from packages.authoring import rich_images
from packages.authoring.repository import assert_sources
from packages.domain.runtime import (
    DomainError,
    audit,
    clean,
    etag,
    fetch,
    require_read_committed,
    sha,
    uid,
)
from packages.lifecycle.service import assert_work_writable, lock


def require_transaction(c):
    if c.autocommit:
        raise DomainError(
            "AUTHORING_IMAGE_REQUIRES_TRANSACTION", "图片资格核验必须保留同一事务的项目锁", 409
        )
    require_read_committed(c)


def require_available(c, *, worker=False):
    for name in ("authoring_image_assets", "authoring_image_versions"):
        exists = c.execute("SELECT to_regclass(%s) AS item", ("design." + name,)).fetchone()["item"]
        required = ("SELECT",) if worker else ("SELECT", "INSERT")
        if exists is None or not all(
            c.execute(
                "SELECT has_table_privilege(current_user,%s,%s) AS ok",
                ("design." + name, capability),
            ).fetchone()["ok"]
            for capability in required
        ):
            raise DomainError(
                "AUTHORING_RICH_NOT_CONFIGURED", "尚未配置已审表图资产能力，请保留当前正文", 503
            )


def source_guard(c, bid, refs):
    require_transaction(c)
    assert_sources(
        c,
        bid,
        {
            "type": "doc",
            "attrs": {"title": "图片来源守卫", "ordinal": 0, "source_refs": refs, "rule_ids": []},
            "content": [],
        },
    )


def version(c, bid, asset_id, version_id=None):
    row = fetch(
        c, "SELECT * FROM authoring_image_assets WHERE bid_id=%s AND id=%s", (bid, asset_id)
    )
    value = fetch(
        c,
        "SELECT * FROM authoring_image_versions WHERE bid_id=%s AND asset_id=%s AND id=%s",
        (bid, asset_id, version_id or row["head_version_id"]),
    )
    result = clean(value) | {
        "title": row["title"],
        "head_revision": row["head_revision"],
        "head_version_id": str(row["head_version_id"]),
        "is_current": value["id"] == row["head_version_id"],
    }
    result["etag"] = etag("authoring-image", row["id"], row["head_revision"])
    return result


def blob(c, value):
    """Only a coordinator-produced digest-checked object descriptor is accepted."""
    required = {"digest", "byte_size", "object_key", "media_type"}
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value["byte_size"]) is not int
        or value["byte_size"] < 1
        or not isinstance(value["object_key"], str)
        or not value["object_key"].startswith("authoring-images/")
    ):
        raise DomainError("AUTHORING_IMAGE_OBJECT_INVALID", "图片对象身份不完整", 409)
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(digest) DO NOTHING",
        (uid(), value["digest"], value["object_key"], value["byte_size"], value["media_type"]),
    )
    actual = fetch(c, "SELECT * FROM blobs WHERE digest=%s", (value["digest"],))
    if actual["byte_size"] != value["byte_size"] or actual["media_type"] != value["media_type"]:
        raise DomainError("AUTHORING_IMAGE_OBJECT_INVALID", "图片对象与已登记字节冲突", 409)
    return actual


def insert(c, bid, actor, title, reason, source_refs, proof, original_object, normalized_object):
    require_transaction(c)
    require_available(c)
    assert_work_writable(c, bid)
    if (
        not isinstance(title, str)
        or not 1 <= len(title.strip()) <= 200
        or any(ord(ch) < 32 for ch in title)
        or not isinstance(reason, str)
        or not 1 <= len(reason.strip()) <= 1000
    ):
        raise DomainError("AUTHORING_IMAGE_METADATA_INVALID", "请输入图片名称和本次登记原因", 422)
    source_guard(c, bid, source_refs)
    if (
        proof.get("protocol") != rich_images.PROTOCOL
        or original_object["digest"] != proof.get("input_digest")
        or normalized_object["digest"] != proof.get("output_digest")
        or original_object["media_type"] != proof.get("input_media_type")
        or normalized_object["media_type"] != "image/png"
        or normalized_object["byte_size"] != proof.get("output_byte_size")
    ):
        raise DomainError("AUTHORING_IMAGE_OBJECT_INVALID", "原图与规范图证明不一致", 409)
    original, normalized = blob(c, original_object), blob(c, normalized_object)
    asset, identity = uid(), uid()
    c.execute(
        "INSERT INTO authoring_image_assets(id,bid_id,title,head_revision,head_version_id,created_by) VALUES(%s,%s,%s,1,%s,%s)",
        (asset, bid, title, identity, actor),
    )
    payload = {
        "bid_id": str(bid),
        "asset_id": str(asset),
        "revision": 1,
        "original_blob_id": str(original["id"]),
        "normalized_blob_id": str(normalized["id"]),
        "normalization_proof": proof,
        "source_refs": source_refs,
        "state": "active",
        "reason": reason,
    }
    c.execute(
        """INSERT INTO authoring_image_versions(id,bid_id,asset_id,revision,state,reason,
        original_blob_id,normalized_blob_id,original_media_type,width_px,height_px,normalization_proof,source_refs,digest,created_by)
        VALUES(%s,%s,%s,1,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            identity,
            bid,
            asset,
            reason,
            original["id"],
            normalized["id"],
            proof["input_media_type"],
            proof["width_px"],
            proof["height_px"],
            Jsonb(proof),
            Jsonb(source_refs),
            sha(payload),
            actor,
        ),
    )
    audit(
        c,
        actor,
        bid,
        "authoring.image_registered",
        {"asset_id": str(asset), "version_id": str(identity), "normalization_digest": sha(proof)},
    )
    return version(c, bid, asset)


def withdraw(c, bid, asset_id, actor, expected, reason):
    require_transaction(c)
    require_available(c)
    assert_work_writable(c, bid)
    current = version(c, bid, asset_id)
    if current["etag"] != expected:
        raise DomainError("AUTHORING_IMAGE_REVISION_CONFLICT", "图片版本已变化，请刷新后重试", 412)
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise DomainError("AUTHORING_IMAGE_REASON_REQUIRED", "请输入停用原因", 422)
    if current["state"] == "withdrawn":
        raise DomainError("AUTHORING_IMAGE_ALREADY_WITHDRAWN", "该图片已经停用", 409)
    identity, revision = uid(), current["revision"] + 1
    digest = sha(
        {
            "previous_id": current["id"],
            "previous_digest": current["digest"],
            "state": "withdrawn",
            "reason": reason,
        }
    )
    c.execute(
        """INSERT INTO authoring_image_versions(id,bid_id,asset_id,revision,previous_revision,previous_version_id,state,reason,
        original_blob_id,normalized_blob_id,original_media_type,width_px,height_px,normalization_proof,source_refs,digest,created_by)
        VALUES(%s,%s,%s,%s,%s,%s,'withdrawn',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            identity,
            bid,
            asset_id,
            revision,
            current["revision"],
            UUID(current["id"]),
            reason,
            UUID(current["original_blob_id"]),
            UUID(current["normalized_blob_id"]),
            current["original_media_type"],
            current["width_px"],
            current["height_px"],
            Jsonb(current["normalization_proof"]),
            Jsonb(current["source_refs"]),
            digest,
            actor,
        ),
    )
    c.execute(
        "UPDATE authoring_image_assets SET head_revision=%s,head_version_id=%s WHERE id=%s AND bid_id=%s",
        (revision, identity, asset_id, bid),
    )
    audit(
        c,
        actor,
        bid,
        "authoring.image_withdrawn",
        {"asset_id": str(asset_id), "version_id": str(identity), "reason": reason},
    )
    return version(c, bid, asset_id)


def freeze_assets(c, bid, identities, *, worker=False):
    """RC + bid serialization is shared by API changes and Worker fenced publish.

    Worker has SELECT only. Do NOT FOR UPDATE the asset tables; API head changes
    must take this same existing bid lock before updating their own writable head.
    """
    require_transaction(c)
    require_available(c, worker=worker)
    lock(c, bid, worker=worker)
    result = []
    for asset_id, revision, version_id in sorted(set(identities)):
        value = version(c, bid, UUID(asset_id), UUID(version_id))
        if not value["is_current"] or value["revision"] != revision or value["state"] != "active":
            raise DomainError("AUTHORING_IMAGE_STALE", "图片已更新或停用，请重新选择有效图片", 409)
        source_guard(c, bid, value["source_refs"])
        original = fetch(c, "SELECT * FROM blobs WHERE id=%s", (UUID(value["original_blob_id"]),))
        normalized = fetch(
            c, "SELECT * FROM blobs WHERE id=%s", (UUID(value["normalized_blob_id"]),)
        )
        result.append(
            {
                "asset_id": asset_id,
                "asset_revision": revision,
                "asset_version_id": version_id,
                "version_digest": value["digest"],
                "normalization_proof": value["normalization_proof"],
                "original_blob": clean(original),
                "normalized_blob": clean(normalized),
                "source_refs": value["source_refs"],
            }
        )
    return result
