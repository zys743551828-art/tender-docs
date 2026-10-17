"""独立验收辅助：合成资料、实际HTTP与脱敏记录。"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure(value, label):
    if not value:
        raise AssertionError(label)


def key():
    return str(uuid.uuid4())


def members():
    path = ROOT / ".local/qa-test-members.json"
    ensure(path.stat().st_mode & 0o077 == 0, "临时凭据权限必须600")
    return json.loads(path.read_text())


class Client:
    def __init__(self, member=None):
        self.base = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8801/api/v1").rstrip("/")
        self.origin = os.environ["APP_ORIGIN"]
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.token = None
        if member is not None:
            status, _, body = self.call(
                "POST",
                "/auth/login",
                dict(login_name=member["login"], password=member["password"]),
                action=False,
            )
            ensure(status == 200, "真实成员登录应成功")
            self.token = body["csrf_token"]

    def call(self, method, path, body=None, headers=None, action=True, action_key=None):
        outgoing = dict(Origin=self.origin, Accept="application/json")
        if body is not None:
            outgoing["Content-Type"] = "application/json"
        if action and method in ["POST", "PATCH", "DELETE"]:
            outgoing.update(
                {"X-CSRF-Token": self.token or "", "Idempotency-Key": action_key or key()}
            )
        outgoing.update(headers or {})
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=outgoing,
            method=method,
        )
        try:
            response = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            content = response.headers.get("Content-Type", "")
            payload = (
                json.loads(raw)
                if raw and "json" in content
                else (raw.decode(errors="replace")[:100] if raw else None)
            )
            validate_response(method, path, response.status, payload)
            return response.status, response.headers, payload

    def bid(self, title=None):
        status, _, body = self.call("POST", "/bids", dict(title=title or "独立QA-" + key()))
        ensure(status == 201, "建项目返回201")
        return body["id"]

    def batch(self, bid):
        status, _, body = self.call("POST", f"/bids/{bid}/upload-batches", {})
        ensure(status == 201, "建上传批次返回201")
        return body["id"]

    def poll(self, path, predicate, label, seconds=35):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status, headers, body = self.call("GET", path)
            ensure(status == 200, label + "查询成功")
            if predicate(body):
                return headers, body
            time.sleep(0.15)
        raise AssertionError(label + "在约定测试截止内完成")


def pdf_bytes(text="Synthetic municipal tender body with more than twenty readable characters."):
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 760 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(data)


def upload(client, bid, batch, data, role="body", target=None, put_data=None, complete=True):
    digest = hashlib.sha256(data).hexdigest()
    status, headers, grant = client.call(
        "POST",
        f"/bids/{bid}/uploads",
        dict(
            batch_id=batch,
            role=role,
            filename="synthetic.pdf",
            byte_size=len(data),
            declared_sha256=digest,
            target=target or dict(kind="new_file"),
        ),
    )
    ensure(status == 200, "创建真实上传授权")
    request = urllib.request.Request(
        grant["upload_url"],
        data=data if put_data is None else put_data,
        method="PUT",
        headers={"Content-Type": "application/pdf"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        ensure(response.status in [200, 204], "签名S3 PUT成功")
    if not complete:
        return grant, None
    status, _, accepted = client.call(
        "POST",
        f"/uploads/{grant['upload_id']}/complete",
        dict(parts=[], declared_sha256=digest),
        headers={"If-Match": headers["ETag"]},
    )
    ensure(status == 202, "完成上传真实异步核验受理")
    return grant, accepted


def write_result(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")


def three_page_pdf():
    """一页文字、一页空白、一页真实栅格图；独立于产品解析结果。"""
    import zlib

    text = b"BT /F1 12 Tf 50 760 Td (Three page QA: readable municipal construction evidence on first physical page.) Tj ET"
    image = zlib.compress(bytes([0, 0, 0, 255, 255, 255] * 8))
    image_content = b"q 180 0 0 180 100 400 cm /Im1 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R 8 0 R] /Count 3 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 7 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im1 10 0 R >> >> /Contents 9 0 R >>",
        b"<< /Length "
        + str(len(image_content)).encode()
        + b" >>\nstream\n"
        + image_content
        + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 4 /Height 4 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
        + str(len(image)).encode()
        + b" >>\nstream\n"
        + image
        + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(data)


_SCHEMA_CACHE = None


def validate_response(method, path, status, payload):
    """独立按已审目标响应Schema核验真实返回；不使用应用序列化函数。"""
    global _SCHEMA_CACHE
    import re

    from jsonschema import Draft202012Validator, FormatChecker

    if _SCHEMA_CACHE is None:
        protocol = json.loads(
            (ROOT / "packages/contracts/schemas/protocol.schema.json").read_text()
        )
        api = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
        _SCHEMA_CACHE = (protocol, api)
    schema, api = _SCHEMA_CACHE
    name = "Error" if status >= 400 else None
    for route, operations in api["paths"].items():
        pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", route) + "$"
        if re.match(pattern, path.split("?")[0]) and method.lower() in operations:
            content = (
                operations[method.lower()]["responses"].get(str(status), {}).get("content", {})
            )
            ref = content.get("application/json", {}).get("schema", {}).get("$ref")
            if ref:
                name = ref.split("/")[-1]
            break
    if status == 204:
        return
    if name:
        validator = Draft202012Validator(
            dict(schema, **{"$ref": "#/$defs/" + name}), format_checker=FormatChecker()
        )
        errors = list(validator.iter_errors(payload))
        ensure(
            not errors,
            "实际响应应符合已审Schema "
            + name
            + ("；路径 " + str(list(errors[0].path)) if errors else ""),
        )
