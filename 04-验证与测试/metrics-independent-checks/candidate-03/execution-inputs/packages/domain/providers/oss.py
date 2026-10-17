"""阿里云OSS原生V4适配；不修改bucket策略，不通过S3协议伪装OSS。"""

from __future__ import annotations

import hashlib
import logging
import os
import re

import oss2
import requests
from botocore.exceptions import ClientError

from packages.domain.runtime import DomainError

ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
BUCKET = "tender-base"
REGION = "cn-hangzhou"

# SDK的DEBUG/INFO会输出签名、headers和错误响应原文。应用只暴露下列安全错误。
for _name in list(logging.Logger.manager.loggerDict):
    if _name == "oss2" or _name.startswith("oss2."):
        logging.getLogger(_name).disabled = True


def _error(exc):
    status = getattr(exc, "status", None)
    if status == 404:
        # 保留现有storage port的缺失分支，不包含原始请求/URL/响应。
        return ClientError({"Error": {"Code": "NoSuchKey", "Message": "对象不存在"}}, "Object")
    if status in (401, 403):
        return DomainError("OBJECT_STORE_ACCESS_DENIED", "对象存储访问被拒绝", 503)
    if status == 409:
        return DomainError("OBJECT_ALREADY_EXISTS", "对象已存在，禁止覆盖", 409)
    return DomainError("OBJECT_STORE_UNAVAILABLE", "对象存储请求失败", 503, retryable=True)


class SafeSession(oss2.Session):
    """沿用环境proxy/CA，禁止重定向和SDK隐式网络重试。"""

    def __init__(self):
        super().__init__(adapter=requests.adapters.HTTPAdapter(max_retries=0))

    def do_request(self, req, timeout):
        try:
            response = self.session.request(
                req.method,
                req.url,
                data=req.data,
                params=req.params,
                headers=req.headers,
                stream=True,
                timeout=(5, 30),
                proxies=req.proxies,
                allow_redirects=False,
            )
            return oss2.http.Response(response)
        except requests.RequestException:
            raise DomainError(
                "OBJECT_STORE_UNAVAILABLE", "对象存储连接失败", 503, retryable=True
            ) from None


class SafeBody:
    def __init__(self, native):
        self.native = native

    def read(self, size=None):
        try:
            return self.native.read(size)
        except (oss2.exceptions.OssError, requests.RequestException):
            raise DomainError(
                "OBJECT_STORE_UNAVAILABLE", "对象存储读取失败", 503, retryable=True
            ) from None

    def close(self):
        self.native.resp.response.close()


class OssClient:
    def __init__(self, native, bucket_name=BUCKET, prefix=""):
        if not isinstance(prefix, str) or (
            prefix and (not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/", prefix))
        ):
            raise DomainError("OBJECT_STORE_CONFIG_INVALID", "对象存储命名空间前缀不合法", 503)
        self.native, self.bucket_name, self.prefix = native, bucket_name, prefix

    @classmethod
    def from_env(cls):
        expected = {
            "OBJECT_STORE_BACKEND": "oss",
            "OBJECT_STORE_ENDPOINT": ENDPOINT,
            "OBJECT_STORE_BUCKET": BUCKET,
            "OBJECT_STORE_REGION": REGION,
            "OBJECT_STORE_BUCKET_URL": f"https://{BUCKET}.oss-cn-hangzhou.aliyuncs.com",
        }
        if any(os.environ.get(k, "").rstrip("/") != v for k, v in expected.items()):
            raise DomainError("OBJECT_STORE_CONFIG_INVALID", "OSS须配置指定杭州官方HTTPS端点", 503)
        access = os.environ.get("OBJECT_STORE_ACCESS_KEY")
        secret = os.environ.get("OBJECT_STORE_SECRET_KEY")
        if not access or not secret:
            raise DomainError("OBJECT_STORE_NOT_CONFIGURED", "尚未配置OSS凭据", 503)
        native = oss2.Bucket(
            oss2.AuthV4(access, secret),
            ENDPOINT,
            BUCKET,
            region=REGION,
            session=SafeSession(),
            connect_timeout=30,
        )
        return cls(native, prefix=os.environ.get("OBJECT_STORE_PREFIX", ""))

    def _key(self, Bucket, Key):
        if (
            Bucket != self.bucket_name
            or not isinstance(Key, str)
            or not Key
            or Key.startswith(("/", "?"))
            or "\x00" in Key
        ):
            raise DomainError("OBJECT_LOCATOR_INVALID", "对象定位不合法", 422)
        if any(part in (".", "..") for part in Key.split("/")):
            raise DomainError("OBJECT_LOCATOR_INVALID", "对象定位不合法", 422)
        return self.prefix + Key

    @staticmethod
    def _meta(native):
        headers = {str(k).lower(): v for k, v in native.headers.items()}
        return {
            "ContentLength": native.content_length,
            "ContentType": headers.get("content-type"),
            "ETag": native.etag,
            "Metadata": {k[11:]: v for k, v in headers.items() if k.startswith("x-oss-meta-")},
            "VersionId": headers.get("x-oss-version-id"),
        }

    def head_object(self, *, Bucket, Key):
        key = self._key(Bucket, Key)
        try:
            return self._meta(self.native.head_object(key))
        except oss2.exceptions.OssError as exc:
            raise _error(exc) from None

    def get_object(self, *, Bucket, Key):
        key = self._key(Bucket, Key)
        try:
            native = self.native.get_object(key)
            return {**self._meta(native), "Body": SafeBody(native)}
        except oss2.exceptions.OssError as exc:
            raise _error(exc) from None

    def put_object(self, *, Bucket, Key, Body, ContentType):
        key = self._key(Bucket, Key)
        if not isinstance(Body, bytes) or not isinstance(ContentType, str):
            raise DomainError("OBJECT_REQUEST_INVALID", "对象内容须为明确字节和媒体类型", 422)
        digest = hashlib.sha256(Body).hexdigest()
        try:
            result = self.native.put_object(
                key,
                Body,
                headers={
                    "Content-Type": ContentType,
                    "x-oss-forbid-overwrite": "true",
                    "x-oss-meta-sha256": digest,
                },
            )
            return {
                "ETag": result.etag,
                "VersionId": result.headers.get("x-oss-version-id"),
                "sha256": digest,
                "deduplicated": False,
            }
        except oss2.exceptions.OssError as exc:
            if getattr(exc, "status", None) != 409 or getattr(exc, "code", None) not in (
                "FileAlreadyExists",
                "ObjectAlreadyExists",
            ):
                raise _error(exc) from None
        # 内容寻址重放可复用，但HEAD/ETag/CRC不能证明内容相同。
        existing = self.get_object(Bucket=Bucket, Key=Key)
        try:
            same = (
                existing["ContentLength"] == len(Body)
                and existing["Body"].read(len(Body) + 1) == Body
            )
        finally:
            existing["Body"].close()
        if not same:
            raise DomainError("OBJECT_CONTENT_CONFLICT", "对象已存在且实际字节不同，未覆盖", 409)
        return {
            "ETag": existing["ETag"],
            "VersionId": existing["VersionId"],
            "sha256": digest,
            "deduplicated": True,
        }

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        key = self._key(Params.get("Bucket"), Params.get("Key"))
        if (
            type(ExpiresIn) is not int
            or not 1 <= ExpiresIn <= 900
            or method not in ("get_object", "put_object")
        ):
            raise DomainError("OBJECT_GRANT_INVALID", "对象授权方式或有效期不合法", 422)
        if set(Params) - {"Bucket", "Key", "ContentType"}:
            raise DomainError("OBJECT_GRANT_INVALID", "对象授权参数不支持", 422)
        if method == "put_object" and not Params["Key"].startswith(
            ("staging/", "workbench-checks/")
        ):
            raise DomainError("OBJECT_GRANT_INVALID", "正式原件不允许签发上传授权", 422)
        media = Params.get("ContentType", "application/pdf")
        if not isinstance(media, str) or not re.fullmatch(r"[\w.+-]+/[\w.+-]+", media):
            raise DomainError("OBJECT_GRANT_INVALID", "媒体类型不合法", 422)
        try:
            return self.native.sign_url(
                "GET" if method == "get_object" else "PUT",
                key,
                ExpiresIn,
                headers={"Content-Type": media} if method == "put_object" else None,
                slash_safe=True,
            )
        except oss2.exceptions.OssError as exc:
            raise _error(exc) from None

    def delete_object(self, *, Bucket, Key):
        """仅删除显式对象；不提供list/bucket管理操作。用于本系统合成探针清理。"""
        key = self._key(Bucket, Key)
        try:
            result = self.native.delete_object(key)
            return {"VersionId": result.headers.get("x-oss-version-id")}
        except oss2.exceptions.OssError as exc:
            raise _error(exc) from None
