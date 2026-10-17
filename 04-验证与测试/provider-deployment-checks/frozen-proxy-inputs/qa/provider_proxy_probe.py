"""真实release HTTP/PG/Kafka/OSS同源传输独立边界与竞争；无模型请求。"""

from __future__ import annotations

import hashlib
import http.client
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from packages.domain.runtime import connection
from qa.helpers import Client, ensure, members, pdf_bytes, write_result


def bytes_call(client, url, data=None, *, method="PUT", overrides=None, anonymous=False):
    headers = {
        "Origin": os.environ["APP_ORIGIN"],
        "Content-Type": "application/pdf",
        "X-CSRF-Token": client.token or "",
    }
    headers.update(overrides or {})
    headers = {k: v for k, v in headers.items() if v is not None}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener() if anonymous else client.opener
    try:
        response = opener.open(request, timeout=35)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def cookie_header(client):
    return "; ".join(cookie.name + "=" + cookie.value for cookie in client.jar)


def raw_headers(client, url, length, extra=""):
    parsed = urlsplit(url)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    return (
        f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1:8802\r\n"
        f"Origin: {os.environ['APP_ORIGIN']}\r\nCookie: {cookie_header(client)}\r\n"
        f"X-CSRF-Token: {client.token}\r\nContent-Type: application/pdf\r\n"
        + (f"Content-Length: {length}\r\n" if length is not None else "")
        + extra
        + "Connection: close\r\n\r\n"
    ).encode()


def raw_exchange(client, url, length, body, extra=""):
    sock = socket.create_connection(("127.0.0.1", 8802), timeout=12)
    try:
        sock.sendall(raw_headers(client, url, length, extra) + body)
        response = http.client.HTTPResponse(sock)
        response.begin()
        return response.status, response.read()
    finally:
        sock.close()


class PausedPut:
    """100 Continue证明真实handler已校验授权并进入流读取，再制造并发。"""

    def __init__(self, client, url, data):
        self.data = data
        self.sock = socket.create_connection(("127.0.0.1", 8802), timeout=15)
        self.sock.sendall(raw_headers(client, url, len(data), "Expect: 100-continue\r\n"))
        received = b""
        while b"\r\n\r\n" not in received:
            received += self.sock.recv(4096)
        ensure(received.startswith(b"HTTP/1.1 100"), "实际HTTP 100 Continue应在合法流读取前返回")
        self.sock.sendall(data[:5])

    def finish(self):
        try:
            self.sock.sendall(self.data[5:])
            response = http.client.HTTPResponse(self.sock)
            response.begin()
            return response.status, response.read()
        finally:
            self.sock.close()


def main():
    out = Path(os.environ["QA_RESULT_PATH"])
    result = dict(
        scope="真实release HTTP/PG/Kafka/OSS同源传输独立合法/异常/禁止/竞争；非A1模型评审",
        passed=False,
        checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
        model_requests=0,
    )
    stage = "初始化"
    try:
        ensure(
            Path(os.environ["WORKBENCH_ENV_FILE"]).name == "external-release.env",
            "只允许新的external-release profile",
        )
        ensure(urlsplit(os.environ["API_DATABASE_DSN"]).port == 55441, "只允许独立release PG")
        ensure(os.environ["APP_ORIGIN"] == "http://127.0.0.1:5175", "只允许external浏览器Origin")
        client = Client(members()[0])
        other = Client(members()[1])
        same_member_new_session = Client(members()[0])
        bid = client.bid()
        result["bid_id"] = bid
        data = pdf_bytes("Independent real proxy municipal body and immutable evidence fixture.")
        digest = hashlib.sha256(data).hexdigest()

        def grant(*, name="代理合成资料.pdf", declared=None, size=None, headers=None):
            batch = client.batch(bid)
            status, response_headers, body = client.call(
                "POST",
                f"/bids/{bid}/uploads",
                dict(
                    batch_id=batch,
                    role="body",
                    filename=name,
                    byte_size=size or len(data),
                    declared_sha256=declared or digest,
                    target={"kind": "new_file"},
                ),
                headers=headers,
            )
            return status, response_headers, body, batch

        def row(upload):
            with connection() as c:
                return c.execute(
                    "SELECT u.state,u.staging_key,u.verified_version_id,u.error_code,t.revision,t.aborted_at FROM uploads u JOIN upload_transfers t ON t.upload_id=u.id WHERE u.id=%s",
                    (upload,),
                ).fetchone()

        def accepted_grant(**kwargs):
            status, headers, g, batch = grant(**kwargs)
            ensure(status == 200, "合法新授权应成功")
            parts = urlsplit(g["upload_url"])
            ensure(
                parts.scheme == "http"
                and parts.netloc == "127.0.0.1:5175"
                and parts.path == f"/api/v1/object-transfers/uploads/{g['upload_id']}"
                and set(parse_qs(parts.query)) == {"ticket"},
                "绝对URL仅可信同源短票据，无云签名",
            )
            return headers, g, batch

        stage = "授权与CSRF"
        _, g, _ = accepted_grant(
            headers={
                "Host": "untrusted.invalid",
                "X-Forwarded-Host": "untrusted.invalid",
                "X-Forwarded-Proto": "https",
            }
        )
        url = g["upload_url"]
        upload = g["upload_id"]
        before = row(upload)
        observations = []
        for label, caller, overrides, anonymous, expected in [
            ("anonymous", client, {}, True, 401),
            ("missing_csrf", client, {"X-CSRF-Token": None}, False, 403),
            ("wrong_csrf", client, {"X-CSRF-Token": "synthetic-invalid"}, False, 403),
            ("foreign_origin", client, {"Origin": "https://untrusted.invalid"}, False, 403),
            ("other_member", other, {}, False, 403),
            ("same_member_other_session", same_member_new_session, {}, False, 403),
        ]:
            status, _, _ = bytes_call(caller, url, data, overrides=overrides, anonymous=anonymous)
            ensure(status == expected, label + "应拒绝")
            observations.append(dict(case=label, status=status))
        ensure(row(upload) == before, "拒绝路径不得改变上传状态/定位")
        result["authorization"] = observations
        result["checks"].append("真实Cookie/CSRF/Origin与同成员不同session均限制短票据")

        stage = "票据对象方法边界"
        _, other_grant, _ = accepted_grant()
        wrong_url = url.replace(upload, other_grant["upload_id"])
        status, _, _ = bytes_call(client, wrong_url, data)
        ensure(status == 403, "同票据换upload对象拒绝")
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        ticket = query["ticket"][0]
        query["ticket"] = [ticket[:-1] + ("0" if ticket[-1] != "0" else "1")]
        modified = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), "")
        )
        status, _, _ = bytes_call(client, modified, data)
        ensure(status == 403, "篡改票据拒绝")
        status, _, _ = bytes_call(client, url, method="GET")
        ensure(status == 405, "上传票据不能当GET使用")
        ensure(row(upload) == before, "对象/方法错误不写原件")
        result["checks"].append("可信Origin不取Host/Forwarded；token完整性、upload对象与方法绑定")

        stage = "大小媒体与真实流"
        for label, headers, body, expected in [
            ("wrong_length", {"Content-Length": str(len(data) + 1)}, data, 422),
            ("media", {"Content-Type": "application/octet-stream"}, data, 415),
            ("encoding", {"Content-Encoding": "gzip"}, data, 415),
            ("not_pdf", {}, b"NOTPDF" + data[6:], 415),
        ]:
            status, _, _ = bytes_call(client, url, body, overrides=headers)
            ensure(status == expected, label + "必须拒绝")
        status, _ = raw_exchange(client, url, None, b"0\r\n\r\n", "Transfer-Encoding: chunked\r\n")
        ensure(status == 411, "无准确Content-Length拒绝")
        max_bytes = min(
            int(os.environ.get("OBJECT_TRANSFER_MAX_BYTES", "104857600")),
            int(os.environ.get("MAX_UPLOAD_BYTES", "104857600")),
        )
        status, _, _, _ = grant(size=max_bytes + 1)
        ensure(status == 413, "超限声明在发大实体前拒绝")
        # 长度与chunked互相矛盾时，HTTP解析器可先400；若进入handler则必须413。
        chunk = data + b"X"
        wire = f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
        status, _ = raw_exchange(client, url, len(data), wire, "Transfer-Encoding: chunked\r\n")
        ensure(status in [400, 413], "超额实体不能变成功")
        result["oversized_wire_status"] = status
        short = PausedPut(client, url, data)
        short.sock.shutdown(socket.SHUT_WR)
        try:
            reply = http.client.HTTPResponse(short.sock)
            reply.begin()
            short_status = reply.status
            reply.read()
        except (http.client.HTTPException, OSError):
            short_status = None
        finally:
            short.sock.close()
        time.sleep(0.2)
        ensure(row(upload) == before, "短传/超量/非法格式均不公布key或核验成功")
        result["short_connection_status"] = short_status
        result["checks"].append(
            "真实HTTP准确长度/Content-Type/编码/PDF魔数/上限/超额实体和半连接短传拒绝且无定位副作用"
        )

        stage = "成功上传与旧票据"
        status, _, _ = bytes_call(client, url, data)
        ensure(status == 200, "合法同源传输成功")
        written = row(upload)
        ensure(
            written["staging_key"] != before["staging_key"] and written["state"] == "authorized",
            "只公布独立暂存key，不自动complete",
        )
        status, _, _ = bytes_call(client, url, data)
        ensure(status == 403, "已换key的旧票据不能再覆盖")
        status, _, parts = client.call("GET", f"/uploads/{upload}/parts")
        ensure(status == 200 and len(parts["completed_parts"]) == 1, "parts可恢复已真实存储字节")
        status, _, accepted = client.call(
            "POST", f"/uploads/{upload}/complete", dict(parts=[], declared_sha256=digest)
        )
        ensure(status == 202, "显式complete才入真实Kafka核验")
        _, verified = client.poll(
            f"/uploads/{upload}",
            lambda x: x["state"] in ["verified", "quarantined"],
            "真实OSS SHA核验",
            seconds=70,
        )
        ensure(verified["state"] == "verified", "真实Worker只对实际SHA正确原件verified")
        result.update(upload_id=upload, document_version_id=verified["document_version_id"])
        result["checks"].append(
            "同源真实OSS暂存→旧ticket拒绝→parts恢复→显式complete→Kafka实际SHA核验"
        )

        stage = "真实下载与会话"
        status, _, download = client.call(
            "GET", f"/artifacts/{verified['document_version_id']}/download"
        )
        ensure(status == 200, "真实私有原件可取授权")
        download_url = download["download_url"]
        ensure(urlsplit(download_url).netloc == "127.0.0.1:5175", "下载仍同源，无云签名")
        status, headers, original = bytes_call(client, download_url, method="GET")
        ensure(status == 200 and hashlib.sha256(original).hexdigest() == digest, "下载实际SHA一致")
        ensure(
            "filename*=UTF-8''" in headers["Content-Disposition"]
            and "%E4%BB%A3" in headers["Content-Disposition"],
            "中文名RFC5987且不注入header",
        )
        status, _, full = bytes_call(
            client, download_url, method="GET", overrides={"Range": "bytes=0-4"}
        )
        ensure(status == 200 and full == data, "Range明确全量200，不伪造206")
        for caller, anonymous in [(client, True), (other, False), (same_member_new_session, False)]:
            status, _, _ = bytes_call(caller, download_url, method="GET", anonymous=anonymous)
            ensure(status in ([401] if anonymous else [403]), "下载必须同session授权")
        logout_client = Client(members()[0])
        _, _, logout_grant = logout_client.call(
            "GET", f"/artifacts/{verified['document_version_id']}/download"
        )
        logout_client.call("POST", "/auth/logout")
        status, _, _ = bytes_call(logout_client, logout_grant["download_url"], method="GET")
        ensure(status == 401, "logout后旧授权也无效")
        result["download_sha256"] = digest
        result["checks"].append("真实中文名下载SHA/Range全量、匿名/跨session/会话撤销拒绝")

        stage = "服务端摘要负例"
        _, bad, _ = accepted_grant()
        wrong = data[:-1] + b"X"
        status, _, _ = bytes_call(client, bad["upload_url"], wrong)
        ensure(status == 200, "上传阶段不谎称已经核验摘要")
        client.call(
            "POST", f"/uploads/{bad['upload_id']}/complete", dict(parts=[], declared_sha256=digest)
        )
        _, bad_view = client.poll(
            f"/uploads/{bad['upload_id']}",
            lambda x: x["state"] in ["verified", "quarantined"],
            "错误SHA隔离",
            seconds=70,
        )
        ensure(
            bad_view["state"] == "quarantined"
            and bad_view["error_code"] == "UPLOAD_DIGEST_MISMATCH"
            and bad_view["document_version_id"] is None,
            "同大小错误SHA不得形成有效原件",
        )
        result["checks"].append("同大小不同PDF实体经真实Worker复算SHA隔离，无有效版本")

        stage = "短授权到期"
        _, expiring, _ = accepted_grant()
        exp_id = expiring["upload_id"]
        with connection() as c:
            c.execute(
                "UPDATE uploads SET expires_at=now()+interval '4 seconds' WHERE id=%s", (exp_id,)
            )
        _, expires_headers, _ = client.call("GET", f"/uploads/{exp_id}")
        status, _, short_grant = client.call(
            "POST",
            f"/uploads/{exp_id}/authorizations",
            {"part_numbers": [1]},
            headers={"If-Match": expires_headers["ETag"]},
        )
        ensure(status == 200, "到期前可发被上传会话限制的短授权")
        time.sleep(4.1)
        status, _, _ = bytes_call(client, short_grant["upload_url"], data)
        ensure(status == 410, "真实服务短授权自然到期后拒绝")
        ensure(
            row(exp_id)["state"] == "authorized" and row(exp_id)["verified_version_id"] is None,
            "过期请求无有效原件",
        )
        result["checks"].append("隔离PG将本次上传有效期设4秒，服务端刷新真实短授权并自然到期410")

        stage = "流与refresh/complete/abort竞争"
        races = []
        for command in ["authorizations", "complete", "abort"]:
            _, racing, _ = accepted_grant()
            rid = racing["upload_id"]
            old = row(rid)
            paused = PausedPut(client, racing["upload_url"], data)
            try:
                _, h, _ = client.call("GET", f"/uploads/{rid}")
                payload = (
                    {"part_numbers": [1]}
                    if command == "authorizations"
                    else (
                        {"parts": [], "declared_sha256": digest} if command == "complete" else None
                    )
                )
                expected = 202 if command == "complete" else 200
                status, _, _ = client.call(
                    "POST", f"/uploads/{rid}/{command}", payload, headers={"If-Match": h["ETag"]}
                )
                ensure(status == expected, "并发命令应真实生效：" + command)
                stream_status, _ = paused.finish()
            finally:
                paused.sock.close()
            current = row(rid)
            ensure(
                stream_status in [403, 409] and current["staging_key"] == old["staging_key"],
                command + "胜出后旧流不得换key",
            )
            ensure(current["verified_version_id"] is None, command + "竞争不能制造有效原件")
            races.append(
                dict(
                    command=command,
                    status=status,
                    old_stream_status=stream_status,
                    key_unchanged=True,
                )
            )
        result["races"] = races
        result["checks"].append(
            "100 Continue实际流读取屏障下refresh/complete/abort分别胜出，旧流拒绝且不换key"
        )
        result["passed"] = True
    except Exception as error:
        result["failure"] = dict(
            stage=stage,
            type=type(error).__name__,
            message=str(error)
            if isinstance(error, AssertionError)
            else "实际同源传输验收未完成；不输出URL/Cookie/凭据",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_result(out, result)
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
