"""原生OSS multipart，只操作已登记会话的精确对象/UploadId，不列举桶。"""

import hashlib

import oss2
import requests
from botocore.exceptions import ClientError

from packages.domain import storage
from packages.domain.runtime import DomainError


class NativeMultipart:
    def __init__(self):
        self.port = storage.client()
        if not hasattr(self.port, "native"):
            raise DomainError("MULTIPART_BACKEND_UNAVAILABLE", "分片上传仅在原生OSS配置启用", 503)

    def _key(self, key):
        return self.port._key(Bucket=storage.bucket(), Key=key)

    def _call(self, method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (oss2.exceptions.OssError, requests.RequestException):
            raise DomainError(
                "MULTIPART_PROVIDER_UNAVAILABLE",
                "分片存储请求未确认，请刷新状态后重试",
                503,
                retryable=True,
            ) from None

    def initialize(self, key, media_type="application/pdf"):
        return self._call(
            self.port.native.init_multipart_upload,
            self._key(key),
            headers={"Content-Type": media_type},
        ).upload_id

    def parts(self, key, provider_id):
        marker = ""
        result: list[dict] = []
        for _ in range(11):
            page = self._call(
                self.port.native.list_parts,
                self._key(key),
                provider_id,
                marker=marker,
                max_parts=1000,
            )
            result.extend(
                {"part_number": p.part_number, "etag": p.etag, "byte_size": p.size}
                for p in page.parts
            )
            if not page.is_truncated:
                return result
            next_marker = str(page.next_marker)
            if next_marker == marker:
                break
            marker = next_marker
        raise DomainError("MULTIPART_PARTS_INVALID", "分片清单超出对账范围", 409)

    def upload(self, key, provider_id, number, data):
        return self._call(
            self.port.native.upload_part, self._key(key), provider_id, number, data
        ).etag

    def complete(self, key, provider_id, parts):
        self._call(
            self.port.native.complete_multipart_upload,
            self._key(key),
            provider_id,
            [oss2.models.PartInfo(p["part_number"], p["etag"]) for p in parts],
            headers={"x-oss-forbid-overwrite": "true"},
        )

    def abort(self, key, provider_id):
        try:
            self.port.native.abort_multipart_upload(self._key(key), provider_id)
        except oss2.exceptions.NoSuchUpload:
            return
        except (oss2.exceptions.OssError, requests.RequestException):
            raise DomainError(
                "MULTIPART_ABORT_UNCONFIRMED",
                "上传已停止授权，远端分片取消尚未确认",
                503,
                retryable=True,
            ) from None

    def verify_complete(self, row):
        try:
            head = self.port.head_object(Bucket=storage.bucket(), Key=row["staging_key"])
            if head["ContentLength"] != row["declared_size"]:
                raise DomainError("UPLOAD_SIZE_MISMATCH", "完整对象大小不匹配", 422)
            result = self.port.get_object(Bucket=storage.bucket(), Key=row["staging_key"])
        except ClientError:
            raise DomainError(
                "MULTIPART_COMPLETE_UNCONFIRMED",
                "尚未查到完整对象，请使用同一完成请求重试",
                503,
                retryable=True,
            ) from None
        hasher, count = hashlib.sha256(), 0
        try:
            if result["ContentLength"] != row["declared_size"]:
                raise DomainError("UPLOAD_SIZE_MISMATCH", "完整对象大小不匹配", 422)
            while True:
                data = result["Body"].read(min(1024 * 1024, row["declared_size"] + 1 - count))
                if not data:
                    break
                count += len(data)
                hasher.update(data)
                if count > row["declared_size"]:
                    raise DomainError("UPLOAD_SIZE_MISMATCH", "完整对象大小不匹配", 422)
        finally:
            result["Body"].close()
        if count != row["declared_size"] or hasher.hexdigest() != row["declared_digest"]:
            raise DomainError("UPLOAD_DIGEST_MISMATCH", "完整对象摘要不匹配，请重新上传", 422)
