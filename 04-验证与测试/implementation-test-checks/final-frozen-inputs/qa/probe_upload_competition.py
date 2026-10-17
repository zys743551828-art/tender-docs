"""真实上传版本竞争和显式重绑定；只使用独立QA库与合成字节。"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from packages.domain.runtime import connection
from qa.helpers import Client, ensure, key, members, pdf_bytes, upload, write_result


def main():
    record = dict(
        scope="实际HTTP/S3/Kafka上传head竞争、重绑定及禁止路径；不含multipart",
        passed=False,
        checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    stage = "初始化"
    try:
        a, b = [Client(x) for x in members()]
        bid = a.bid()
        record["bid_id"] = bid

        def prepare(client, text, target=None, role="body"):
            batch = client.batch(bid)
            grant, _ = upload(client, bid, batch, pdf_bytes(text), target=target, role=role)
            _, view = client.poll(
                "/uploads/" + grant["upload_id"],
                lambda x: x["state"] in ["verified", "quarantined"],
                "核验竞争稿",
            )
            ensure(view["state"] == "verified", "竞争稿真实核验成功")
            return dict(batch=batch, upload=grant["upload_id"], view=view)

        def commit(client, item, etag=None, action_key=None):
            if etag is None:
                _, h, _ = client.call("GET", "/bids/" + bid)
                etag = h["ETag"]
            body = dict(
                accepted_upload_ids=[item["upload"]],
                accepted_binding_ids=[item["view"]["binding_id"]],
                explicit_partial_acceptance=False,
            )
            return client.call(
                "POST",
                f"/bids/{bid}/upload-batches/{item['batch']}/commit",
                body,
                headers={"If-Match": etag},
                action_key=action_key,
            )

        def material():
            status, _, m = a.call("GET", f"/bids/{bid}/materials")
            ensure(status == 200, "材料可读")
            return next(x for x in m["documents"] if x["role"] == "body")

        first = prepare(a, "First synthetic municipal body. Keep immutable physical PDF evidence.")
        status, _, body = commit(a, first)
        ensure(status == 202, "首次入库")
        a.poll(
            "/operations/" + body["operation"]["id"],
            lambda x: x["state"] in ["succeeded", "failed"],
            "首次解析",
        )
        doc = material()
        ensure(
            doc["file_revision"] == 1 and doc["file_head_revision"] == 2,
            "首版本序号1与head修订2分离",
        )
        file_id = doc["file_id"]
        record["file_id"] = file_id
        record["checks"].append("首版本序号和head修订的真实区分")
        target = dict(kind="new_version", file_id=file_id, expected_file_revision=2)
        left, right = (
            prepare(
                a, "Candidate A municipal construction body revised with unique wording.", target
            ),
            prepare(b, "Candidate B municipal construction body independently revised.", target),
        )
        _, h, _ = a.call("GET", "/bids/" + bid)
        etag = h["ETag"]
        stage = "并发head"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(commit, a, left, etag), pool.submit(commit, b, right, etag)]
            outcomes = [f.result() for f in futures]
        ensure(sorted(x[0] for x in outcomes) == [202, 412], "两人相同head并发只能一个推进")
        winner, loser = (left, right) if outcomes[0][0] == 202 else (right, left)
        client = a if loser is left else b
        status, _, error = commit(client, loser)
        ensure(
            status == 412 and error["code"] == "FILE_REVISION_CONFLICT",
            "刷新bid仍不得静默刷新文件expected revision",
        )
        _, uh, uv = client.call("GET", "/uploads/" + loser["upload"])
        ensure(
            uv["state"] == "verified"
            and uv["document_version_id"] == loser["view"]["document_version_id"],
            "冲突保留已核验原字节与版本",
        )
        record["checks"].append("真实并发仅一人推进，冲突保留verified字节并再次拒绝旧文件revision")
        stage = "禁止重绑"
        other_bid = a.bid()
        other_batch = a.batch(other_bid)
        status, _, foreign = a.call(
            "POST",
            f"/bids/{other_bid}/uploads",
            dict(
                batch_id=other_batch,
                role="body",
                filename="foreign.pdf",
                byte_size=1,
                declared_sha256="a" * 64,
                target=dict(kind="new_file"),
            ),
        )
        ensure(status == 200, "构造另一bid的真实逻辑文件")
        status, _, error = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            dict(
                target=dict(
                    kind="new_version", file_id=foreign["file_id"], expected_file_revision=1
                ),
                reason="禁止跨bid",
            ),
            headers={"If-Match": uh["ETag"]},
        )
        record["cross_bid_status"] = status
        ensure(
            status == 409 and error["code"] == "UPLOAD_TARGET_MISMATCH", "跨bid重绑明确409目标冲突"
        )
        role_batch = a.batch(bid)
        status, _, role_grant = a.call(
            "POST",
            f"/bids/{bid}/uploads",
            dict(
                batch_id=role_batch,
                role="tender",
                filename="role.pdf",
                byte_size=1,
                declared_sha256="a" * 64,
                target=dict(kind="new_file"),
            ),
        )
        ensure(status == 200, "构造不同角色文件")
        status, _, error = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            dict(
                target=dict(
                    kind="new_version", file_id=role_grant["file_id"], expected_file_revision=1
                ),
                reason="禁止不同角色",
            ),
            headers={"If-Match": uh["ETag"]},
        )
        ensure(status == 409 and error["code"] == "UPLOAD_TARGET_MISMATCH", "角色不同重绑拒绝")
        with connection() as c:
            expiry = c.execute(
                "SELECT verified_retain_until FROM uploads WHERE id=%s", (loser["upload"],)
            ).fetchone()["verified_retain_until"]
            c.execute(
                "UPDATE uploads SET verified_retain_until=now()-interval '1 second' WHERE id=%s",
                (loser["upload"],),
            )
        try:
            status, _, error = client.call(
                "POST",
                f"/uploads/{loser['upload']}/bindings",
                dict(
                    target=dict(kind="new_version", file_id=file_id, expected_file_revision=3),
                    reason="禁止已过期",
                ),
                headers={"If-Match": uh["ETag"]},
            )
            ensure(
                status == 410 and error["code"] == "VERIFIED_BYTES_EXPIRED",
                "真实服务拒绝已过期核验字节",
            )
        finally:
            with connection() as c:
                c.execute(
                    "UPDATE uploads SET verified_retain_until=%s WHERE id=%s",
                    (expiry, loser["upload"]),
                )
        record["checks"].append("跨bid/资料角色/保留期过期禁止路径")
        stage = "显式重绑"
        action_key = key()
        body = dict(
            target=dict(kind="new_version", file_id=file_id, expected_file_revision=3),
            reason="已看最新head，显式继续",
        )
        status, bh, binding = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            body,
            headers={"If-Match": uh["ETag"]},
            action_key=action_key,
        )
        ensure(
            status == 200 and binding["previous_binding_id"] == uv["binding_id"], "重绑保留原选择链"
        )
        status, rh, replay = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            body,
            headers={"If-Match": uh["ETag"]},
            action_key=action_key,
        )
        ensure(
            status == 200 and replay == binding and rh.get("Idempotent-Replayed") == "true",
            "同key仅同binding",
        )
        loser["view"]["binding_id"] = binding["id"]
        record["checks"].append("不重传字节重绑最新head，原IfMatch同key原样重放")
        stage = "重绑后二次竞争"
        next_item = prepare(
            a,
            "Third municipal body branch moves head after another member reconfirms.",
            dict(kind="new_version", file_id=file_id, expected_file_revision=3),
        )
        status, _, _ = commit(a, next_item)
        ensure(status == 202, "另一人再次推进head")
        status, _, error = commit(client, loser)
        ensure(
            status == 412 and error["code"] == "FILE_REVISION_CONFLICT", "已重绑后仍执行第二次CAS"
        )
        _, uh, _ = client.call("GET", "/uploads/" + loser["upload"])
        status, _, binding = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            dict(target=dict(kind="new_file"), reason="显式另建文件保留分支"),
            headers={"If-Match": uh["ETag"]},
        )
        ensure(status == 200 and binding["file_id"] != file_id, "另建文件为显式新目标")
        loser["view"]["binding_id"] = binding["id"]
        status, _, _ = commit(client, loser)
        ensure(status == 202, "相同verified字节另建文件可入库")
        record["checks"].append("二次竞争仍CAS，显式另建文件复用同核验字节")
        _, uh, _ = client.call("GET", "/uploads/" + loser["upload"])
        status, _, error = client.call(
            "POST",
            f"/uploads/{loser['upload']}/bindings",
            dict(target=dict(kind="new_file"), reason="禁止提交后重绑"),
            headers={"If-Match": uh["ETag"]},
        )
        ensure(status == 409 and error["code"] == "UPLOAD_NOT_REBINDABLE", "已入库对象不得再重绑")
        record["checks"].append("已提交对象不能重绑且旧版本/原件仍保留")
        record["passed"] = True
    except Exception as e:
        record["failure"] = dict(
            stage=stage,
            type=type(e).__name__,
            message=str(e) if isinstance(e, AssertionError) else "执行异常，未输出私有URL或凭据",
        )
    record["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_result(os.environ.get("QA_RESULT_PATH", "qa/upload-competition-result.json"), record)
    print(
        dict(passed=record["passed"], checks=len(record["checks"]), failure=record.get("failure"))
    )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
