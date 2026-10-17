"""独立HTTP边界验收。须有真实运行API和显式测试账号；不会启动模拟服务。"""

from __future__ import annotations

import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.request
import uuid


class ApiBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = [
            "QA_BASE_URL",
            "QA_ORIGIN",
            "QA_LOGIN_NAME",
            "QA_PASSWORD",
            "QA_APP_REVISION",
            "QA_CONTRACT_SHA256",
            "QA_CONFIG_SHA256",
            "QA_ADAPTER_MODE",
        ]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise unittest.SkipTest("未提供实际应用测试配置：" + ", ".join(missing))
        cls.base = os.environ["QA_BASE_URL"].rstrip("/")
        cls.origin = os.environ["QA_ORIGIN"]

    def setUp(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None, headers=None):
        values = {"Origin": self.origin, "Accept": "application/json"}
        if body is not None:
            values["Content-Type"] = "application/json"
        values.update(headers or {})
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=values,
            method=method,
        )
        try:
            response = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            payload = json.loads(raw) if raw else None
            return response.status, response.headers, payload

    def assert_error(self, status, body, expected):
        self.assertEqual(status, expected)
        self.assertIsInstance(body, dict)
        self.assertEqual(set(body), {"code", "message", "request_id", "retryable", "details"})
        self.assertTrue(body["code"])
        self.assertTrue(body["request_id"])
        self.assertIsInstance(body["retryable"], bool)

    def login(self):
        status, headers, body = self.request(
            "POST",
            "/auth/login",
            {"login_name": os.environ["QA_LOGIN_NAME"], "password": os.environ["QA_PASSWORD"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"member", "csrf_token", "expires_at"})
        self.assertTrue(body["csrf_token"])
        self.assertNotIn("password", body)
        cookie_headers = ";".join(headers.get_all("Set-Cookie") or []).lower()
        self.assertIn("httponly", cookie_headers)
        self.assertIn("samesite=", cookie_headers)
        return body["csrf_token"]

    def action_headers(self, token, key=None):
        return {"X-CSRF-Token": token, "Idempotency-Key": key or str(uuid.uuid4())}

    def test_anonymous_cannot_read_or_write_bids(self):
        status, _, body = self.request("GET", "/bids")
        self.assert_error(status, body, 401)
        status, _, body = self.request(
            "POST", "/bids", {"title": "匿名不得落库"}, headers=self.action_headers("invalid")
        )
        self.assert_error(status, body, 401)

    def test_session_cookie_matches_frozen_contract(self):
        self.login()
        self.assertTrue(any(cookie.name == "session" for cookie in self.cookies))

    def test_login_and_revocation_are_real(self):
        token = self.login()
        status, _, _ = self.request("GET", "/bids")
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", "/auth/logout", headers=self.action_headers(token))
        self.assertEqual(status, 204)
        status, _, body = self.request("GET", "/bids")
        self.assert_error(status, body, 401)

    def test_wrong_password_and_cross_origin_rejected(self):
        status, _, body = self.request(
            "POST",
            "/auth/login",
            {"login_name": os.environ["QA_LOGIN_NAME"], "password": "invalid-" + uuid.uuid4().hex},
        )
        self.assert_error(status, body, 401)
        status, _, body = self.request(
            "POST",
            "/auth/login",
            {"login_name": os.environ["QA_LOGIN_NAME"], "password": os.environ["QA_PASSWORD"]},
            headers={"Origin": "https://foreign-origin.example.test"},
        )
        self.assert_error(status, body, 403)

    def test_writes_require_csrf_even_with_valid_session(self):
        self.login()
        status, _, body = self.request(
            "POST",
            "/bids",
            {"title": "无CSRF不得创建"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assert_error(status, body, 403)
        status, _, body = self.request(
            "POST",
            "/bids",
            {"title": "错误CSRF不得创建"},
            headers=self.action_headers("foreign-token"),
        )
        self.assert_error(status, body, 403)

    def test_create_is_idempotent_and_key_cannot_change_request(self):
        token = self.login()
        key = str(uuid.uuid4())
        title = "独立QA-" + uuid.uuid4().hex
        status, _, first = self.request(
            "POST", "/bids", {"title": title}, headers=self.action_headers(token, key)
        )
        self.assertEqual(status, 201)
        bid_id = str(uuid.UUID(first["id"]))
        status, _, replay = self.request(
            "POST", "/bids", {"title": title}, headers=self.action_headers(token, key)
        )
        self.assertEqual(status, 201)
        self.assertEqual(replay, first)
        status, _, body = self.request(
            "POST", "/bids", {"title": title + "-changed"}, headers=self.action_headers(token, key)
        )
        self.assert_error(status, body, 409)
        status, _, current = self.request("GET", "/bids/" + bid_id)
        self.assertEqual(status, 200)
        self.assertEqual(current["title"], title)
        self.assertIsNone(current["current_review_id"])
        self.assertIsNone(current["current_body_id"])
        self.assertIn("next_action", current)

    def test_write_origin_and_idempotency_are_required(self):
        token = self.login()
        status, _, body = self.request(
            "POST",
            "/bids",
            {"title": "恶意站点"},
            headers=dict(self.action_headers(token), Origin="https://foreign-origin.example.test"),
        )
        self.assert_error(status, body, 403)
        status, _, body = self.request(
            "POST", "/bids", {"title": "缺少幂等键"}, headers={"X-CSRF-Token": token}
        )
        self.assertIn(status, (422, 428))
        self.assertTrue(body["request_id"])
