"""真实OSS合成对象独立验证；只访问本次UUID前缀，不列既有对象。"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import oss2

from qa.helpers import ensure, pdf_bytes
from qa.provider_preflight import configuration, safe_error, write


def main():
    result = dict(
        scope="指定真实杭州OSS bucket的本次合成UUID前缀PUT/GET/私有签名/匿名拒绝/中文名/条件写/清理",
        passed=False,
        started_at=datetime.now(timezone.utc).isoformat(),
        checks=[],
        object_list_requests=0,
        bucket_configuration_mutations=0,
    )
    bucket = None
    created = False
    version_id = None
    object_key = "workbench-checks/" + str(uuid4()) + "/合成资料原件-市政道路.pdf"
    result["object_key"] = object_key
    try:
        values = configuration()
        bucket = oss2.Bucket(
            oss2.AuthV4(values["OBJECT_STORE_ACCESS_KEY"], values["OBJECT_STORE_SECRET_KEY"]),
            values["OBJECT_STORE_ENDPOINT"],
            values["OBJECT_STORE_BUCKET"],
            region=values["OBJECT_STORE_REGION"],
            connect_timeout=20,
        )
        data = pdf_bytes(
            "Synthetic external provider check only; no bid or private source material."
        )
        expected = hashlib.sha256(data).hexdigest()
        uploaded = bucket.put_object(
            object_key,
            data,
            headers={"x-oss-forbid-overwrite": "true", "Content-Type": "application/pdf"},
        )
        created = True
        version_id = uploaded.headers.get("x-oss-version-id")
        result["put"] = dict(status=uploaded.status, request_id=uploaded.request_id)
        result["checks"].append("指定本次UUID中文key条件PUT成功")
        downloaded = bucket.get_object(object_key)
        original = downloaded.read()
        ensure(hashlib.sha256(original).hexdigest() == expected, "鉴权GET的SHA必须匹配")
        result["get"] = dict(
            status=downloaded.status, request_id=downloaded.request_id, sha256=expected
        )
        result["checks"].append("真实鉴权GET原字节SHA一致")
        try:
            bucket.put_object(
                object_key,
                b"Must not overwrite original",
                headers={"x-oss-forbid-overwrite": "true"},
            )
        except oss2.exceptions.ServerError as error:
            result["conditional_put"] = dict(
                status=error.status, code=error.code, request_id=error.request_id
            )
            ensure(
                error.status == 409 and error.code == "FileAlreadyExists", "重复条件写需明确拒绝"
            )
        else:
            raise AssertionError("重复条件写竟覆盖原对象")
        ensure(
            hashlib.sha256(bucket.get_object(object_key).read()).hexdigest() == expected,
            "拒绝覆盖后原对象摘要保持",
        )
        result["checks"].append("同key forbid-overwrite拒绝409且原字节保持")
        filename = "资料原件-市政道路.pdf"
        disposition = "attachment; filename*=UTF-8''" + quote(filename, safe="")
        signed = bucket.sign_url(
            "GET", object_key, 60, params={"response-content-disposition": disposition}
        )
        ensure(
            urlsplit(signed).hostname == "tender-base.oss-cn-hangzhou.aliyuncs.com",
            "签名只发送指定bucket官方host",
        )
        unsigned = "https://tender-base.oss-cn-hangzhou.aliyuncs.com/" + quote(object_key, safe="/")
        with httpx.Client(timeout=20, trust_env=True, follow_redirects=False) as client:
            private = client.get(signed)
            anonymous = client.get(unsigned)
        ensure(
            private.status_code == 200 and hashlib.sha256(private.content).hexdigest() == expected,
            "私有签名URL真实下载SHA一致",
        )
        ensure(
            private.headers.get("content-disposition") == disposition,
            "中文下载名Content-Disposition不丢失",
        )
        ensure(anonymous.status_code == 403, "同对象去签名匿名请求必须拒绝")
        result["signed_download"] = dict(
            status=private.status_code,
            sha256=expected,
            filename=filename,
            request_id=private.headers.get("x-oss-request-id"),
        )
        result["anonymous"] = dict(
            status=anonymous.status_code, request_id=anonymous.headers.get("x-oss-request-id")
        )
        result["checks"].append("中文名私有签名下载真实SHA一致，去签名匿名403")
        result["passed"] = True
    except Exception as error:
        result["failure"] = safe_error(error)
        if isinstance(error, AssertionError):
            result["failure"]["message"] = str(error)
        for source, target in [
            ("status", "status"),
            ("code", "provider_code"),
            ("request_id", "request_id"),
        ]:
            if hasattr(error, source):
                result["failure"][target] = getattr(error, source)
    finally:
        if bucket is not None and created:
            try:
                params = {"versionId": version_id} if version_id else None
                removed = bucket.delete_object(object_key, params=params)
                result["cleanup"] = dict(
                    status=removed.status,
                    request_id=removed.request_id,
                    only_created_object=True,
                    exact_version=bool(version_id),
                )
            except Exception as error:
                result["cleanup"] = safe_error(error)
                result["passed"] = False
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write(Path(os.environ["QA_PROVIDER_OUTPUT"]), result)
    print(
        dict(
            passed=result["passed"],
            checks=len(result["checks"]),
            failure=result.get("failure"),
            cleanup=result.get("cleanup"),
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
