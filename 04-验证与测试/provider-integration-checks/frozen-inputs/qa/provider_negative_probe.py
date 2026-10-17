"""无外部请求的独立适配负例；显式MockTransport不冒充真实模型效果。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from packages.domain.providers.deepseek import DeepSeekClient, ProviderError
from packages.domain.providers.oss import OssClient
from packages.domain.runtime import DomainError
from qa.provider_preflight import write

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}
MESSAGES = [{"role": "user", "content": "Synthetic test JSON only"}]


def envelope(content='{"ok":true}', finish="stop", model="deepseek-flash"):
    return {
        "id": "qa-response-id",
        "model": model,
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
    }


class ProviderNegativeTests(unittest.TestCase):
    def client(self, handler, **kwargs):
        return DeepSeekClient(
            api_key="qa-synthetic-credential", transport=httpx.MockTransport(handler), **kwargs
        )

    def test_success_does_not_qualify_formal_score(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json=envelope())

        answer = self.client(handler).generate(messages=MESSAGES, max_tokens=32, json_schema=SCHEMA)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["stream"])
        self.assertEqual(calls[0]["thinking"], {"type": "disabled"})
        self.assertEqual(answer.data, {"ok": True})
        self.assertFalse(answer.formal_scoring_eligible)

    def test_http_failure_never_follows_redirect_or_retries(self):
        for status in [401, 403, 429, 500, 302]:
            with self.subTest(status=status):
                calls = []

                def handler(request, calls=calls, status=status):
                    calls.append(request.url.host)
                    return httpx.Response(
                        status,
                        headers={
                            "location": "https://untrusted.invalid",
                            "x-request-id": "qa-http",
                        },
                        text="private-source-marker",
                    )

                with self.assertRaises(ProviderError) as caught:
                    self.client(handler).generate(messages=MESSAGES)
                self.assertEqual(calls, ["api.deepseek.com"])
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(caught.exception.details["external_attempts"], 1)
                self.assertNotIn("private-source-marker", str(caught.exception.details))
                self.assertNotIn("qa-synthetic-credential", str(caught.exception))

    def test_timeout_stays_unknown_and_one_attempt(self):
        calls = []

        def handler(request):
            calls.append(True)
            raise httpx.ReadTimeout("private-source-marker", request=request)

        with self.assertRaises(ProviderError) as caught:
            self.client(handler).generate(messages=MESSAGES)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.code, "MODEL_TIMEOUT")
        self.assertEqual(caught.exception.details["billing_state"], "unknown")
        self.assertFalse(caught.exception.retryable)

    def test_incomplete_output_preserves_usage_without_repair_call(self):
        calls = []

        def handler(request):
            calls.append(True)
            return httpx.Response(200, json=envelope(finish="length"))

        with self.assertRaises(ProviderError) as caught:
            self.client(handler).generate(messages=MESSAGES, json_schema=SCHEMA)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.code, "MODEL_OUTPUT_INCOMPLETE")
        self.assertEqual(caught.exception.details["usage"]["total_tokens"], 10)
        self.assertEqual(caught.exception.details["billing_state"], "usage_reported")

    def test_untrusted_json_is_rejected(self):
        for content in [
            '{"ok":true,"ok":false}',
            '{"ok":NaN}',
            '{"ok":false}',
            '{"ok":true,"extra":1}',
            "[]",
        ]:
            with self.subTest(content=content):
                calls = []

                def handler(request, calls=calls, content=content):
                    calls.append(True)
                    return httpx.Response(200, json=envelope(content=content))

                with self.assertRaises(ProviderError) as caught:
                    self.client(handler).generate(messages=MESSAGES, json_schema=SCHEMA)
                self.assertEqual(len(calls), 1)
                self.assertEqual(caught.exception.code, "MODEL_OUTPUT_INVALID")

    def test_other_model_cannot_inherit_identity(self):
        with self.assertRaises(ProviderError) as caught:
            self.client(
                lambda _: httpx.Response(200, json=envelope(model="deepseek-v4-pro"))
            ).generate(messages=MESSAGES)
        self.assertEqual(caught.exception.code, "MODEL_INVALID_RESPONSE")

    def test_remote_schema_rejected_before_request(self):
        calls = []
        with self.assertRaises(DomainError):
            self.client(lambda _: calls.append(True)).generate(
                messages=MESSAGES, json_schema={"$ref": "https://untrusted.invalid/schema"}
            )
        self.assertEqual(calls, [])

    def test_dangling_or_unsupported_schema_reference_is_unpaid(self):
        for schema in [
            {"$ref": "#/$defs/missing"},
            {"$defs": {"a": {"$anchor": "a", "type": "object"}}, "$ref": "#a"},
        ]:
            with self.subTest(schema=schema):
                calls = []
                with self.assertRaises(DomainError) as caught:
                    self.client(lambda _, calls=calls: calls.append(True)).generate(
                        messages=MESSAGES, json_schema=schema
                    )
                self.assertEqual(caught.exception.code, "MODEL_REQUEST_INVALID")
                self.assertEqual(calls, [])

    def test_valid_local_schema_pointer_keeps_single_request(self):
        calls = []

        def handler(request):
            calls.append(True)
            return httpx.Response(200, json=envelope())

        schema = {
            "type": "object",
            "properties": {"ok": {"$ref": "#/$defs/yes"}},
            "$defs": {"yes": {"const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        answer = self.client(handler).generate(messages=MESSAGES, json_schema=schema)
        self.assertEqual(answer.data, {"ok": True})
        self.assertEqual(len(calls), 1)

    def test_total_deadline_covers_drip_read(self):
        class Drip(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    yield b" "

        calls = []

        def handler(request):
            calls.append(True)
            return httpx.Response(200, stream=Drip())

        start = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            self.client(handler, total_timeout=0.08).generate(messages=MESSAGES)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(caught.exception.code, "MODEL_TIMEOUT")
        self.assertEqual(len(calls), 1)

    def test_model_endpoint_is_official_https_only(self):
        for address in [
            "http://api.deepseek.com/v1",
            "https://untrusted.invalid/v1",
            "https://api.deepseek.com.evil.invalid",
        ]:
            with self.subTest(address=address), self.assertRaises(DomainError):
                DeepSeekClient(api_key="qa-synthetic", base_url=address)

    def test_oss_invalid_locator_rejected_before_native(self):
        class NoNetwork:
            def __getattr__(self, name):
                raise AssertionError("禁止调用native：" + name)

        client = OssClient(NoNetwork())
        for bucket, key in [
            ("other", "workbench-checks/a"),
            ("tender-base", "/absolute"),
            ("tender-base", "workbench-checks/../foreign"),
            ("tender-base", "bad\x00key"),
        ]:
            with self.subTest(bucket=bucket, key=key), self.assertRaises(DomainError):
                client.get_object(Bucket=bucket, Key=key)

    def test_oss_invalid_grants_fail_without_network(self):
        client = OssClient(object())
        for method, params, ttl in [
            ("get_object", {"Bucket": "tender-base", "Key": "workbench-checks/a"}, 901),
            ("delete_object", {"Bucket": "tender-base", "Key": "workbench-checks/a"}, 60),
            ("put_object", {"Bucket": "tender-base", "Key": "blobs/a"}, 60),
            (
                "get_object",
                {
                    "Bucket": "tender-base",
                    "Key": "workbench-checks/a",
                    "Endpoint": "https://untrusted.invalid",
                },
                60,
            ),
        ]:
            with self.subTest(method=method, ttl=ttl), self.assertRaises(DomainError):
                client.generate_presigned_url(method, Params=params, ExpiresIn=ttl)

    def test_oss_untrusted_endpoint_configuration_rejected(self):
        with patch.dict(os.environ, {"OBJECT_STORE_ENDPOINT": "https://untrusted.invalid"}):
            with self.assertRaises(DomainError):
                OssClient.from_env()


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProviderNegativeTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    record = dict(
        scope="独立显式MockTransport/无网络负例，不代表真实模型效果",
        passed=result.wasSuccessful(),
        tests=result.testsRun,
        failures=len(result.failures),
        errors=len(result.errors),
        live_chat_requests=0,
        external_object_requests=0,
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    write(Path(os.environ["QA_PROVIDER_OUTPUT"]), record)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
