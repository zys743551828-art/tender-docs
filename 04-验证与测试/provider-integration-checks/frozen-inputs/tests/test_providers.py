"""离线供应商边界测试，不请求付费模型或用户OSS资料。"""

import asyncio
import hashlib
import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import oss2
import pytest
import requests
from botocore.exceptions import ClientError

from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient, ProviderError
from packages.domain.providers.oss import OssClient, SafeBody, SafeSession
from packages.domain.runtime import DomainError

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}


def envelope(content='{"ok":true}', finish="stop"):
    return {
        "id": "response-id",
        "model": "deepseek-flash",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }


def model_client(handler, **kwargs):
    return DeepSeekClient(
        api_key="synthetic-never-network-secret", transport=httpx.MockTransport(handler), **kwargs
    )


def test_generation_single_request_json_usage_and_qualification():
    requests_seen = []

    def handler(request):
        requests_seen.append(request)
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["temperature"] == 0 and body["max_tokens"] == 32
        assert body["response_format"] == {"type": "json_object"}
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        return httpx.Response(200, json=envelope(), headers={"x-request-id": "official-request-id"})

    result = model_client(handler).generate(
        messages=[{"role": "user", "content": "Return JSON"}], max_tokens=32, json_schema=SCHEMA
    )
    assert len(requests_seen) == 1
    assert result.data == {"ok": True} and result.request_id == "official-request-id"
    assert result.usage["total_tokens"] == 13 and not result.formal_scoring_eligible


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500, 503])
def test_model_http_failure_never_retries_or_echoes(status):
    seen = []
    marker = "private-provider-response-secret"

    def handler(request):
        seen.append(request)
        return httpx.Response(
            status, text=marker, headers={"location": "https://attacker.invalid/"}
        )

    with pytest.raises(ProviderError) as caught:
        model_client(handler).generate(messages=[{"role": "user", "content": marker}])
    assert len(seen) == 1 and not caught.value.retryable
    assert caught.value.details["billing_state"] == "unknown"
    assert marker not in str(caught.value.details) + str(caught.value)


def test_model_timeout_has_unknown_billing_and_one_attempt():
    seen = []

    async def handler(request):
        seen.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=envelope())

    with pytest.raises(ProviderError) as caught:
        model_client(handler, total_timeout=0.01).generate(
            messages=[{"role": "user", "content": "Return JSON"}]
        )
    assert len(seen) == 1 and caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.details["billing_state"] == "unknown" and not caught.value.retryable


@pytest.mark.parametrize(
    "response,code",
    [
        (envelope(finish="length"), "MODEL_OUTPUT_INCOMPLETE"),
        (envelope('{"ok":false}'), "MODEL_OUTPUT_INVALID"),
        (envelope('{"ok":true,"ok":false}'), "MODEL_OUTPUT_INVALID"),
        (envelope('{"ok":NaN}'), "MODEL_OUTPUT_INVALID"),
        (
            {
                **envelope(),
                "usage": {"prompt_tokens": True, "completion_tokens": 4, "total_tokens": 5},
            },
            "MODEL_INVALID_RESPONSE",
        ),
        ({**envelope(), "model": "unexpected-other-model"}, "MODEL_INVALID_RESPONSE"),
    ],
)
def test_invalid_model_outputs_never_accepted(response, code):
    with pytest.raises(ProviderError) as caught:
        model_client(lambda r: httpx.Response(200, json=response)).generate(
            messages=[{"role": "user", "content": "Return JSON"}], json_schema=SCHEMA
        )
    assert caught.value.code == code and not caught.value.retryable


def test_invalid_configuration_and_schema_fail_before_network():
    with pytest.raises(DomainError):
        DeepSeekClient(api_key="synthetic", base_url="http://api.deepseek.com")
    with pytest.raises(DomainError):
        DeepSeekClient(api_key="synthetic", model="deepseek-chat")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=envelope())

    with pytest.raises(DomainError):
        model_client(handler).generate(
            messages=[{"role": "user", "content": "Return JSON"}],
            json_schema={"$ref": "https://external.invalid/schema"},
        )
    with pytest.raises(DomainError):
        model_client(handler).generate(
            messages=[{"role": "user", "content": "Return JSON"}],
            json_schema={"$ref": "#/$defs/missing"},
        )
    assert seen == []


def oss_env(monkeypatch):
    for key, value in {
        "OBJECT_STORE_BACKEND": "oss",
        "OBJECT_STORE_ENDPOINT": "https://oss-cn-hangzhou.aliyuncs.com",
        "OBJECT_STORE_BUCKET": "tender-base",
        "OBJECT_STORE_REGION": "cn-hangzhou",
        "OBJECT_STORE_BUCKET_URL": "https://tender-base.oss-cn-hangzhou.aliyuncs.com",
        "OBJECT_STORE_ACCESS_KEY": "synthetic-access",
        "OBJECT_STORE_SECRET_KEY": "synthetic-secret",
        "OBJECT_STORE_PREFIX": "",
    }.items():
        monkeypatch.setenv(key, value)


def test_native_oss_signing_and_strict_grants(monkeypatch):
    oss_env(monkeypatch)
    monkeypatch.setenv("OBJECT_STORE_PREFIX", "workbench-environments/test-id/")
    client = storage.client()
    assert isinstance(client, OssClient) and isinstance(client.native.auth, oss2.AuthV4)
    url = client.generate_presigned_url(
        "put_object", Params={"Bucket": "tender-base", "Key": "staging/中文.pdf"}, ExpiresIn=60
    )
    parsed = urlsplit(url)
    assert (
        parsed.scheme == "https" and parsed.hostname == "tender-base.oss-cn-hangzhou.aliyuncs.com"
    )
    assert "workbench-environments/test-id/staging/" in parsed.path
    assert "x-oss-signature" in parse_qs(parsed.query)
    for key in ["blobs/immutable.pdf", "../other.pdf", "/other.pdf"]:
        with pytest.raises(DomainError):
            client.generate_presigned_url(
                "put_object", Params={"Bucket": "tender-base", "Key": key}, ExpiresIn=60
            )
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://attacker.invalid")
    with pytest.raises(DomainError):
        storage.client()


class NativeMemory:
    """仅模拟SDK网络边界；验证适配器条件头、命名空间和碰撞处理。"""

    def __init__(self):
        self.objects = {}
        self.calls = []

    def put_object(self, key, data, headers):
        self.calls.append(("put", key, headers))
        assert headers["x-oss-forbid-overwrite"] == "true"
        if key in self.objects:
            raise oss2.exceptions.ServerError(409, {}, "collision", {"Code": "FileAlreadyExists"})
        self.objects[key] = data
        assert headers["x-oss-meta-sha256"] == hashlib.sha256(data).hexdigest()
        return SimpleNamespace(etag="opaque-etag", headers={"x-oss-version-id": "version-id"})

    def get_object(self, key):
        self.calls.append(("get", key))
        if key not in self.objects:
            raise oss2.exceptions.ServerError(404, {}, "missing", {"Code": "NoSuchKey"})
        data = self.objects[key]
        body = io.BytesIO(data)
        return SimpleNamespace(
            headers={},
            content_length=len(data),
            etag="opaque-etag",
            read=body.read,
            resp=SimpleNamespace(response=body),
        )

    def head_object(self, key):
        return self.get_object(key)

    def delete_object(self, key):
        self.calls.append(("delete", key))
        self.objects.pop(key, None)
        return SimpleNamespace(headers={})


def test_oss_immutable_dedup_actual_bytes_and_prefix():
    native = NativeMemory()
    client = OssClient(native, prefix="workbench-checks/test-id/")
    args = {"Bucket": "tender-base", "Key": "blobs/abc", "ContentType": "application/pdf"}
    assert not client.put_object(**args, Body=b"original")["deduplicated"]
    assert client.put_object(**args, Body=b"original")["deduplicated"]
    with pytest.raises(DomainError) as caught:
        client.put_object(**args, Body=b"changed")
    assert caught.value.code == "OBJECT_CONTENT_CONFLICT"
    result = client.get_object(Bucket="tender-base", Key="blobs/abc")
    assert result["Body"].read() == b"original"
    assert client.head_object(Bucket="tender-base", Key="blobs/abc")["ContentLength"] == 8
    client.delete_object(Bucket="tender-base", Key="blobs/abc")
    assert all(call[1].startswith("workbench-checks/test-id/blobs/") for call in native.calls)
    assert not native.objects
    with pytest.raises(ClientError) as error:
        client.get_object(Bucket="tender-base", Key="blobs/abc")
    assert error.value.response["Error"]["Code"] == "NoSuchKey"


@pytest.mark.parametrize(
    "prefix", ["/outside/", "../other/", "valid/no-trailing-slash", "a//b/", "a/../../"]
)
def test_invalid_oss_prefix_rejected(prefix):
    with pytest.raises(DomainError):
        OssClient(NativeMemory(), prefix=prefix)


def test_oss_transport_preserves_proxy_ca_no_redirects_or_retry(monkeypatch):
    session = SafeSession()
    assert session.session.trust_env
    assert session.session.adapters["https://"].max_retries.total == 0
    seen = []

    def request(*args, **kwargs):
        seen.append(kwargs)
        raise requests.ConnectionError("https://secret-query.invalid/?signature=private")

    monkeypatch.setattr(session.session, "request", request)
    req = SimpleNamespace(
        method="PUT",
        url="https://tender-base.oss-cn-hangzhou.aliyuncs.com/path",
        data=b"data",
        params={},
        headers={},
        proxies=None,
    )
    with pytest.raises(DomainError) as caught:
        session.do_request(req, 30)
    assert len(seen) == 1 and not seen[0]["allow_redirects"]
    assert "signature" not in str(caught.value.details) + str(caught.value)
    assert "verify" not in seen[0]  # Requests默认验证并读取环境CA，禁止verify=False。


def test_oss_body_read_error_is_safe():
    def read(size):
        raise requests.ConnectionError("secret-signed-url")

    with pytest.raises(DomainError) as caught:
        SafeBody(SimpleNamespace(read=read)).read()
    assert "secret-signed-url" not in str(caught.value.details) + str(caught.value)
