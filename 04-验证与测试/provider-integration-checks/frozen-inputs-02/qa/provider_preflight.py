"""官方DeepSeek与指定OSS bucket的独立只读预检；不列对象或调用chat。"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    path = ROOT / ".local/providers.env"
    if path.stat().st_mode & 0o077:
        raise RuntimeError("provider凭据文件必须为600")
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, _, value = line.partition("=")
        parts = shlex.split(value, comments=True)
        if len(parts) != 1:
            raise RuntimeError("provider配置格式不合法")
        values[name.strip()] = parts[0]
    if urlsplit(values["DEEPSEEK_BASE_URL"]).hostname != "api.deepseek.com":
        raise RuntimeError("只允许指定官方模型host")
    endpoint = urlsplit(values["OBJECT_STORE_ENDPOINT"])
    if endpoint.scheme != "https" or endpoint.hostname not in {
        "oss-cn-hangzhou.aliyuncs.com",
        "tender-base.oss-cn-hangzhou.aliyuncs.com",
    }:
        raise RuntimeError("只允许用户指定杭州OSS host")
    if values["OBJECT_STORE_BUCKET"] != "tender-base":
        raise RuntimeError("只允许用户指定bucket")
    return values


def safe_error(error):
    return dict(type=type(error).__name__, message="外部只读请求未完成；不输出密钥/请求头/签名URL")


def write(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")


def main():
    result = dict(
        scope="DeepSeek模型列表及指定杭州OSS bucket信息/ACL只读预检；无对象扫描/付费chat",
        started_at=datetime.now(timezone.utc).isoformat(),
        checks=[],
        passed=False,
        chat_requests=0,
        object_list_requests=0,
        bucket_configuration_mutations=0,
        issues=[],
    )
    try:
        values = configuration()
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=True) as client:
            response = client.get(
                "https://api.deepseek.com/v1/models",
                headers={"Authorization": "Bearer " + values["DEEPSEEK_API_KEY"]},
            )
        result["models"] = dict(status=response.status_code, host="api.deepseek.com")
        if response.status_code != 200:
            raise RuntimeError("模型列表未授权或不可用")
        models = [item["id"] for item in response.json().get("data", [])]
        result["models"]["available"] = models
        requested_model = values["DEEPSEEK_MODEL"]
        result["models"]["requested_model"] = requested_model
        result["models"]["requested_model_available"] = requested_model in models
        if requested_model not in models:
            result["issues"].append("REQUESTED_MODEL_NOT_LISTED")
        result["checks"].append("官方模型列表200，实际可用模型如实记录")
        import oss2

        auth = oss2.AuthV4(values["OBJECT_STORE_ACCESS_KEY"], values["OBJECT_STORE_SECRET_KEY"])
        bucket = oss2.Bucket(
            auth,
            values["OBJECT_STORE_ENDPOINT"],
            values["OBJECT_STORE_BUCKET"],
            region=values["OBJECT_STORE_REGION"],
            connect_timeout=20,
        )
        info = bucket.get_bucket_info()
        result["bucket_info"] = dict(
            status=info.status,
            request_id=info.request_id,
            location=info.location,
            storage_class=info.storage_class,
        )
        result["checks"].append("指定bucket只读信息授权成功")
        acl = bucket.get_bucket_acl()
        result["bucket_acl"] = dict(status=acl.status, request_id=acl.request_id, acl=acl.acl)
        if acl.acl != "private":
            raise RuntimeError("bucket当前非private，需要按实际权限评估")
        result["checks"].append("指定bucket只读ACL为private")
        result["passed"] = not result["issues"]
    except Exception as error:
        result["failure"] = safe_error(error)
        if hasattr(error, "status"):
            result["failure"]["status"] = error.status
        if hasattr(error, "code"):
            result["failure"]["provider_code"] = error.code
        if hasattr(error, "request_id"):
            result["failure"]["request_id"] = error.request_id
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write(Path(os.environ["QA_PROVIDER_OUTPUT"]), result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
