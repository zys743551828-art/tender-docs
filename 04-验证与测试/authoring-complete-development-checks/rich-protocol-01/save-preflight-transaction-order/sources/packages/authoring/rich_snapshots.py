"""Complete immutable image manifests embedded in core draft snapshots."""

from uuid import UUID

from packages.authoring.content import validate_content
from packages.domain.runtime import DomainError, sha

PROTOCOL = "authoring-image-snapshot/1"


def identities(sections):
    values = set()
    for section in sections:
        validate_content(section)
        for node in section["content"]:
            if node["type"] == "image":
                attrs = node["attrs"]
                values.add(
                    (
                        str(UUID(attrs["asset_id"])),
                        attrs["asset_revision"],
                        str(UUID(attrs["asset_version_id"])),
                    )
                )
    return sorted(values)


def prepare(c, bid, sections, *, worker=False):
    from packages.authoring.rich_assets import freeze_assets

    value = {
        "protocol": PROTOCOL,
        "items": freeze_assets(c, bid, identities(sections), worker=worker),
    }
    return value | {"digest": sha(value)}


def checked(value, sections):
    expected = identities(sections)
    if value is None:
        if expected:
            raise DomainError("AUTHORING_IMAGE_SNAPSHOT_CORRUPT", "图片缺少冻结资产清单", 409)
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"protocol", "items", "digest"}
        or value["protocol"] != PROTOCOL
        or value["digest"] != sha({k: v for k, v in value.items() if k != "digest"})
        or not isinstance(value["items"], list)
    ):
        raise DomainError("AUTHORING_IMAGE_SNAPSHOT_CORRUPT", "图片清单摘要不一致", 409)
    found = []
    for item in value["items"]:
        try:
            if set(item) != {
                "asset_id",
                "asset_revision",
                "asset_version_id",
                "version_digest",
                "normalization_proof",
                "original_blob",
                "normalized_blob",
                "source_refs",
            }:
                raise ValueError()
            identity = (
                str(UUID(item["asset_id"])),
                item["asset_revision"],
                str(UUID(item["asset_version_id"])),
            )
            proof, original, normalized = (
                item["normalization_proof"],
                item["original_blob"],
                item["normalized_blob"],
            )
            if (
                original["digest"] != proof["input_digest"]
                or original["media_type"] != proof["input_media_type"]
                or normalized["digest"] != proof["output_digest"]
                or normalized["byte_size"] != proof["output_byte_size"]
                or normalized["media_type"] != "image/png"
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DomainError(
                "AUTHORING_IMAGE_SNAPSHOT_CORRUPT", "图片版本或原图/规范图身份不一致", 409
            ) from None
        found.append(identity)
    if found != expected:
        raise DomainError("AUTHORING_IMAGE_SNAPSHOT_CORRUPT", "图片清单未精确覆盖正文版本", 409)
    return value


def assert_current(c, bid, value, sections, *, worker=False):
    checked(value, sections)
    if value is not None and prepare(c, bid, sections, worker=worker) != value:
        raise DomainError("AUTHORING_IMAGE_STALE", "图片资产与冻结快照不一致", 409)
