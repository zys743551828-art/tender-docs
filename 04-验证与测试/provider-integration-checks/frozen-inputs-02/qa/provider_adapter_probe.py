"""实际OSS适配器独立验证；合成对象与真实存储，无应用成功冒充。"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

from qa.helpers import ensure, pdf_bytes
from qa.provider_preflight import configuration, safe_error, write


def main():
    result = dict(
        scope="原生OSS adapter真实条件写/幂等/冲突/私有下载/签名上传；无AI业务结论",
        passed=False,
        checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
        object_list_requests=0,
        bucket_configuration_mutations=0,
    )
    client = None
    created = []
    try:
        os.environ.update(configuration())
        from packages.domain.providers.oss import OssClient
        from packages.domain.runtime import DomainError

        prefix = "workbench-checks/" + str(uuid4()) + "/"
        os.environ["OBJECT_STORE_PREFIX"] = prefix
        client = OssClient.from_env()
        key = "blobs/适配器合成正文.pdf"
        result["prefix"] = prefix
        source = Path(__file__).resolve().parents[1] / "packages/domain/providers/oss.py"
        output = Path(os.environ["QA_PROVIDER_OUTPUT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "executed-oss-adapter.py").write_bytes(source.read_bytes())
        data = pdf_bytes("Independent native OSS adapter synthetic immutable body.")
        expected = hashlib.sha256(data).hexdigest()
        bucket = "tender-base"
        put = client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/pdf")
        created.append((prefix + key, put.get("VersionId")))
        ensure(
            put["sha256"] == expected and put["deduplicated"] is False,
            "首次实际适配器条件写记录正确身份",
        )
        repeat = client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/pdf")
        ensure(
            repeat["deduplicated"] is True and repeat["sha256"] == expected, "相同实际字节复用原件"
        )
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=b"Different protected original",
                ContentType="application/pdf",
            )
        except DomainError as error:
            ensure(
                error.code == "OBJECT_CONTENT_CONFLICT" and error.status == 409,
                "不同实际字节同key明确409",
            )
        else:
            raise AssertionError("同key不同内容不应覆盖")
        head = client.head_object(Bucket=bucket, Key=key)
        original = client.get_object(Bucket=bucket, Key=key)
        try:
            ensure(
                head["ContentLength"] == len(data)
                and hashlib.sha256(original["Body"].read()).hexdigest() == expected,
                "冲突后实际原件字节仍完整",
            )
        finally:
            original["Body"].close()
        result["checks"].append("实际adapter首写、同字节复用、异字节409且原SHA保持")
        signed = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
        )
        with httpx.Client(timeout=20, trust_env=True, follow_redirects=False) as caller:
            response = caller.get(signed)
            anonymous = caller.get(
                "https://tender-base.oss-cn-hangzhou.aliyuncs.com/" + quote(prefix + key, safe="/")
            )
            ensure(
                response.status_code == 200
                and hashlib.sha256(response.content).hexdigest() == expected
                and anonymous.status_code == 403,
                "adapter私有签名下载真实SHA且匿名拒绝",
            )
            result["checks"].append("真实adapter签名GET/中文key SHA与匿名403")
            staging_key = "staging/浏览器上传合成.pdf"
            grant = client.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket, "Key": staging_key, "ContentType": "application/pdf"},
                ExpiresIn=60,
            )
            sent = caller.put(grant, content=data, headers={"Content-Type": "application/pdf"})
            ensure(sent.status_code == 200, "adapter签名PUT必须可被实际HTTP客户端使用")
            created.append((prefix + staging_key, sent.headers.get("x-oss-version-id")))
            stored = client.get_object(Bucket=bucket, Key=staging_key)
            try:
                ensure(
                    hashlib.sha256(stored["Body"].read()).hexdigest() == expected,
                    "签名上传后的实际GET原件SHA一致",
                )
            finally:
                stored["Body"].close()
            result["request_ids"] = dict(
                get=response.headers.get("x-oss-request-id"),
                anonymous=anonymous.headers.get("x-oss-request-id"),
                put=sent.headers.get("x-oss-request-id"),
            )
            result["checks"].append("实际HTTP使用adapter签名PUT并以原生GET核SHA")
        result["sha256"] = expected
        result["passed"] = True
    except Exception as error:
        result["failure"] = safe_error(error)
        if isinstance(error, AssertionError):
            result["failure"]["message"] = str(error)
        for field in ["code", "status", "request_id"]:
            if hasattr(error, field):
                result["failure"][field] = getattr(error, field)
    finally:
        result["cleanup"] = []
        if client:
            for key, version in created:
                try:
                    removed = client.native.delete_object(
                        key, params={"versionId": version} if version else None
                    )
                    result["cleanup"].append(
                        dict(key=key, status=removed.status, request_id=removed.request_id)
                    )
                except Exception as error:
                    result["cleanup"].append(safe_error(error))
                    result["passed"] = False
        adapter = Path(__file__).resolve().parents[1] / "packages/domain/providers/oss.py"
        result["adapter_sha256"] = (
            hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter.exists() else None
        )
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        write(Path(os.environ["QA_PROVIDER_OUTPUT"]), result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
